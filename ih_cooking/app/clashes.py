"""Meal-vs-calendar logic (port of the house script, now pure and testable).
compute(today, days, plan, events, cfg, recipes) -> dict. No network, no clock, no files.
plan   : {"mon": "Meal name", ... "sun": ...}   (missing day = no meal)
events : {calendar_entity: [ {start, end, summary, description, location}, ... ]}   start/end = ISO text or {"dateTime"|"date": ...}
cfg    : see DEFAULT_CONFIG (people, shared calendars, words, windows, schedules, closures)
recipes: {meal name (lower case): {"serves": n, "meal_time": "HH:MM"}}"""
import copy, re
from datetime import datetime, date, timedelta

DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

# Example defaults only. The owner supplies the real values in the add-on options (clash_config).
DEFAULT_CONFIG = {
    'timezone': 'Europe/London',
    'people': [
        {'name': 'Person A', 'calendar': 'calendar.person_a', 'pattern': r'\bperson a\b'},
        {'name': 'Person B', 'calendar': 'calendar.person_b', 'pattern': r'\bperson b\b'},
        {'name': 'Person C', 'calendar': 'calendar.person_c', 'pattern': r'\b(person c|pc)\b',
         'schedule': {'0': [['13:35', '15:05']], '2': [['09:00', '10:55']]},   # weekday (0=Mon) -> [start, end] lessons or shifts
         'travel_minutes': 45,
         'closures': [{'first': '2026-12-21', 'last': '2027-01-05', 'label': 'Holidays', 'sure': True}]},
    ],
    'shared_calendars': ['calendar.shared'],
    'away_words': r'away|stay|trip|abroad|hotel|weekend|camp|visit|cruise|flight|hospital',
    'ignore_words': r'birthday|anniversary|bin\b|collection|reminder|renewal|delivery|expires|due\b',
    'evening_window': ['17:30', '19:30'],
    'lunch_window': ['12:30', '14:00'],
    'lunch_words': r'roast|lunch|brunch',
}


def hm(t):
    return int(t[:2]) * 60 + int(t[3:5])


def merged_config(user):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if isinstance(user, dict):
        cfg.update(user)
    return cfg


def closure(person, day):
    for c in person.get('closures') or []:
        if date.fromisoformat(c['first']) <= day <= date.fromisoformat(c['last']):
            return c.get('label', 'closed'), bool(c.get('sure', True))
    return None


def schedule_clash(person, day, win):
    """Text if the person is at (or travelling to/from) a scheduled item during the window, '' if not, None if no schedule that day."""
    items = (person.get('schedule') or {}).get(str(day.weekday()))
    if not items:
        return None
    if closure(person, day):
        return ''
    tr = int(person.get('travel_minutes', 0))
    for s, e in items:
        if hm(s) - tr < win[1] and hm(e) + tr > win[0]:
            return 'scheduled %s-%s' % (s, e)
    return ''


def meal_kind(meal, cfg, meal_time=None):
    if meal_time:
        m = hm(meal_time)
        return ('lunch', (m - 30, m + 60)) if m < 15 * 60 else ('evening', (m - 60, m + 60))
    if re.search(cfg['lunch_words'], meal or '', re.I):
        return 'lunch', tuple(hm(x) for x in cfg['lunch_window'])
    return 'evening', tuple(hm(x) for x in cfg['evening_window'])


def _stamp(v):
    if isinstance(v, dict):
        v = v.get('dateTime') or v.get('date') or ''
    return str(v)


def parse(s, tz):
    s = _stamp(s)
    if len(s) == 10:
        return datetime.fromisoformat(s).replace(tzinfo=tz), True
    d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    return (d.replace(tzinfo=tz) if d.tzinfo is None else d.astimezone(tz)), False


def compute(today, days, plan, events, cfg, recipes, tz):
    cfg = merged_config(cfg)
    people = cfg['people']
    own = {p['calendar']: p['name'] for p in people if p.get('calendar')}
    ents = list(own) + [c for c in cfg['shared_calendars'] if c not in own]
    names = [p['name'] for p in people]
    out_days = []
    for d in range(days):
        day = today + timedelta(days=d)
        key = DAYS[day.weekday()]
        meal = (plan or {}).get(key) or ''
        rec = (recipes or {}).get(meal.lower(), {})
        kind, win = meal_kind(meal, cfg, rec.get('meal_time'))
        day0 = datetime.combine(day, datetime.min.time(), tz)
        a, b = day0 + timedelta(minutes=win[0]), day0 + timedelta(minutes=win[1])
        away, unassigned, notes = {}, [], []
        for p in people:
            cc = schedule_clash(p, day, win)
            if cc:
                away.setdefault(p['name'], []).append(cc)
            cl = closure(p, day)
            if cl and (p.get('schedule') or {}).get(str(day.weekday())):
                notes.append('%s: closed, %s%s' % (p['name'], cl[0], '' if cl[1] else ' (dates unconfirmed)'))
        for ent in ents:
            for ev in (events or {}).get(ent, []) or []:
                s, ad1 = parse(ev.get('start'), tz)
                e, ad2 = parse(ev.get('end'), tz)
                title = str(ev.get('summary') or '')
                text = ' '.join(str(ev.get(k) or '') for k in ('summary', 'description', 'location'))
                if re.search(cfg['ignore_words'], title, re.I) and not re.search(cfg['away_words'], title, re.I):
                    continue
                if ad1:
                    if not (s.date() <= day < e.date()):
                        continue
                    if not re.search(cfg['away_words'], title, re.I):
                        continue
                    why = '%s (all day)' % title
                else:
                    if not (s < b and e > a):
                        continue
                    why = '%s %s-%s%s' % (title, s.strftime('%H:%M'), e.strftime('%H:%M'), '' if s.date() == e.date() else ' overnight')
                who = [own[ent]] if ent in own else [p['name'] for p in people if p.get('pattern') and re.search(p['pattern'], text, re.I)]
                if not who:
                    unassigned.append(why)
                    continue
                for n in who:
                    away.setdefault(n, []).append(why)
        home = [n for n in names if n not in away]
        all_out = bool(names) and not home
        out_days.append({
            'date': day.isoformat(), 'weekday': key, 'meal': meal, 'kind': kind,
            'window': ['%02d:%02d' % divmod(win[0], 60), '%02d:%02d' % divmod(win[1], 60)],
            'away': away, 'home': home, 'eating': len(home), 'all_out': all_out,
            'unassigned': unassigned, 'notes': notes, 'serves': rec.get('serves'),
            'clash': bool(away) or bool(unassigned),
            'advice': ('nobody home: skip or move the meal' if all_out and meal else
                       'nobody home' if all_out else ''),
        })
    return {'today': today.isoformat(), 'days': out_days,
            'clashes': [x for x in out_days if x['clash']],
            'all_clear': not any(x['clash'] for x in out_days)}
