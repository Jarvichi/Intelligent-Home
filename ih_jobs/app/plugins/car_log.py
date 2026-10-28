"""car_log: every 10 minutes, log electric-car charge sessions with their cost. Each rise of the battery percentage since the last run is costed at the
CURRENT electricity rate, so a session that crosses a cheap-rate boundary is priced right. A session ends when the car is unplugged, the battery has not risen
for `stall_min` minutes, or the charge target is reached. Wall energy = battery energy / efficiency (an estimate: there is no charger meter).
Every house value comes from settings (JSON in plugins[].settings):
  soc_entity       battery percentage sensor
  plug_entity      plug sensor; plugged when its state (any case) equals plug_value
  plug_value       the plug_entity state meaning 'cable locked in' (default CHARGING_CABLE_LOCKED)
  plug2_entity     optional second plug sensor; when it has a real state, it wins: plugged when its state equals plug2_value
  plug2_value      default CONNECTED
  target_entity    optional charge-target percentage sensor (session ends on reaching it)
  rate_entity      current electricity rate sensor (money per kWh)
  battery_kwh      usable battery size (default 39.0)
  efficiency       default 0.88
  stall_min        default 60
  offpeak_rate     a rate below this counts as off-peak (default 0.20)
  minutes          run every N minutes (default 10)
  publish_entity   optional: write a summary (month and week cost, last sessions) to this entity id via the HA state API (live only)
State (plugin state 'charges'): {"sessions": [...], "cur": null or {...}, "last_soc": number}. Same shape as the old script's json, so it can be copied in.
Dry: reads the sensors and logs what it would record; saves nothing."""
import datetime as dt

NAME = 'car_log'
SCHEDULE = {'kind': 'every', 'minutes': 10}
DEFAULTS = {'soc_entity': '', 'plug_entity': '', 'plug_value': 'CHARGING_CABLE_LOCKED', 'plug2_entity': '', 'plug2_value': 'CONNECTED',
            'target_entity': '', 'rate_entity': '', 'battery_kwh': 39.0, 'efficiency': 0.88, 'stall_min': 60, 'offpeak_rate': 0.20,
            'publish_entity': ''}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def finish(d, why):
    c = d.get('cur')
    if c and c['kwh'] > 0:
        d['sessions'].append({'start': c['start'], 'end': c['last_rise'], 'soc_from': c['soc_from'], 'soc_to': c['soc_to'], 'kwh': round(c['kwh'], 2),
                              'cost_gbp': round(c['cost'], 2), 'offpeak_share': round(c['kwh_off'] / c['kwh'] * 100) if c['kwh'] else 0, 'why': why})
        d['sessions'] = d['sessions'][-200:]
    d['cur'] = None


def is_plugged(s, plug2_state, plug_state):
    """True/False, or None when the plug state is unknown (keep state, try next time)."""
    if s['plug2_entity'] and plug2_state not in (None, 'unknown', 'unavailable'):
        return str(plug2_state).upper() == str(s['plug2_value']).upper()
    if plug_state is None:
        return None
    return str(plug_state).upper() == str(s['plug_value']).upper()   # exact match: 'NOT_LOCKED' contains 'LOCKED'


def tick(d, s, soc, plugged, rate, target, t):
    """Advance the log by one reading. d is changed in place. t = naive local datetime (no microseconds)."""
    last, c = d.get('last_soc'), d.get('cur')
    if c and not plugged:
        finish(d, 'unplugged')
        c = None
    elif c and (t - dt.datetime.fromisoformat(c['last_rise'])).total_seconds() > s['stall_min'] * 60:
        finish(d, 'stalled')
        c = None
    if plugged and last is not None and soc > last:
        if rate is not None:
            kwh = (soc - last) / 100 * s['battery_kwh'] / s['efficiency']
            if not c:
                c = d['cur'] = {'start': t.isoformat(), 'soc_from': last, 'soc_to': soc, 'kwh': 0.0, 'cost': 0.0, 'kwh_off': 0.0, 'last_rise': t.isoformat()}
            c['kwh'] += kwh
            c['cost'] += kwh * rate
            c['soc_to'] = soc
            c['last_rise'] = t.isoformat()
            if rate < s['offpeak_rate']:
                c['kwh_off'] += kwh
            if target is not None and soc >= target:
                finish(d, 'target')
    d['last_soc'] = soc
    return d


