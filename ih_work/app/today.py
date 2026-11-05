"""Builds the read-only 'Today' card from the queue files and the snapshot main.py writes. Only fixed fields are copied out
(title, number, state tag, short fixed text): no instruction, result, error, consumer name or token can reach the page."""
import json, os, time
from datetime import datetime, timezone

from store import ACTIVE, Store, parse

TITLE = 'Today'
SUBTITLE = 'What is running, what is waiting and why'
STALE_S = 180
CACHE_S = 5
_cache = {'t': 0.0, 'v': None}


def _title(i):
    return str(i.get('title') or 'Untitled')[:80]


def _tz(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def build(items, snap, now, tz=timezone.utc):
    """items = list of queue items, snap = the snapshot dict (or None), now = aware datetime -> payload dict."""
    w = (snap or {}).get('worker') or {}
    ok = w.get('state') == 'ready' or w.get('state', '').startswith('ready')
    rows = []
    running = next((i for i in items if i.get('status') in ACTIVE), None)
    if running:
        rows.append({'name': _title(running), 'plain': True, 'state': 'running', 'note': 'Running now'})
    queued = sorted((i for i in items if i.get('status') == 'queued'), key=lambda i: i.get('seq', 0))
    for n, i in enumerate(queued[:5]):
        note = 'queued #%d / ' % i.get('seq', 0)
        note += 'ready to run' if (ok and n == 0 and not running) else 'runs when the pace is low enough'
        rows.append({'name': _title(i), 'plain': True, 'state': 'queued', 'note': note})
    if len(queued) > 5:
        rows.append({'name': '%d more waiting' % (len(queued) - 5), 'plain': True, 'note': 'Not shown here'})
    day = now.astimezone(tz).date()
    def today_count(status):
        c = 0
        for i in items:
            t = parse(i.get('finished_at'))
            if i.get('status') == status and t and t.astimezone(tz).date() == day:
                c += 1
        return c
    done, failed = today_count('done'), today_count('failed')
    rows.append({'name': 'Done today', 'value': str(done), 'note': 'Finished without a problem', 'state': 'ok'})
    rows.append({'name': 'Failed today', 'value': str(failed), 'note': 'Look at these on the Agents dashboard' if failed else 'None', 'state': 'failed' if failed else 'ok'})
    foot = w.get('reason') or 'No status yet. The add-on may have just started.'
    at = parse((snap or {}).get('at'))
    if snap and at and (now - at).total_seconds() > STALE_S:
        foot = 'Status is old (the add-on may be stuck). Last said: ' + foot
    return {'title': TITLE, 'subtitle': SUBTITLE, 'rows': rows, 'footnote': foot[:300]}


def fetch(data_dir=None):
    d = data_dir or os.environ.get('IHW_DATA', '/data')
    items = Store(d).peek()
    try:
        with open(os.path.join(d, 'state.json')) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    return items, st.get('snapshot')


def today(fetcher=fetch, clock=time.time, tz_name=None):
    now = clock()
    if _cache['v'] is not None and now - _cache['t'] < CACHE_S:
        return _cache['v']
    try:
        items, snap = fetcher()
        if tz_name is None:
            try:
                with open(os.path.join(os.environ.get('IHW_DATA', '/data'), 'options.json')) as f:
                    tz_name = json.load(f).get('timezone') or 'Europe/London'
            except (OSError, ValueError):
                tz_name = 'Europe/London'
        v = build(items, snap, datetime.fromtimestamp(now, timezone.utc), _tz(tz_name))
    except Exception:
        v = None
    _cache['t'], _cache['v'] = now, v
    return v
