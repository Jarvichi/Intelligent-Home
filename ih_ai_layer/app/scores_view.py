"""'AI scores' page data: each outside AI's IQ. Test part comes from the AI Trust Test add-on (GET /scores, bearer register_key), live part from this layer's own /status.

IQ = 80% test + 20% live when both exist (the old trust_test.py rule without human verdicts), else the test mark alone. Nothing but fixed fields is passed on:
no question text, answers, keys or tokens. The trust add-on being off or unreachable gives a clear message and no rows."""
import json, os, time, urllib.request

import today

TRUST_URL = 'http://172.30.32.1:8810/scores'
CACHE_S = 5
_cache = {'t': 0.0, 'v': None}


def _opt(path, key):
    try:
        with open(path) as f:
            return str(json.load(f).get(key) or '').strip()
    except (OSError, ValueError, AttributeError):
        return ''


def _num(v):
    return int(round(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _base(name):
    return str(name or '').replace('ih_ai_layer_', '', 1)


def fetch_trust(options_path=None, url=TRUST_URL, opener=urllib.request.urlopen):
    key = _opt(options_path or os.environ.get('IHA_OPTIONS', '/data/options.json'), 'register_key')
    if not key:
        raise RuntimeError('no register_key')
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
    with opener(req, timeout=3) as r:
        return json.loads(r.read(500000))


def build(trust, status):
    """trust = the /scores dict (or None), status = this layer's /status dict (or None) -> payload for api/scores."""
    live = {}
    if isinstance(status, dict):
        for name, v in ((status.get('scores') or {}).get('general_qa') or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                live[_base(name)] = int(round(v * 100))
    out = {'title': 'AI scores', 'subtitle': 'IQ = 80% fixed-question test + 20% live answer score (test alone when there is no live score yet)',
           'rows': [], 'date': None, 'dry': None, 'message': ''}
    if not isinstance(trust, dict) or trust.get('ok') is not True:
        out['message'] = 'The AI Trust Test add-on could not be reached, so there are no test scores to show. Check it is installed and running, and that both add-ons use the same register_key.'
        return out
    out['date'], out['dry'] = trust.get('date'), trust.get('dry')
    rows = []
    for r in trust.get('rows') or []:
        if not isinstance(r, dict):
            continue
        agent = _base(r.get('agent'))
        test, lv = _num(r.get('test')), live.get(agent)
        iq = None if test is None else (round(0.8 * test + 0.2 * lv) if lv is not None else test)
        rows.append({'name': today._label({'provider': agent}), 'iq': iq, 'test': test, 'live': lv, 'invented': bool(r.get('invented')),
                     'scored': _num(r.get('scored')), 'date': out['date']})
    rows.sort(key=lambda r: (r['iq'] is None, -(r['iq'] or 0), r['name']))
    out['rows'] = rows
    if not rows:
        out['message'] = 'No test results yet. The AI Trust Test runs on the 1st of each month.'
    elif out['dry']:
        out['message'] = 'The last test was a dry run (test mode): scores are real, but no review to-do was added.'
    return out


def scores(fetch_t=fetch_trust, fetch_s=today.fetch_status, clock=time.time):
    now = clock()
    if _cache['v'] is not None and now - _cache['t'] < CACHE_S:
        return _cache['v']
    try:
        t = fetch_t()
    except Exception:
        t = None
    try:
        s = fetch_s()
    except Exception:
        s = None
    v = build(t, s)
    _cache['t'], _cache['v'] = now, v
    return v