def rows(d):
    s = list(d['sessions'])
    c = d.get('cur')
    if c and c['kwh'] > 0:
        s.append(dict(c, end=c['last_rise'], cost_gbp=round(c['cost'], 2), kwh=round(c['kwh'], 2), offpeak_share=round(c['kwh_off'] / c['kwh'] * 100), why='charging'))
    return s


def summary(d, now):
    """The dashboard payload (same keys as the old script's `sensor` output). now = naive local datetime."""
    s = rows(d)
    month = now.strftime('%Y-%m')
    mtd = round(sum(x['cost_gbp'] for x in s if x['start'][:7] == month), 2)
    out = []
    for x in reversed(s[-50:]):
        a, b = dt.datetime.fromisoformat(x['start']), dt.datetime.fromisoformat(x['end'])
        m = int((b - a).total_seconds() / 60)
        out.append({'t': x['start'][:16].replace('T', ' '), 'p': '%d → %d%%' % (round(x['soc_from']), round(x['soc_to'])), 'kwh': x['kwh'], 'cost': '£%.2f' % x['cost_gbp'],
                    'off': '%d%%' % x['offpeak_share'], 'end': x['end'][:16].replace('T', ' '), 'why': x.get('why', ''),
                    'dur': '%dh %02dm' % divmod(m, 60) if m >= 60 else '%d min' % m, 'id': x['start']})
    wk0 = (now - dt.timedelta(days=now.weekday())).strftime('%Y-%m-%d')   # Monday
    week = round(sum(x['cost_gbp'] for x in s if x['start'][:10] >= wk0), 2)
    return {'mtd': mtd, 'week': week, 'n': len(s), 'rows': out, 'last': out[0] if out else {}, 'month_label': now.strftime('%B')}


def last_line(d):
    s = rows(d)
    if not s:
        return ''
    x = s[-1]
    return '%d%% → %d%% · %.0f kWh · £%.2f (%d%% off-peak)' % (round(x['soc_from']), round(x['soc_to']), x['kwh'], x['cost_gbp'], x['offpeak_share'])


def run(ctx):
    s = ctx.settings
    if not s['soc_entity'] or not s['plug_entity'] or not s['rate_entity']:
        ctx.log('not configured (needs soc_entity, plug_entity, rate_entity in settings)')
        return False
    d = ctx.state.load('charges')
    d.setdefault('sessions', [])
    d.setdefault('cur', None)
    d.setdefault('last_soc', None)
    soc = num(ctx.ha.state(s['soc_entity']))
    if soc is None:
        ctx.log('battery level unknown, skipped')
        return True
    plugged = is_plugged(s, ctx.ha.state(s['plug2_entity']) if s['plug2_entity'] else None, ctx.ha.state(s['plug_entity']))
    if plugged is None:
        ctx.log('plug state unknown, skipped')
        return True
    rate = num(ctx.ha.state(s['rate_entity']))
    target = num(ctx.ha.state(s['target_entity'])) if s['target_entity'] else None
    t = ctx.now.replace(tzinfo=None, microsecond=0)
    before = len(d['sessions'])
    tick(d, s, soc, plugged, rate, target, t)
    if ctx.dry:
        ctx.log('[DRY] would record: battery %d%%, plugged=%s, session=%s, finished sessions +%d (nothing saved)'
                % (round(soc), plugged, 'open' if d.get('cur') else 'none', len(d['sessions']) - before))
        return True
    ctx.state.save('charges', d)
    if s['publish_entity']:
        p = summary(d, t)
        ctx.ha.post('states/' + s['publish_entity'], {'state': str(p['mtd']), 'attributes': p})
    return True
