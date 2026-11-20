"""ih_themed_events rules: what a themed event record is, how it is checked, and when it happens next.
Pure logic: no files, no network, no clock (callers pass `today`). The store keeps what these functions accept."""
import re
from datetime import date, timedelta

SLUG_RE = re.compile(r'^[a-z][a-z0-9_]{1,19}$')
ENTITY_RE = {'light': re.compile(r'^light\.[a-z0-9_]+$'), 'media_player': re.compile(r'^media_player\.[a-z0-9_]+$'),
             'automation': re.compile(r'^automation\.[a-z0-9_]+$'), 'switch': re.compile(r'^switch\.[a-z0-9_]+$'),
             'script': re.compile(r'^script\.[a-z0-9_]+$')}
TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
STATUSES = ('planned', 'built', 'live')
STATUS_LABELS = {'planned': 'Planned (helpers only or nothing yet)', 'built': 'Built (generated, not installed)', 'live': 'Live in Home Assistant'}
EFFECT_KINDS = ('sound', 'lights', 'sound_and_lights')
EVENT_FIELDS = {'slug', 'name', 'icon', 'when', 'status', 'light_group', 'lights', 'extra_switches', 'speakers', 'sounds_folder', 'pause_automations',
                'effects', 'look', 'auto_off', 'notes'}
NAME_MAX, TEXT_MAX, LIST_MAX = 80, 2000, 40


