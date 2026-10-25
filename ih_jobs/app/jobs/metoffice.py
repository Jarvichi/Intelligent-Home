"""Job metoffice: Met Office DataHub forecast -> sensor.met_office_forecast and sensor.weather_forecast_best (Met Office, or met.no when
the cache is older than STALE_HOURS or missing). Settings are add-on options: the API key (met_office_key, never logged), timezone (day boundaries), zone_entity (location; its latitude and
longitude are used) and fallback_weather_entity (the weather entity used when the Met Office data is missing or stale). The cache lives in
<data_dir>/metoffice_cache.json; HA writes go through ctx.set_state (dry = log only). The API fetch and the met.no read are real in dry mode.
Never call the API more often than MIN_AGE_MIN (the cache is shared by the scheduled and the start-up run)."""
import json, os, time, urllib.parse, urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MIN_AGE_MIN = 55        # never call the API more often than this
STALE_HOURS = 3         # Met Office data older than this -> fall back to met.no
DEFAULT_TZ = 'Europe/London'   # the Met Office covers the UK; option timezone overrides
DEFAULT_ZONE = 'zone.home'    # Home Assistant's own home zone
DEFAULT_WEATHER = 'weather.forecast_home'   # Home Assistant's default met.no entity
BASE = 'https://data.hub.api.metoffice.gov.uk/mo-blended-prob-forecast-feature-svc/2.0.0/collections/uk-spot-percentiles/instances/blended/position'
PARAMS = ['airTemperature1p5m', 'lweThicknessOfPrecipitationAmountSumPt01h', 'relativeHumidity1p5m', 'windSpeed10m',
          'windFromDirection10mMean', 'cloudAreaFraction', 'ultravioletIndex', 'weatherCodePt01h']


def tz_of(ctx):
    try:
        return ZoneInfo(str(ctx.opts.get('timezone') or DEFAULT_TZ))
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def cache_path(ctx):
    return os.path.join(ctx.data_dir, 'metoffice_cache.json')


def load(ctx):
    p = cache_path(ctx)
    return json.load(open(p)) if os.path.exists(p) else None


# Met Office significant weather code -> Home Assistant condition
WC = {0: 'clear-night', 1: 'sunny', 2: 'partlycloudy', 3: 'partlycloudy', 5: 'fog', 6: 'fog', 7: 'cloudy', 8: 'cloudy',
      9: 'rainy', 10: 'rainy', 11: 'rainy', 12: 'rainy', 13: 'pouring', 14: 'pouring', 15: 'pouring', 16: 'snowy-rainy',
      17: 'snowy-rainy', 18: 'snowy-rainy', 19: 'hail', 20: 'hail', 21: 'hail', 22: 'snowy', 23: 'snowy', 24: 'snowy',
      25: 'snowy', 26: 'snowy', 27: 'snowy', 28: 'lightning-rainy', 29: 'lightning-rainy', 30: 'lightning-rainy'}

def fetch(ctx):
    key = str(ctx.opts.get('met_office_key') or '').strip()
    if not key:
        raise RuntimeError('no met_office_key option')
    z = ctx.ha('states/' + (str(ctx.opts.get('zone_entity') or '').strip() or DEFAULT_ZONE))['attributes']
    url = BASE + '?coords=' + urllib.parse.quote('POINT(%s %s)' % (z['longitude'], z['latitude'])) + '&parameter-name=' + ','.join(PARAMS)
    with ctx.open(urllib.request.Request(url, headers={'apikey': key}), timeout=60) as r:
        raw = json.load(r)
    out = {'fetched_at': time.time(), 'series': {}}
    for cv in raw['coverages']:
        name = list(cv['ranges'])[0]
        rg = cv['ranges'][name]
        ax = cv['domain']['axes']
        times = ax['t']['values']
        vals = rg['values']
        if rg.get('axisNames', ['t'])[0] == 'percentiles':
            pc = ax['percentiles']['values']
            n = len(times)
            P = len(pc)
            # The API says axisNames [percentiles, t] but the values are laid out time-major (all percentiles of hour 1, then hour 2...):
            # checked 22 Oct 2026 - percentiles rise with p only under this reading. Hence the [j + P*i] indexing.
            out['series'][name] = {'t': times, 'pc': pc, 'v': [[vals[P * i + j] for i in range(n)] for j in range(P)]}
        else:
            out['series'][name] = {'t': times, 'pc': None, 'v': [vals]}
    json.dump(out, open(cache_path(ctx), 'w'))
    return out


def med(s, i):
    """median (p50) at index i; single-valued series return their only row"""
    if s['pc'] is None: return s['v'][0][i]
    return s['v'][s['pc'].index('50')][i]

