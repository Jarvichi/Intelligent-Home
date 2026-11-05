"""Builds the read-only 'Today' card for the ingress status page: how many task channels are open and done, from this add-on's own /data/state.json.

Only two counts and fixed text are output: task names, goals and channel ids are never read out.
"""
import json, os, time

TITLE = 'Today'
SUBTITLE = 'Task channels right now'
CACHE_S = 5
_cache = {'t': 0.0, 'v': None}


def _read(path):
    """-> dict, {} when the file does not exist yet, None when it is unreadable."""
    try:
        with open(path) as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def build(state, opts):
    """state = parsed state.json (dict) or None, opts = options dict -> payload dict, or None when the state cannot be read."""
    if not isinstance(state, dict):
        return None
    tasks = state.get('tasks') if isinstance(state.get('tasks'), dict) else {}
    vals = [t for t in tasks.values() if isinstance(t, dict)]
    n_open = sum(1 for t in vals if t.get('state') == 'wip')
    n_done = sum(1 for t in vals if t.get('state') == 'done')
    shadow = opts.get('shadow') is not False or os.path.exists(os.path.join(os.environ.get('IHT_DATA', '/data'), 'STOP_LIVE'))
    tag = 'test' if shadow else 'live'
    try:
        days = max(1, min(365, int(opts.get('cleanup_days'))))
    except (TypeError, ValueError):
        days = 30
    out = {'title': TITLE, 'subtitle': SUBTITLE,
           'rows': [{'name': 'Open tasks', 'value': str(n_open), 'note': 'In the Work in Progress group', 'state': tag},
                    {'name': 'Done tasks', 'value': str(n_done), 'note': 'Tidied up %d days after they are done' % days, 'state': tag}]}
    if shadow:
        out['footnote'] = 'Test mode: no channels are created or moved, so these counts only change in live mode.'
    return out


def today(data_dir=None, clock=time.time):
    now = clock()
    if _cache['v'] is not None and now - _cache['t'] < CACHE_S:
        return _cache['v']
    d = data_dir or os.environ.get('IHT_DATA', '/data')
    try:
        v = build(_read(os.path.join(d, 'state.json')), _read(os.path.join(d, 'options.json')) or {})
    except Exception:
        v = None
    _cache['t'], _cache['v'] = now, v
    return v