class RuleError(Exception):
    """code: bad (400), missing (404), conflict (409)."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _text(v, field, maximum=TEXT_MAX, required=False):
    if not isinstance(v, str):
        raise RuleError('bad', '%s must be text' % field)
    v = v.strip()
    if required and not v:
        raise RuleError('bad', '%s is required' % field)
    if len(v) > maximum:
        raise RuleError('bad', '%s is longer than %d characters' % (field, maximum))
    return v


def _entities(v, field, domain):
    if not isinstance(v, list) or len(v) > LIST_MAX:
        raise RuleError('bad', '%s must be a list of at most %d entries' % (field, LIST_MAX))
    out = []
    for e in v:
        if not isinstance(e, str) or not ENTITY_RE[domain].match(e):
            raise RuleError('bad', '%s: "%s" is not a %s entity id' % (field, str(e)[:40], domain))
        if e not in out:
            out.append(e)
    return out


def _clock(v, field):
    v = _text(v, field)
    if v and not TIME_RE.match(v):
        raise RuleError('bad', '%s must be HH:MM (24 hours)' % field)
    return v


def _iso_date(v, field):
    try:
        return date.fromisoformat(_text(v, field, 10, True))
    except ValueError:
        raise RuleError('bad', '%s must be a date like 2026-12-31' % field)


def _when(v):
    """`when`: {"annual": {"month": 12, "day": 31, "days": 1}} or {"dates": [{"start": "2026-12-31", "end": "2027-01-01"}]}. Empty = no date."""
    if not v:
        return {}
    if not isinstance(v, dict) or len(v) != 1 or next(iter(v)) not in ('annual', 'dates'):
        raise RuleError('bad', 'when must hold exactly one of: annual, dates')
    if 'annual' in v:
        a = v['annual']
        if not isinstance(a, dict) or set(a) - {'month', 'day', 'days'}:
            raise RuleError('bad', 'annual takes month, day and days')
        try:
            m, d, n = int(a['month']), int(a['day']), int(a.get('days', 1))
            date(2024, m, d)      # a leap year, so 29 Feb is allowed
        except (KeyError, TypeError, ValueError):
            raise RuleError('bad', 'annual needs a real month and day')
        if not 1 <= n <= 31:
            raise RuleError('bad', 'annual days must be 1 to 31')
        return {'annual': {'month': m, 'day': d, 'days': n}}
    rows = v['dates']
    if not isinstance(rows, list) or len(rows) > 60:
        raise RuleError('bad', 'dates must be a list of at most 60 ranges')
    out = []
    for r in rows:
        if not isinstance(r, dict) or set(r) - {'start', 'end'} or 'start' not in r:
            raise RuleError('bad', 'each date range needs start (and optional end)')
        s = _iso_date(r['start'], 'start')
        e = _iso_date(r.get('end') or r['start'], 'end')
        if e < s:
            raise RuleError('bad', 'end is before start')
        out.append({'start': s.isoformat(), 'end': e.isoformat()})
    return {'dates': sorted(out, key=lambda r: r['start'])}


def _effects(v):
    if not isinstance(v, list) or len(v) > 12:
        raise RuleError('bad', 'effects must be a list of at most 12')
    out, seen = [], set()
    for e in v:
        if not isinstance(e, dict) or set(e) - {'id', 'name', 'kind', 'sound', 'min_gap_minutes', 'max_gap_minutes', 'from', 'to', 'at'}:
            raise RuleError('bad', 'an effect takes id, name, kind, sound, min_gap_minutes, max_gap_minutes, from, to, at')
        eid = _text(e.get('id', ''), 'effect id', 20, True)
        if not SLUG_RE.match(eid) or eid in seen:
            raise RuleError('bad', 'effect id "%s" must be a unique lower-case word' % eid[:20])
        seen.add(eid)
        kind = e.get('kind', 'sound_and_lights')
        if kind not in EFFECT_KINDS:
            raise RuleError('bad', 'effect kind must be from: ' + ', '.join(EFFECT_KINDS))
        try:
            lo, hi = int(e.get('min_gap_minutes', 10)), int(e.get('max_gap_minutes', 15))
        except (TypeError, ValueError):
            raise RuleError('bad', 'effect gaps must be whole minutes')
        if not 1 <= lo <= hi <= 240:
            raise RuleError('bad', 'effect gaps need 1 <= min <= max <= 240')
        out.append({'id': eid, 'name': _text(e.get('name', eid), 'effect name', NAME_MAX, True), 'kind': kind,
                    'sound': _text(e.get('sound', ''), 'effect sound', 120), 'min_gap_minutes': lo, 'max_gap_minutes': hi,
                    'from': _clock(e.get('from', ''), 'effect from'), 'to': _clock(e.get('to', ''), 'effect to'), 'at': _clock(e.get('at', ''), 'effect at')})
    return out


def _look(v):
    if not v:
        return {}
    if not isinstance(v, dict) or set(v) - {'rgb', 'brightness', 'transition'}:
        raise RuleError('bad', 'look takes rgb, brightness and transition')
    rgb = v.get('rgb')
    if rgb is not None and (not isinstance(rgb, list) or len(rgb) != 3 or not all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in rgb)):
        raise RuleError('bad', 'look rgb must be three numbers 0 to 255')
    out = {}
    if rgb is not None:
        out['rgb'] = rgb
    for k, lo, hi in (('brightness', 1, 255), ('transition', 0, 30)):
        if k in v:
            if isinstance(v[k], bool) or not isinstance(v[k], int) or not lo <= v[k] <= hi:
                raise RuleError('bad', 'look %s must be a whole number %d to %d' % (k, lo, hi))
            out[k] = v[k]
    return out


def clean_event(raw, slug=None):
    """Return the stored form of an event record, or raise RuleError. `slug` fixes the slug when updating."""
    if not isinstance(raw, dict):
        raise RuleError('bad', 'the event must be an object')
    extra = set(raw) - EVENT_FIELDS
    if extra:
        raise RuleError('bad', 'unknown field: ' + ', '.join(sorted(extra)))
    s = _text(raw.get('slug', slug or ''), 'slug', 20, True)
    if not SLUG_RE.match(s):
        raise RuleError('bad', 'slug must be 2 to 20 lower-case letters, digits or _, starting with a letter')
    if slug and s != slug:
        raise RuleError('bad', 'the slug cannot change')
    status = raw.get('status', 'planned')
    if status not in STATUSES:
        raise RuleError('bad', 'status must be from: ' + ', '.join(STATUSES))
    group = _text(raw.get('light_group', ''), 'light_group', 80)
    if group and not ENTITY_RE['light'].match(group):
        raise RuleError('bad', 'light_group must be a light entity id')
    folder = _text(raw.get('sounds_folder', '/media/' + s), 'sounds_folder', 120)
    if folder and not re.match(r'^/media/[A-Za-z0-9_./-]+$', folder) or '..' in folder:
        raise RuleError('bad', 'sounds_folder must be inside /media')
    return {'slug': s, 'name': _text(raw.get('name', ''), 'name', NAME_MAX, True), 'icon': _text(raw.get('icon', 'mdi:party-popper'), 'icon', 60),
            'when': _when(raw.get('when')), 'status': status, 'light_group': group,
            'lights': _entities(raw.get('lights', []), 'lights', 'light'), 'extra_switches': _entities(raw.get('extra_switches', []), 'extra_switches', 'switch'),
            'speakers': _entities(raw.get('speakers', []), 'speakers', 'media_player'), 'sounds_folder': folder,
            'pause_automations': _entities(raw.get('pause_automations', []), 'pause_automations', 'automation'),
            'effects': _effects(raw.get('effects', [])), 'look': _look(raw.get('look')),
            'auto_off': _clock(raw.get('auto_off', '02:30'), 'auto_off'), 'notes': _text(raw.get('notes', ''), 'notes')}


def clean_checklist_item(raw):
    if not isinstance(raw, dict) or set(raw) - {'text', 'days_before'}:
        raise RuleError('bad', 'a checklist item takes text and days_before')
    try:
        n = int(raw.get('days_before', 0))
    except (TypeError, ValueError):
        raise RuleError('bad', 'days_before must be a whole number')
    if not -30 <= n <= 365:
        raise RuleError('bad', 'days_before must be -30 to 365')
    return {'text': _text(raw.get('text', ''), 'text', 300, True), 'days_before': n}


def occurrences(when, today, horizon_days=800):
    """Start/end dates of this event from `today` on (an event still running today is included), soonest first."""
    out = []
    if 'dates' in when:
        for r in when['dates']:
            s, e = date.fromisoformat(r['start']), date.fromisoformat(r['end'])
            if e >= today:
                out.append((s, e))
    elif 'annual' in when:
        a = when['annual']
        for year in (today.year - 1, today.year, today.year + 1, today.year + 2):
            try:
                s = date(year, a['month'], a['day'])
            except ValueError:      # 29 Feb in a non-leap year: use 28 Feb
                s = date(year, a['month'], 28)
            e = s + timedelta(days=a['days'] - 1)
            if e >= today:
                out.append((s, e))
    return sorted(o for o in out if (o[0] - today).days <= horizon_days)


def next_occurrence(when, today):
    """{"start", "end", "days_to_start", "running"} for the next or current run, or None."""
    occ = occurrences(when, today)
    if not occ:
        return None
    s, e = occ[0]
    return {'start': s.isoformat(), 'end': e.isoformat(), 'days_to_start': (s - today).days, 'running': s <= today <= e}


def checklist_due(item, occurrence):
    """Date this checklist item is due (start date minus days_before), or ''."""
    if not occurrence:
        return ''
    return (date.fromisoformat(occurrence['start']) - timedelta(days=item['days_before'])).isoformat()