def build(c, TZ=None):
    TZ = TZ or ZoneInfo(DEFAULT_TZ)
    S = c['series']; T = S['airTemperature1p5m']['t']
    pr = S['lweThicknessOfPrecipitationAmountSumPt01h']
    hourly = []
    for i, t in enumerate(T):
        dt = datetime.fromisoformat(t.replace('Z', '+00:00'))
        row = {'datetime': dt.isoformat()}
        def g(n, f=lambda x: x, nd=1):
            s = S.get(n)
            if not s or t not in s['t']: return None
            v = med(s, s['t'].index(t))
            return None if v is None else round(f(v), nd)
        row['temperature'] = g('airTemperature1p5m', lambda k: k - 273.15)
        mm = g('lweThicknessOfPrecipitationAmountSumPt01h', lambda x: x * 1000, 2)   # API gives metres
        row['precipitation'] = mm
        # chance of >= 0.1 mm in the hour = share of the 15 percentiles at or above 0.1 (approximate: percentiles are not evenly spaced)
        vs = [r[pr['t'].index(t)] for r in pr['v']] if t in pr['t'] else []
        row['precipitation_probability'] = round(100 * sum(1 for v in vs if v is not None and v * 1000 >= 0.1) / len(vs)) if vs else None
        row['humidity'] = g('relativeHumidity1p5m', lambda x: x * 100, 0)
        row['wind_speed'] = g('windSpeed10m', lambda x: x * 3.6)
        row['wind_bearing'] = g('windFromDirection10mMean', lambda x: x, 0)
        row['cloud_coverage'] = g('cloudAreaFraction', lambda x: x * 100, 0)
        row['uv_index'] = g('ultravioletIndex')
        code = g('weatherCodePt01h', lambda x: x, 0)
        row['condition'] = WC.get(int(code)) if code is not None else None
        if row['condition'] is None and row['precipitation'] is not None:
            row['condition'] = 'rainy' if row['precipitation'] >= 0.2 else 'cloudy'
        hourly.append(row)
    days = {}
    for h in hourly:
        d = datetime.fromisoformat(h['datetime']).astimezone(TZ)
        days.setdefault(d.date(), []).append((d, h))
    daily = []
    for day, rows in sorted(days.items()):
        if len(rows) < 12 and daily: continue          # partial last day
        temps = [h['temperature'] for _, h in rows if h['temperature'] is not None]
        winds = [h['wind_speed'] for _, h in rows if h['wind_speed'] is not None]
        conds = [h['condition'] for d, h in rows if 8 <= d.hour <= 20 and h['condition']] or [h['condition'] for _, h in rows if h['condition']]
        wet = [c for c in conds if c not in ('sunny', 'clear-night', 'partlycloudy', 'cloudy', 'fog')]
        # a day is "wet" when a quarter of the daytime hours are
        cond = Counter(wet).most_common(1)[0][0] if len(wet) * 4 >= len(conds) else Counter(conds).most_common(1)[0][0]
        daily.append({'datetime': datetime(day.year, day.month, day.day, 12, tzinfo=TZ).isoformat(), 'condition': cond,
                      'temperature': max(temps), 'templow': min(temps), 'precipitation': round(sum(h['precipitation'] or 0 for _, h in rows), 1),
                      'precipitation_probability': max([h['precipitation_probability'] or 0 for _, h in rows]),
                      'wind_speed': round(max(winds), 1) if winds else None})
    return hourly, daily

def metno(ctx, kind):
    ent = str(ctx.opts.get('fallback_weather_entity') or '').strip() or DEFAULT_WEATHER
    r = ctx.ha('services/weather/get_forecasts?return_response', {'entity_id': ent, 'type': kind})
    return r['service_response'][ent]['forecast']


def refresh(ctx, force=False):
    """One run (the old `refresh`). Returns True; a failed API fetch is logged and falls back to the cache / met.no, as before."""
    key = str(ctx.opts.get('met_office_key') or '').strip()
    TZ = tz_of(ctx)
    c = load(ctx)
    if force or not c or time.time() - c['fetched_at'] > MIN_AGE_MIN * 60:
        try:
            c = fetch(ctx)
        except Exception as e:
            msg = str(e)[:100]
            ctx.log('metoffice: fetch failed: %s %s' % (type(e).__name__, msg.replace(key, '***') if key else msg))
    age_h = (time.time() - c['fetched_at']) / 3600 if c else None
    attrs = {'friendly_name': 'Met Office forecast', 'icon': 'mdi:weather-partly-rainy', 'source': 'met_office'}
    now = datetime.now(TZ)
    if c:
        hourly, daily = build(c, TZ)
        hourly = [h for h in hourly if datetime.fromisoformat(h['datetime']) > datetime.now(timezone.utc)]
        daily = [d for d in daily if datetime.fromisoformat(d['datetime']).date() >= now.date()]
        attrs.update(daily=daily[:3], hourly=hourly[:12], fetched_at=datetime.fromtimestamp(c['fetched_at'], TZ).isoformat(),
                     age_hours=round(age_h, 2), hourly_hours=len(hourly))
        ctx.set_state('sensor.met_office_forecast', attrs['fetched_at'], attrs)
    best = dict(attrs, friendly_name='Weather forecast (best source)')
    if c and age_h <= STALE_HOURS:
        best['source'] = 'met_office'; best['daily'] = attrs['daily']; best['hourly'] = attrs['hourly']
    else:
        best['source'] = 'met.no'; best['daily'] = metno(ctx, 'daily')[:3]; best['hourly'] = metno(ctx, 'hourly')[:12]
        best.pop('fetched_at', None); best.pop('age_hours', None)
    ctx.set_state('sensor.weather_forecast_best', best['source'], best)
    ctx.log('metoffice: ok: best source = %s | met office age h = %s' % (best['source'], None if age_h is None else round(age_h, 2)))
    return True


def run(ctx):
    return refresh(ctx)
