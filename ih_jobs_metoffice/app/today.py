"""Builds the read-only 'Today' card for the ingress status page from the forecast sensors this add-on sets
(sensor.weather_forecast_best and sensor.met_office_forecast).

Only fixed fields are copied out (numbers and words from a fixed list): nothing from a sensor is passed through, so no keys, URLs or place names can leak.
"""
import time
from datetime import datetime, timezone

TITLE = 'Today'
SUBTITLE = 'Forecast it is sharing with the house'
CACHE_S = 5
STALE_S = 3 * 3600        # same limit the job uses before it falls back to the Home Assistant weather
BEST, MET = 'sensor.weather_forecast_best', 'sensor.met_office_forecast'
SOURCES = {'met_office': 'Met Office', 'met.no': 'met.no'}
CONDITIONS = {'clear-night': 'Clear', 'cloudy': 'Cloudy', 'exceptional': 'Exceptional', 'fog': 'Foggy', 'hail': 'Hail', 'lightning': 'Thunder',
              'lightning-rainy': 'Thunder and rain', 'partlycloudy': 'Part cloudy', 'pouring': 'Heavy rain', 'rainy': 'Rainy', 'snowy': 'Snowy',
              'snowy-rainy': 'Sleet', 'sunny': 'Sunny', 'windy': 'Windy', 'windy-variant': 'Windy'}
_cache = {'t': 0.0, 'v': None}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def _when(text):
    try:
        d = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def span(seconds):
    s = int(max(seconds, 0))
    if s < 90:
        return 'under 2 min'
    if s < 5400:
        return '%d min' % round(s / 60)
    if s < 2 * 86400:
        return '%d h' % round(s / 3600)
    return '%d days' % round(s / 86400)


def _deg(v):
    return ('%.1f' % v).rstrip('0').rstrip('.') + '°' if v is not None else None


def build(best, met=None, now=None):
    """best / met = HA state dicts (or None) -> payload dict for api/today, or None when there is no forecast."""
    if not isinstance(best, dict):
        return None
    a = best.get('attributes') if isinstance(best.get('attributes'), dict) else {}
    daily = [d for d in (a.get('daily') or []) if isinstance(d, dict)]
    if not daily:
        return None
    now = time.time() if now is None else now
    source = str(a.get('source') or best.get('state') or '')
    tag = SOURCES.get(source, 'forecast')
    rows, today_date = [], None
    for i, d in enumerate(daily[:3]):
        dt = _when(d.get('datetime'))
        if dt is None:
            continue
        if today_date is None:
            today_date = datetime.fromtimestamp(now, dt.tzinfo).date()
        gap = (dt.date() - today_date).days
        name = 'Today' if gap == 0 else 'Tomorrow' if gap == 1 else dt.strftime('%A')
        row = {'name': name}
        t, low, prob = _num(d.get('temperature')), _num(d.get('templow')), _num(d.get('precipitation_probability'))
        if t is not None:
            row['value'] = _deg(t)
        else:
            row['plain'] = True
        words = [CONDITIONS[d['condition']]] if d.get('condition') in CONDITIONS else []
        if low is not None:
            words.append('Low ' + _deg(low))
        if words:
            row['model'] = '. '.join(words)
        if prob is not None:
            p = max(0.0, min(100.0, prob))
            row['pct'] = p
            row['note'] = '%d%% chance of rain' % round(p)
        row['state'] = tag
        rows.append(row)
    if not rows:
        return None
    ma = met.get('attributes') if isinstance(met, dict) and isinstance(met.get('attributes'), dict) else {}
    fetched = _when(ma.get('fetched_at'))
    if fetched is not None:
        age = now - fetched.timestamp()
        late = age > STALE_S
        rows.append({'name': 'Forecast age', 'value': span(age) + ' old' if age >= 90 else 'just now',
                     'note': 'Older than 3 hours: the house uses Home Assistant weather instead' if late else 'Falls back to Home Assistant weather if more than 3 hours old',
                     'state': 'old' if late else 'ok'})
    else:
        rows.append({'name': 'Forecast age', 'plain': True, 'note': 'Not reported' + ('. The house is using Home Assistant weather' if source == 'met.no' else ''),
                     'state': 'fallback' if source == 'met.no' else 'unknown'})
    return {'title': TITLE, 'subtitle': SUBTITLE, 'rows': rows}


def fetch_states(ha=None):
    if ha is None:
        from ihlib.ha import HA
        ha = HA()
    out = []
    for e in (BEST, MET):
        try:
            out.append(ha.get('states/' + e, 3))
        except Exception:
            out.append(None)
    return out


def today(fetch=fetch_states, clock=time.time):
    now = clock()
    if _cache['v'] is not None and now - _cache['t'] < CACHE_S:
        return _cache['v']
    try:
        best, met = fetch()
        v = build(best, met, now)
    except Exception:
        v = None
    _cache['t'], _cache['v'] = now, v
    return v
