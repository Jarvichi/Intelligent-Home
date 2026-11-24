"""The Kindle's other screens (dinner menu, who's home, birthdays, the Kindle menu, cooking): values prepared here, layout in templates/<screen>.html
and templates/kindle.css (same fixed positions as popup_render.py / device_menu.py / cooking_render.py, so the touch zones in device_surface.py line up)."""
import datetime
from kindle_html import ART, render

EATS = ('ASAP', '12:30', '13:30', '18:30')
CAPTIONS = ('As soon as ready', '12:30 lunch', '13:30 lunch', '18:30 dinner')
MENU_ROWS = ('COOK DINNER', 'WHO IS HOME', 'ALL LIGHTS OFF', 'REFRESH', 'WI-FI', 'BIRTHDAYS', 'BACK')
COOKING_BUTTONS = ((596, 92, 'DONE'), (696, 92, 'DID IT'), (796, 84, 'ON IT'), (888, 60, '−5'), (956, 60, '+5'))


def dinner(data, now=None):
    selected = data.get('selected_eat', 'ASAP') if isinstance(data, dict) else 'ASAP'
    selected = selected if selected in EATS else 'ASAP'
    items = data.get('items') if isinstance(data, dict) and isinstance(data.get('items'), list) else []
    pages = data.get('pages', 1) if isinstance(data, dict) else 1
    page = data.get('page', 0) if isinstance(data, dict) else 0
    rows = []
    for item in items[:8]:
        if isinstance(item, dict):
            tonight = item.get('tonight') is True
            rows.append({'title': str(item.get('title') or ''), 'tonight': tonight,
                         'sub': ('TONIGHT’S PLAN · ' if tonight else '') + 'ready from ' + str(item.get('eat') or '—') + ' · ' + str(item.get('lead') or 0) + ' min'})
    return render('dinner.html', chips=[{'label': c, 'on': EATS[i] == selected} for i, c in enumerate(CAPTIONS)], items=rows,
                  pages=pages if type(pages) is int else 1, page=int(page) if isinstance(page, (int, float)) else 0,
                  message='Cooking menu unavailable' if not isinstance(data, dict) else 'No recipes available' if not items else '')


def home(panel, now=None):
    now = now or datetime.datetime.now().astimezone()
    members = panel.get('members') if isinstance(panel, dict) and isinstance(panel.get('members'), list) else []
    members = sorted(members, key=lambda x: 0 if isinstance(x, dict) and x.get('home') is True else 1)
    rows = []
    for member in members[:3]:
        if not isinstance(member, dict):
            continue
        art = member.get('art')
        status = 'Home' if member.get('home') is True else 'Out' if member.get('home') is False else 'Unavailable'
        try:
            when = datetime.datetime.fromisoformat(str(member.get('transition') or '').replace('Z', '+00:00')).astimezone()
            if when.date() == now.date():
                shown = 'today ' + when.strftime('%H:%M')
            elif (now - when).total_seconds() < 6 * 86400:
                shown = when.strftime('%a %H:%M')
            else:
                shown = when.strftime('%a %d %b %H:%M')
            status += ' · ' + ('arrived ' if member.get('home') is True else 'left ') + shown
        except (ValueError, TypeError, OverflowError, OSError):
            pass
        rows.append({'art': art if isinstance(art, str) and ART.fullmatch(art) else None, 'initial': str(member.get('name') or '?')[:1],
                     'name': member.get('name') or 'Unknown', 'out': member.get('home') is False, 'status': status})
    return render('home.html', members=rows, message='' if members else 'Presence unavailable')


def birthdays(data, now=None):
    items = data.get('items') if isinstance(data, dict) and isinstance(data.get('items'), list) else []
    rows = [{'name': it.get('name') or '', 'date': it.get('date') or '', 'countdown': it.get('countdown') or '', 'today': it.get('today') is True}
            for it in items[:8] if isinstance(it, dict)]
    return render('birthdays.html', items=rows, message='Birthdays unavailable' if not isinstance(data, dict) else 'No upcoming birthdays' if not items else '')


def menu(data=None, now=None):
    return render('menu.html', rows=MENU_ROWS)


def cooking(cook, alerts=None):
    target = cook.get('oven_target')
    next_rows = cook.get('next') if isinstance(cook.get('next'), list) else []
    shown = [r for r in next_rows[:3] if isinstance(r, dict)]
    count = min(3, len(next_rows))
    next_h = max(150, 150 + max(0, count - 1) * 60)
    line_y = max(392, 206 + next_h + 36)
    steps = []
    for i, row in enumerate((cook.get('steps') if isinstance(cook.get('steps'), list) else [])[:9]):
        if isinstance(row, dict) and line_y + 28 + i * 54 + 20 < 990:
            steps.append({'at': row.get('at', ''), 'text': ('✓ ' if row.get('done') else '') + str(row.get('text') or '')})
    life = [str(x.get('title') or '') for x in (alerts.get('alerts') or []) if isinstance(x, dict) and x.get('kind') in ('smoke', 'co')] if isinstance(alerts, dict) else []
    status = 'Alert sources not configured' if isinstance(alerts, dict) and alerts.get('configured') is False else (('Offline: '+', '.join(alerts['offline'][:2])) if isinstance(alerts,dict) and alerts.get('offline') else 'Alert sources unavailable') if alerts is None or (isinstance(alerts, dict) and not alerts.get('complete')) else ''
    return render('cooking.html', title=cook.get('title') or 'Cooking', eat=str(cook.get('eat') or '—'),
                  oven=(str(target) + '° target') if isinstance(target, (float, int)) and not isinstance(target, bool) else 'Unavailable',
                  next_h=next_h, next_rows=[str(r.get('at') or '') + ' ' + str(r.get('text') or '') for r in shown],
                  buttons=[{'x': x, 'w': w, 'label': label} for x, w, label in COOKING_BUTTONS], line_y=line_y, steps=steps,
                  prep=[str(r.get('text', '')) for r in (cook.get('prep') if isinstance(cook.get('prep'), list) else [])[:5] if isinstance(r, dict)],
                  warnings=life[:2] + ([status] if status else []))


TYRES_SIZE = (600, 760)
TYRES = ('fl', 'fr', 'rl', 'rr')
TYRE_NAMES = {'fl': 'FRONT LEFT', 'fr': 'FRONT RIGHT', 'rl': 'REAR LEFT', 'rr': 'REAR RIGHT'}


def tyres(values):
    """values: {'fl': 26, 'tfl': 32, ...} pressure now and target in PSI for each tyre. A tyre under 90% of its target is low."""
    rows = []
    for k in TYRES:
        now, target = values.get(k), values.get('t' + k)
        ok = all(type(x) in (int, float) and 0 < x < 200 for x in (now, target))
        rows.append({'k': k, 'name': TYRE_NAMES[k], 'now': round(now) if ok else '–', 'target': round(target) if ok else '–', 'low': ok and now < 0.9 * target})
    return render('tyres.html', tyres=rows, any_low=any(r['low'] for r in rows))


def loading():
    """Shown on the main screen for the minute or so between picking a meal and the cooking screen being ready."""
    return render('loading.html')
