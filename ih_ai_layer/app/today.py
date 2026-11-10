"""Builds the read-only 'Today' card for the ingress status page from the layer's own /status.

Only a fixed set of fields is copied out: nothing from /status is passed through, so no keys, tokens, URLs or request text can leak.
"""
import json, os, time, urllib.request

LABELS = {'groq': 'Groq', 'mistral': 'Mistral', 'gemini': 'Gemini', 'pollinations': 'Pollinations', 'openrouter': 'OpenRouter', 'openai': 'OpenAI',
          'serper': 'Serper (web search)', 'serpapi': 'SerpApi (showtimes and images)', 'publicapis': 'PublicAPIs (API directory)', 'steamstore': 'Steam Store (game art)', 'igdb': 'IGDB (game art)',
          'grok': 'Grok', 'claude': 'Claude', 'mcp': 'MCP'}
SEARCH = ('serper', 'serpapi')
TITLE = 'Today'
SUBTITLE = "Free allowance used, and how good each AI's answers have been (general questions)"
CACHE_S = 5
_cache = {'t': 0.0, 'v': None}


def _int(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def _label(p):
    kind = str(p.get('provider') or '')
    base = LABELS.get(kind) or (kind.capitalize() if kind else 'Provider')
    if kind == 'mcp':
        suffix = str(p.get('name') or '').replace('ih_ai_layer_mcp_', '', 1)
        if suffix and not suffix.startswith('ih_ai_layer'):
            base = 'MCP ' + suffix
    return base[:60]


def build(status):
    """status = the layer's /status dict (or None) -> payload dict for api/today, or None when there is nothing to show."""
    if not isinstance(status, dict):
        return None
    scores = (status.get('scores') or {}).get('general_qa') or {}
    dry = status.get('mode') == 'dry'
    stopped = bool(status.get('kill_switch'))
    rows = []
    for p in status.get('providers') or []:
        if not isinstance(p, dict):
            continue
        kind = str(p.get('provider') or '')
        row = {'name': _label(p)}
        model = p.get('model')
        if isinstance(model, str) and model:
            row['model'] = model[:80]
        sc = scores.get(p.get('name'))
        if isinstance(sc, (int, float)) and not isinstance(sc, bool):
            row['score'] = int(round(sc * 100))
        used, cap = _int(p.get('used_today')), _int(p.get('daily_cap'))
        if used is not None and cap:
            row['used'], row['cap'] = used, cap
            row['unit'] = 'searches' if kind in SEARCH else 'calls'
        if stopped or p.get('kill_switch') or not p.get('configured'):
            row['state'] = 'off'
        elif dry or p.get('shadow'):
            row['state'] = 'test'
        else:
            row['state'] = 'live'
        rows.append(row)
    rows.sort(key=lambda r: (r.get('score') is None, -(r.get('score') or 0), r['name']))
    return {'title': TITLE, 'subtitle': SUBTITLE, 'rows': rows}


def read_token(path):
    try:
        with open(path) as f:
            return str(json.load(f).get('api_token') or '').strip()
    except (OSError, ValueError, AttributeError):
        return ''


def fetch_status(port=8801, options_path=None, opener=urllib.request.urlopen):
    """Calls the main app's /status on loopback with the add-on's own token (read from its options file, never output)."""
    token = read_token(options_path or os.environ.get('IHA_OPTIONS', '/data/options.json'))
    if not token:
        return None
    req = urllib.request.Request('http://127.0.0.1:%d/status' % port, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    with opener(req, timeout=2) as r:
        return json.loads(r.read(2000000))


def today(fetch=fetch_status, clock=time.time):
    """Cached (<= 5 s) payload, or None when the layer cannot be read."""
    now = clock()
    if _cache['v'] is not None and now - _cache['t'] < CACHE_S:
        return _cache['v']
    try:
        v = build(fetch())
    except Exception:
        v = None
    _cache['t'], _cache['v'] = now, v
    return v
