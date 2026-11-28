"""Token-protected read-only GET /summary: a short weather summary for an assistant.

Built from the same two sensors as the ingress 'Today' card (today.py). Fixed fields only: words from a fixed list and numbers; nothing from a
sensor is passed through, so no keys, addresses or place names can leak. Read-only: no service calls. The token unlocks /summary and nothing else.
"""
import hmac, json, time
import today


def _round(v, n=1):
    return None if v is None else round(v, n)


def _cond(d):
    return today.CONDITIONS.get(d.get('condition')) if isinstance(d, dict) else None


def _day(d, name):
    t, low = today._num(d.get('temperature')), today._num(d.get('templow'))
    prob, mm = today._num(d.get('precipitation_probability')), today._num(d.get('precipitation'))
    return {'name': name, 'condition': _cond(d), 'high_c': _round(t), 'low_c': _round(low),
            'rain_chance_pct': None if prob is None else int(round(max(0.0, min(100.0, prob)))), 'rain_mm': _round(mm)}


def build(best, met=None, now=None):
    """HA state dicts -> summary dict, or None when there is no forecast."""
    if not isinstance(best, dict):
        return None
    a = best.get('attributes') if isinstance(best.get('attributes'), dict) else {}
    daily = [d for d in (a.get('daily') or []) if isinstance(d, dict)]
    if not daily:
        return None
    now = time.time() if now is None else now
    source = str(a.get('source') or best.get('state') or '')
    ma = met.get('attributes') if isinstance(met, dict) and isinstance(met.get('attributes'), dict) else {}
    fetched = today._when(ma.get('fetched_at'))
    age = None if fetched is None else max(0.0, now - fetched.timestamp())
    stale = source == 'met_office' and age is not None and age > today.STALE_S
    if stale:
        used = 'met.no'          # more than 3 h old: the house uses the Home Assistant weather (same rule as the job)
    else:
        used = source if source in today.SOURCES else 'unknown'
    hourly = [h for h in (a.get('hourly') or []) if isinstance(h, dict)]
    cur = hourly[0] if hourly else daily[0]
    out = {'now': {'condition': _cond(cur), 'temperature_c': _round(today._num(cur.get('temperature')))}}
    names = ['today', 'tomorrow', 'day_after']
    days = [_day(d, names[i]) for i, d in enumerate(daily[:3])]
    first = days[0]
    out['today'] = {k: first[k] for k in ('condition', 'high_c', 'low_c', 'rain_chance_pct', 'rain_mm')}
    out['next_days'] = days[1:3]
    out['source'] = today.SOURCES.get(used, 'unknown')
    out['source_note'] = 'Met Office data older than 3 hours: using met.no' if stale else ''
    out['forecast_age_min'] = None if age is None else int(age // 60)
    return out


def text(s):
    """One short plain-text block from build()."""
    def f(v, unit=''):
        return 'n/a' if v is None else ('%g%s' % (v, unit))
    n, t = s['now'], s['today']
    lines = ['Now: %s, %s' % (n['condition'] or 'n/a', f(n['temperature_c'], '°C')),
             'Today: %s, high %s, low %s, rain chance %s, rain %s' % (t['condition'] or 'n/a', f(t['high_c'], '°C'), f(t['low_c'], '°C'),
                                                                     f(t['rain_chance_pct'], '%'), f(t['rain_mm'], ' mm'))]
    for d in s['next_days']:
        lines.append('%s: %s, high %s, low %s, rain chance %s, rain %s' % (d['name'].replace('_', ' ').capitalize(), d['condition'] or 'n/a',
                                                                          f(d['high_c'], '°C'), f(d['low_c'], '°C'), f(d['rain_chance_pct'], '%'), f(d['rain_mm'], ' mm')))
    lines.append('Source: %s%s' % (s['source'], ' (%s)' % s['source_note'] if s['source_note'] else ''))
    return '\n'.join(lines) + '\n'


def token_ok(configured, header):
    """Constant-time check of 'Authorization: Bearer <token>'. An empty configured token is always refused (endpoint off)."""
    if not configured or not isinstance(header, str):
        return False
    h = header.strip()
    if h[:7].lower() != 'bearer ':
        return False
    return hmac.compare_digest(h[7:].strip().encode(), configured.encode())


def respond(configured, header, query, fetch=today.fetch_states, clock=time.time):
    """-> (status, content_type, body bytes). 404 when the option is empty (endpoint off), 401 without or with a wrong token."""
    if not configured:
        return 404, 'text/plain', b'not found\n'
    if not token_ok(configured, header):
        return 401, 'text/plain', b'unauthorised\n'
    try:
        best, met = fetch()
        s = build(best, met, clock())
    except Exception:
        s = None
    if s is None:
        return 503, 'text/plain', b'no forecast\n'
    if 'format=json' in (query or '').split('&'):
        return 200, 'application/json', json.dumps(s).encode()
    return 200, 'text/plain; charset=utf-8', text(s).encode()


def install(jobs_child):
    """Wrap jobs_child.make_handler so GET /summary is answered here; every other path goes to the shared handler unchanged."""
    orig = jobs_child.make_handler

    def make_handler(child):
        base = orig(child)
        old_get = base.do_GET

        def do_GET(self):
            path, _, query = self.path.partition('?')
            if path != '/summary':
                return old_get(self)
            code, ct, body = respond(child.opts.get('assistant_token', ''), self.headers.get('Authorization'), query)
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)
        base.do_GET = do_GET
        return base
    jobs_child.make_handler = make_handler
