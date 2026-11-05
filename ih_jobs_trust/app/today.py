"""Builds the read-only 'Today' card for the ingress status page from this add-on's own monthly results (<data>/trust_runs/<YYYY-MM>.json).

Only fixed fields are copied out (agent label from a fixed list, weighted scores, counts, dates): question text, answers and notes are never read out.
"""
import glob, json, os, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import trustjob

TITLE = 'Last test'
CACHE_S = 5
OLD_DAYS = 40
LABELS = {'groq': 'Groq', 'mistral': 'Mistral', 'gemini': 'Gemini', 'pollinations': 'Pollinations', 'openrouter': 'OpenRouter', 'openai': 'OpenAI',
          'grok': 'Grok', 'claude': 'Claude', 'mcp': 'MCP'}
_cache = {'t': 0.0, 'v': None}


def label(agent):
    base = str(agent or '').replace('ih_ai_layer_', '', 1)
    if base in LABELS:
        return LABELS[base]
    if base.startswith('mcp_'):
        base = 'mcp'
        return LABELS['mcp']
    clean = ''.join(c for c in base if (c.isalnum() and c.isascii()) or c in ' _-')[:30].strip()
    return clean.capitalize() if clean else 'AI'


def _day(d):
    return '%d %s' % (d.day, d.strftime('%B'))


def _tz(opts):
    try:
        return ZoneInfo(str(opts.get('timezone') or 'Europe/London'))
    except Exception:
        return ZoneInfo('Europe/London')


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def latest(data_dir):
    """Newest monthly result dict, or None."""
    files = sorted(glob.glob(os.path.join(data_dir, 'trust_runs', '[0-9][0-9][0-9][0-9]-[0-9][0-9].json')))
    for p in reversed(files):
        d = _read(p)
        if isinstance(d, dict) and isinstance(d.get('results'), dict):
            return d
    return None


def next_first(after):
    """First day of the month after `after`."""
    y, m = (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
    return after.replace(year=y, month=m, day=1)


def build(result, opts, now=None):
    """result = newest results dict (or None), opts = the add-on options dict -> payload dict, never None (no results gives a 'No results yet' row)."""
    tz = _tz(opts)
    now = datetime.now(tz) if now is None else now.astimezone(tz)
    live = str(opts.get('mode') or 'dry') == 'live' and opts.get('old_loops_disabled') is True
    test_note = '' if live else ' This add-on is in test mode: it records scores but adds no to-do.'
    if not result:
        nxt = next_first(now)
        return {'title': TITLE, 'subtitle': 'Score out of 100 on %d fixed questions' % len(trustjob.QUESTIONS),
                'rows': [{'name': 'Last test', 'plain': True, 'note': 'No results yet'}],
                'footnote': ('Next test: %s.' % _day(nxt)) + test_note}
    try:
        done = datetime.fromisoformat(str(result.get('finished'))).astimezone(tz)
    except (TypeError, ValueError):
        done = None
    res = result['results']
    by = {}
    for q, r in res.items():
        if q in trustjob.QUESTIONS and isinstance(r, dict) and r.get('agent') and r.get('ok'):
            by.setdefault(str(r['agent']), {})[q] = r
    rows = []
    for agent, qs in by.items():
        s = trustjob.summarise(qs)
        if s['test'] is None:
            continue
        rows.append({'name': label(agent), 'score': s['test'], 'pct': float(s['test']),
                     'note': 'Marks for %d of %d questions' % (s['scored'], len(trustjob.QUESTIONS)) + ('. Made something up' if s['invented'] else '')})
    rows.sort(key=lambda r: (-r['score'], r['name']))
    if not rows:
        rows = [{'name': 'Last test', 'plain': True, 'note': 'No scored answers'}]
    sub = 'Score out of 100 on %d fixed questions' % len(trustjob.QUESTIONS)
    foot = []
    if done:
        sub += ' (%s)' % _day(done)
        foot.append('Next test: %s.' % _day(next_first(done)))
        if now - done > timedelta(days=OLD_DAYS):
            foot.append('The last test is over %d days old. The monthly test may not be running.' % OLD_DAYS)
    else:
        foot.append('Next test: %s.' % _day(next_first(now)))
    out = {'title': TITLE, 'subtitle': sub, 'rows': rows, 'footnote': ' '.join(foot) + test_note}
    if result.get('summary', {}).get('stopped_early') if isinstance(result.get('summary'), dict) else False:
        out['footnote'] += ' The last test stopped early.'
    return out


def read_options(path=None):
    d = _read(path or os.path.join(os.environ.get('IHJ_DATA', '/data'), 'options.json'))
    return d if isinstance(d, dict) else {}


def today(data_dir=None, options=None, clock=time.time):
    """Cached (<= 5 s) payload. Unreadable data dir gives None (404)."""
    now = clock()
    if _cache['v'] is not None and now - _cache['t'] < CACHE_S:
        return _cache['v']
    d = data_dir or os.environ.get('IHJ_DATA', '/data')
    try:
        if not os.path.isdir(d):
            v = None
        else:
            v = build(latest(d), options if options is not None else read_options(os.path.join(d, 'options.json')), datetime.fromtimestamp(now, ZoneInfo('UTC')))
    except Exception:
        v = None
    _cache['t'], _cache['v'] = now, v
    return v
