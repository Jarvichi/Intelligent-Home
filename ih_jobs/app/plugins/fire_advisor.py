"""fire_advisor: log-burner helper. Runs every 10 minutes and does three jobs, gated by time inside the run:
  detect (every run)        spot a fire being lit from a quick rise of a room temperature, record it, keep a 'fire is lit' switch in step (on while lit, off
                            when the room cools or after max_lit_hours), send ONE 'stuffy' display nudge per fire when the air-quality value stays high, and (while a fire
                            is lit) write a temperature sample into the watch log every run.
  advise (daily, advise_at) decide whether tonight is a good fire night (cold or windy evening, someone home or expected, heating would run anyway) and put a display row.
  review (weekly)           write a markdown week review (fire evenings vs others, advice followed or not, suggested threshold changes) into plugin state and the log. Nothing is sent.
The old script read the recorder database; this one uses Home Assistant history (a few hours for detect, 4 weeks of presence switches once a day for advise,
7-15 days of a few entities for advise and review) and the statistics websocket call for gas use. Starting the old fire_watch.py script is replaced by the watch log.
Every house value comes from settings (JSON in plugins[].settings). A section you give REPLACES the same-named default section's keys one by one (missing keys keep the default).
  weather_entity      weather entity (outside temperature, hourly forecast)
  people              presence switches ('on' = at home); names in the message come from the entity id with '_home' and the domain removed
  boiler_entity       boiler heating binary sensor (review only)
  ufh_entities        list of underfloor climate entities (review only; hvac_action 'heating' counts)
  gas_entity          cumulative gas kWh statistic id (advise and review; optional: without it the saving estimate is skipped)
  display_script       script entity that shows a display row; called with text 'title | detail'
  room_label          words for the room in the advice title ('fire room')
  watch_entities      {label: entity id} sampled into the watch log while a fire is lit (optional)
  rooms               {name: {detect_enabled, state_enabled, temp (entity), temp_attr (or null), rise_c, window_min, min_temp, flag (input_boolean), purity (entity or null),
                              nudge ('title | detail'), warmed_by [names of rooms whose fire also warms this one]}}
  detect              {cooldown_min 240, watch_minutes 240}
  state               {end_drop_c 0.5, end_window_min 30, end_below_c 25.0, min_age_min 40, max_lit_hours 8, purity_over 1200, purity_min 15}
  advise              {cold_evening_c 10, cool_evening_c 13, windy_kmh 30, min_people_home 1, typical_home_fraction 0.5, earliest_light '15:30', latest_light '19:00',
                       lead_min_before_arrival 60, gas_p_per_kwh 7, fire_displaces_fraction 0.3, evening_share_of_daily_gas 0.4, log_cost_per_fire_gbp null,
                       cost_mode 'cosy_not_wasteful'}   ('strict' = the saving must beat the log cost)
  review              {followed_window_min 60, days 7}
  advise_at '14:00'; review_at '18:30'; review_isoweekday 7 (Sunday); gate_min 30 (a timed job still runs up to this many minutes late)
  minutes             run every N minutes (10)
State: 'fires' (list, same shape as the old fires.json), 'advice' (list, same as advice.json), 'done' (dates of the last advise and review), 'reviews' (last 12 texts),
'watch' (samples per fire). The old json files can be copied in.
Dry: reads history, states and the forecast; logs every decision ('would call ...', 'would record fire ...'); sets no switch, sends no display row, saves nothing.
A timed job (advise, review) is logged once per day in dry. SAFETY: only ONE live copy: the old host loop and this plugin would both set the fire switch and send rows."""
import datetime as dt
import statistics as stat
import bisect

from ihlib import series as S

NAME = 'fire_advisor'
SCHEDULE = {'kind': 'every', 'minutes': 10}
SECTIONS = {
    'detect': {'cooldown_min': 240, 'watch_minutes': 240},
    'state': {'end_drop_c': 0.5, 'end_window_min': 30, 'end_below_c': 25.0, 'min_age_min': 40, 'max_lit_hours': 8, 'purity_over': 1200, 'purity_min': 15},
    'advise': {'cold_evening_c': 10.0, 'cool_evening_c': 13.0, 'windy_kmh': 30.0, 'min_people_home': 1, 'typical_home_fraction': 0.5, 'earliest_light': '15:30',
               'latest_light': '19:00', 'lead_min_before_arrival': 60, 'gas_p_per_kwh': 7.0, 'fire_displaces_fraction': 0.3, 'evening_share_of_daily_gas': 0.4,
               'log_cost_per_fire_gbp': None, 'cost_mode': 'cosy_not_wasteful'},
    'review': {'followed_window_min': 60, 'days': 7},
}
DEFAULTS = {'weather_entity': '', 'people': [], 'boiler_entity': '', 'ufh_entities': [], 'gas_entity': '', 'display_script': '', 'room_label': 'fire room',
            'watch_entities': {}, 'rooms': {}, 'advise_at': '14:00', 'review_at': '18:30', 'review_isoweekday': 7, 'gate_min': 30}
DEFAULTS.update({k: dict(v) for k, v in SECTIONS.items()})
ROOM = {'detect_enabled': True, 'state_enabled': True, 'temp': '', 'temp_attr': None, 'rise_c': 1.3, 'window_min': 30, 'min_temp': 21.0, 'flag': '', 'purity': None,
        'nudge': '', 'warmed_by': []}
_DRY_SEEN = set()   # dry runs log a timed job once per day (nothing is persisted in dry)


def sec(s, name):
    return dict(SECTIONS[name], **(s.get(name) or {}))


def rooms_of(s):
    return {k: dict(ROOM, **(v or {})) for k, v in (s.get('rooms') or {}).items()}


# ---------- pure rules ----------
def find_rises(rs, rise, win, mn, cooldown):
    """rs numeric series. -> [(trigger_ts, start_ts, base, now)]; start = time of the lowest sample in the window before the trigger"""
    out, last, ts = [], -1e12, [x[0] for x in rs]
    for i, (a, v) in enumerate(rs):
        if a - last < cooldown * 60 or v < mn:
            continue
        j = bisect.bisect_left(ts, a - win * 60)
        if j >= i:
            continue
        lo = min(rs[j:i + 1], key=lambda x: x[1])
        if v - lo[1] >= rise:
            out.append((a, lo[0], lo[1], v))
            last = a
    return out


def fire_ended(rs, now, cfg):
    """True when temp fell more than end_drop_c over end_window_min and is below end_below_c"""
    if not rs:
        return False
    cur = rs[-1][1]
    ref = [v for t, v in rs if t >= now - cfg['end_window_min'] * 60]
    return bool(ref) and max(ref) - cur > cfg['end_drop_c'] and cur < cfg['end_below_c']


def purity_sustained(rs, now, cfg):
    """rs = purity series incl. the row in force at the window start. True when every value over the last purity_min minutes is above purity_over"""
    t0 = now - cfg['purity_min'] * 60
    w = [v for t, v in rs if t >= t0]
    before = S.value_at(rs, t0)
    if before is not None:
        w.append(before)
    return bool(w) and before is not None and all(v > cfg['purity_over'] for v in w)


def person_name(entity):
    return entity.split('.')[1].replace('_home', '')


def kdd_from(days, base=15.5):
    """days = [(gas_kwh or None, [outside temps])] -> (median kWh per heating degree day, number of days used)"""
    ks = []
    for g, w in days:
        if not g or len(w) < 6:
            continue
        h = max(0, base - stat.mean(w))
        if h > 0.5:
            ks.append(g / h)
    return (stat.median(ks) if ks else None), len(ks)


def typical_home(wd, hours, today, people_series, tz):
    """per hour: share of the last 4 same weekdays on which at least one person was home at that hour (None = no data)"""
    res = {}
    for h in hours:
        n = tot = 0
        for w in range(1, 5):
            d = today - dt.timedelta(days=7 * w)
            if d.weekday() != wd:
                d = d - dt.timedelta(days=(d.weekday() - wd) % 7)
            t = dt.datetime(d.year, d.month, d.day, h, 0, tzinfo=tz).timestamp()
            first = people_series[0] if people_series else []
            if not any(ts < t + 1 for ts, _ in first):
                continue
            tot += 1
            n += any(S.value_at(p, t) == 'on' for p in people_series)
        res[h] = n / tot if tot else None
    return res


def advise_decision(a, now, today, fc, home, n_people, typ, kdd, nk, label):
    """The advice. fc = [{'temperature', 'wind_speed'}] evening hours. -> dict(date, light, time, cosy, reasons, msg)"""
    temps = [f['temperature'] for f in fc]
    wind = [f.get('wind_speed') or 0 for f in fc]
    tavg, tmin, wmax = stat.mean(temps), min(temps), max(wind)
    reasons = ['evening outside avg %.1fC (min %.1f), wind max %.0f km/h' % (tavg, tmin, wmax), 'home now: %s' % (', '.join(home) or 'nobody')]
    reasons.append('typical (same weekday, recent weeks) someone home: ' + ' '.join('%dh=%s' % (h, '?' if v is None else '{:.0%}'.format(v)) for h, v in typ.items()))
    cosy = 0
    if tavg <= a['cold_evening_c']:
        cosy += 2
        reasons.append('cold evening (+2)')
    elif tavg <= a['cool_evening_c']:
        cosy += 1
        reasons.append('cool evening (+1)')
    if wmax >= a['windy_kmh']:
        cosy += 1
        reasons.append('windy (+1)')
    first = next((h for h, v in typ.items() if v is not None and v >= a['typical_home_fraction']), None)
    if len(home) >= a['min_people_home']:
        light = max(now + dt.timedelta(minutes=15), now.replace(hour=15, minute=30))
        present = True
    elif first is not None:
        light = now.replace(hour=first, minute=0) - dt.timedelta(minutes=a['lead_min_before_arrival'])
        present = True
    else:
        light, present = None, False
    reasons.append('someone expected in the evening' if present else 'nobody expected home this evening')
    hdd_e = max(0, 15.5 - tavg)
    gas_eve = kdd * hdd_e * a['evening_share_of_daily_gas'] if kdd else None
    save = gas_eve * a['fire_displaces_fraction'] * a['gas_p_per_kwh'] / 100 if gas_eve is not None else None
    if gas_eve is not None:
        reasons.append('gas estimate for the evening ~%.1f kWh (%.1f kWh/degree-day, %d days); fire could displace ~%.0fp' % (gas_eve, kdd, nk, save * 100))
    else:
        reasons.append('no gas per degree day estimate yet')
    cost = a['log_cost_per_fire_gbp']
    if cost is None:
        reasons.append('log price unknown (log_cost_per_fire_gbp null): deciding on cosiness + heating would run anyway, NOT on cost')
    cheaper = True if cost is None or a.get('cost_mode') != 'strict' else (save is not None and save >= cost)
    if cost is not None:
        reasons.append('log cost %.0fp vs saving %.0fp -> %s (mode %s)' % (cost * 100, 0 if save is None else save * 100, 'cheaper' if save is not None and save >= cost else 'not cheaper', a.get('cost_mode')))
    if gas_eve is not None and gas_eve < 1:
        cheaper = False
        reasons.append('heating would barely run: no saving (no)')
    ok = present and cosy >= 1 and cheaper and light is not None
    if light is not None:
        e, l = a['earliest_light'], a['latest_light']
        light = min(max(light, light.replace(hour=int(e[:2]), minute=int(e[3:]))), light.replace(hour=int(l[:2]), minute=int(l[3:])))
    dec = dict(date=str(today), light=ok, time=light.strftime('%H:%M') if ok else None, cosy=cosy, reasons=reasons)
    if ok:
        who = 'all home' if n_people and len(home) == n_people else (', '.join(h.capitalize() for h in home) + ' home' if home else 'evening')
        title = ('\U0001F525 Good fire night: light %s ~%s' % (label, dec['time']))[:55]
        dec['msg'] = '%s | %s' % (title, ('%.0f°C tonight, %s' % (tavg, who))[:40])
    return dec


# ---------- helpers on the context ----------
def _day_ts(d, tz, h=0, m=0):
    return dt.datetime(d.year, d.month, d.day, h, m, tzinfo=tz).timestamp()


def _stat_ts(x):
    return x / 1000.0 if isinstance(x, (int, float)) else S.ts_of(x)


def gas_rows(ctx, entity, start, end):
    """[(ts, cumulative kWh)] hourly statistics rows (websocket). [] when no entity or on error."""
    if not entity:
        return []
    try:
        res = ctx.ha.ws({'type': 'recorder/statistics_during_period', 'start_time': start.astimezone(dt.timezone.utc).isoformat(), 'end_time': end.astimezone(dt.timezone.utc).isoformat(),
                         'statistic_ids': [entity], 'period': 'hour', 'types': ['state']})
    except Exception as e:
        ctx.log('gas statistics not available (%s)' % type(e).__name__)
        return []
    return [(_stat_ts(r['start']), r['state']) for r in (res or {}).get(entity, []) if r.get('state') is not None]


def gas_max(rows, t0, t1):
    v = [x for ts, x in rows if t0 <= ts < t1]
    return max(v) if v else None


def hist(ctx, entity, start, end, attr=None):
    return S.series(ctx.ha.history(entity, start, end), attr)


def show_row(ctx, text):
    s = ctx.settings
    if not s['display_script']:
        ctx.log('no display_script set: row not sent: ' + text)
        return False
    dom, _, svc = s['display_script'].partition('.')
    ctx.service(dom, svc, {'text': text})
    return not ctx.dry


def home_now(ctx):
    return [person_name(p) for p in ctx.settings['people'] if ctx.ha.state(p) == 'on']


def outside_now(ctx):
    try:
        return (ctx.ha.get('states/' + ctx.settings['weather_entity']) or {})['attributes'].get('temperature')
    except Exception:
        return None


# ---------- detect + state ----------
def detect(ctx, fires, tnow):
    """Returns (fires changed?, series by room). Dry: logs only."""
    s, d = ctx.settings, sec(ctx.settings, 'detect')
    rooms = rooms_of(s)
    changed, ser = False, {}
    t0 = tnow - 7200
    for room, r in rooms.items():
        if not r['temp']:
            continue
        ser[room] = S.num(hist(ctx, r['temp'], dt.datetime.fromtimestamp(t0, ctx.now.tzinfo), dt.datetime.fromtimestamp(tnow + 60, ctx.now.tzinfo), r['temp_attr']))
    new = []
    for room, r in rooms.items():
        if not r['detect_enabled'] or room not in ser:
            continue
        for trig, start, base, v in find_rises(ser[room], r['rise_c'], r['window_min'], r['min_temp'], d['cooldown_min']):
            if tnow - trig > 900:
                continue
            if any(f['room'] == room and tnow - f['start_ts'] < d['cooldown_min'] * 60 for f in fires):
                continue
            if any(f['room'] in r['warmed_by'] and tnow - f['start_ts'] < d['cooldown_min'] * 60 for f in fires):
                continue
            tz = ctx.now.tzinfo
            new.append(dict(room=room, start_ts=start, start=dt.datetime.fromtimestamp(start, tz).isoformat(timespec='minutes'),
                            detected=dt.datetime.fromtimestamp(trig, tz).isoformat(timespec='minutes'), temp_from=base, temp_to=v,
                            outside_c=outside_now(ctx), home=home_now(ctx)))
    for f in new:
        if ctx.dry:
            ctx.log('[DRY] would record fire in %s: %s -> %s from about %s' % (f['room'], f['temp_from'], f['temp_to'], f['start'][11:16]))
        else:
            ctx.log('FIRE DETECTED in %s: %s -> %s' % (f['room'], f['temp_from'], f['temp_to']))
            fires.append(f)
            changed = True
    if not new:
        ctx.log('no new fire')
    return changed, ser


def fire_state(ctx, fires, tnow, ser):
    """Per room: lit flag, fire out, ONE stuffy nudge per fire. Returns True if fires changed."""
    cfg, changed = sec(ctx.settings, 'state'), False
    for room, r in rooms_of(ctx.settings).items():
        if not r['state_enabled']:
            continue
        for f in fires:
            if f['room'] == room and 'end_ts' not in f and tnow - f['start_ts'] >= cfg['max_lit_hours'] * 3600:
                if not ctx.dry:
                    f['end_ts'], f['end_reason'] = tnow, 'timeout'
                    changed = True
        open_f = [f for f in fires if f['room'] == room and 'end_ts' not in f and tnow - f['start_ts'] < cfg['max_lit_hours'] * 3600]
        f = open_f[-1] if open_f else None
        if f:
            rs = ser.get(room) or []
            if tnow - f['start_ts'] > cfg['min_age_min'] * 60 and fire_ended(rs, tnow, cfg):
                ctx.log('%s: FIRE OUT%s' % (room, ' (dry, not saved)' if ctx.dry else ''))
                if not ctx.dry:
                    f['end_ts'], f['end'], f['end_reason'] = tnow, dt.datetime.fromtimestamp(tnow, ctx.now.tzinfo).isoformat(timespec='minutes'), 'cooling'
                    changed = True
                f = None
        if f and r['purity'] and not f.get('nudged'):
            ps = S.num(hist(ctx, r['purity'], dt.datetime.fromtimestamp(tnow - cfg['purity_min'] * 60 - 60, ctx.now.tzinfo), dt.datetime.fromtimestamp(tnow + 60, ctx.now.tzinfo)))
            if purity_sustained(ps, tnow, cfg):
                ctx.log('%s: STUFFY nudge -> %s%s' % (room, r['nudge'], ' (dry, not sent)' if ctx.dry else ''))
                if r['nudge']:
                    show_row(ctx, r['nudge'])
                if not ctx.dry:
                    f['nudged'] = tnow
                    changed = True
        want = 'on' if f else 'off'
        if r['flag']:
            if ctx.dry:
                ctx.log('[DRY] would call input_boolean.turn_%s for %s' % (want, r['flag']))
            else:
                try:
                    ctx.service('input_boolean', 'turn_' + want, {'entity_id': r['flag']})
                except Exception as e:
                    ctx.log('flag set failed (helper missing?) %s' % type(e).__name__)
    return changed


def watch_sample(ctx, fires, tnow):
    """While a fire is lit (within watch_minutes of its start): one sample of the watch entities per run (live only)."""
    s = ctx.settings
    if not s['watch_entities'] or ctx.dry:
        return
    wm = sec(s, 'detect')['watch_minutes']
    lit = [f for f in fires if 'end_ts' not in f and tnow - f['start_ts'] < wm * 60]
    if not lit:
        return
    f = lit[-1]
    row = {'t': ctx.now.strftime('%H:%M')}
    for k, e in s['watch_entities'].items():
        try:
            o = ctx.ha.get('states/' + e) or {}
            a = o.get('attributes') or {}
            row[k] = a.get('current_temperature') if e.startswith('climate.') and a.get('current_temperature') is not None else o.get('state')
        except Exception:
            row[k] = None
    w = ctx.state.load('watch')
    key = '%s-%s-fire' % (f['start'][:10], f['room'])
    w.setdefault(key, []).append(row)
    for old in sorted(w)[:-10]:
        w.pop(old)
    ctx.state.save('watch', w)


# ---------- advise ----------
def advise(ctx, tz):
    s = ctx.settings
    a = sec(s, 'advise')
    now = ctx.now
    today = now.date()
    wd = today.weekday()
    r = ctx.ha.post('services/weather/get_forecasts?return_response', {'entity_id': s['weather_entity'], 'type': 'hourly'})
    fcall = ((r or {}).get('service_response') or {}).get(s['weather_entity'], {}).get('forecast', [])
    fc = []
    for f in fcall:
        t = dt.datetime.fromisoformat(f['datetime']).astimezone(tz)
        if t.date() == today and 16 <= t.hour <= 21:
            fc.append(f)
    if not fc:
        ctx.log('no evening forecast')
        return None
    home = home_now(ctx)
    start = now - dt.timedelta(days=29)
    ps = [hist(ctx, p, start, now) for p in s['people']]
    typ = typical_home(wd, range(15, 22), today, ps, tz)
    kdd, nk = None, 0
    if s['gas_entity']:
        wser = S.num(hist(ctx, s['weather_entity'], now - dt.timedelta(days=15), now, 'temperature'))
        g = gas_rows(ctx, s['gas_entity'], now - dt.timedelta(days=15), now)
        days = []
        for i in range(1, 15):
            d = today - dt.timedelta(days=i)
            t0, t1 = _day_ts(d, tz), _day_ts(d + dt.timedelta(days=1), tz)
            days.append((gas_max(g, t0, t1), [v for _, v in S.window(wser, t0, t1)]))
        kdd, nk = kdd_from(days)
    dec = advise_decision(a, now, today, fc, home, len(s['people']), typ, kdd, nk, s['room_label'])
    ctx.log('DECISION: ' + ('light the %s at %s' % (s['room_label'], dec['time']) if dec['light'] else 'no fire advised'))
    for x in dec['reasons']:
        ctx.log(' - ' + x)
    if dec['light']:
        ctx.log('ROW: ' + dec['msg'])
        show_row(ctx, dec['msg'])
    if not ctx.dry:
        h = [x for x in ctx.state.load('advice', []) if x['date'] != str(today)]
        h.append({k: v for k, v in dec.items() if k != 'msg'})
        ctx.state.save('advice', h)
    return dec


# ---------- review ----------
def review(ctx, tz):
    s = ctx.settings
    rv, a = sec(s, 'review'), sec(s, 'advise')
    days = int(rv['days'])
    now = ctx.now
    today = now.date()
    fl = ctx.state.load('fires', [])
    ad = {x['date']: x for x in ctx.state.load('advice', [])}
    start = now - dt.timedelta(days=days + 1)
    wser = S.num(hist(ctx, s['weather_entity'], start, now, 'temperature')) if s['weather_entity'] else []
    bser = hist(ctx, s['boiler_entity'], start, now) if s['boiler_entity'] else []
    userie = [hist(ctx, e, start, now, 'hvac_action') for e in s['ufh_entities']]
    g = gas_rows(ctx, s['gas_entity'], start, now)
    lines = ['# Fire review %s (last %d days)' % (today, days), '', '| day | fire | outside 16-22 | boiler h 16-22 | underfloor h 16-22 | gas kWh/day | advice | followed |', '|---|---|---|---|---|---|---|---|']
    fire_b, non_b = [], []
    unfollowed = advised = unadvised_fire = 0

    def active_h(rs_all, t0, t1):
        rs, tot = S.window(rs_all, t0, t1), 0
        for i, (x, v) in enumerate(rs):
            b = rs[i + 1][0] if i + 1 < len(rs) else t1
            if v in ('on', 'heating'):
                tot += max(0, min(b, t1) - max(x, t0))
        return tot / 3600
    for i in range(days, 0, -1):
        d = today - dt.timedelta(days=i)
        t0, t1 = _day_ts(d, tz, 16), _day_ts(d, tz, 22)
        fs = [f for f in fl if f['start'][:10] == str(d)]
        w = [v for _, v in S.window(wser, t0, t1)]
        o = stat.mean(w) if w else None
        bo = active_h(bser, t0, t1)
        uf = sum(active_h(u, t0, t1) for u in userie)
        gg = gas_max(g, _day_ts(d, tz), _day_ts(d + dt.timedelta(days=1), tz))
        x = ad.get(str(d))
        fol = '-'
        if x and x['light']:
            advised += 1
            ok = any(abs(f['start_ts'] - _day_ts(d, tz, int(x['time'][:2]), int(x['time'][3:]))) <= rv['followed_window_min'] * 60 for f in fs)
            fol = 'yes' if ok else 'no'
            unfollowed += not ok
        elif fs:
            unadvised_fire += 1
        (fire_b if fs else non_b).append((bo, uf, o, gg))
        adv = ('light ' + x['time']) if x and x['light'] else ('none' if x else 'no record')
        lines.append('| %s | %s | %s | %.1f | %.1f | %s | %s | %s |' % (d.strftime('%a %d %b'), ', '.join(f['room'] + ' ' + f['start'][11:] for f in fs) or '-',
                     '-' if o is None else '%.1fC' % o, bo, uf, '-' if not gg else '%.1f' % gg, adv, fol))

    def avg(L, k):
        v = [r[k] for r in L if r[k] is not None]
        return '%.1f' % stat.mean(v) if v else '-'
    lines += ['', 'Fire evenings (%d): outside %sC, boiler %s h, underfloor %s h, gas %s kWh/day.' % (len(fire_b), avg(fire_b, 2), avg(fire_b, 0), avg(fire_b, 1), avg(fire_b, 3)),
              'Non-fire evenings (%d): outside %sC, boiler %s h, underfloor %s h, gas %s kWh/day.' % (len(non_b), avg(non_b, 2), avg(non_b, 0), avg(non_b, 1), avg(non_b, 3)),
              'Advice followed: %d/%d advised evenings. Fires on evenings with no advice: %d.' % (advised - unfollowed, advised, unadvised_fire),
              '(Gas data lags about 2 days; fire and non-fire evenings differ in weather too, so read the comparison loosely.)', '', '## Suggested threshold changes']
    sug = []
    if advised and unfollowed / advised > 0.5:
        sug.append('More than half the advised evenings had no fire: ask why (not home? too much faff?) and consider advising less often (lower cool_evening_c).')
    if unadvised_fire:
        sug.append('%d fire(s) on evenings the advisor said no: raise cool_evening_c (now %s) or relax typical_home_fraction (now %s).' % (unadvised_fire, a['cool_evening_c'], a['typical_home_fraction']))
    if a['log_cost_per_fire_gbp'] is None:
        sug.append('Log price still unknown: set log_cost_per_fire_gbp.')
    if not fl:
        sug.append('No fires recorded yet; keep the thresholds.')
    lines += ['- ' + x for x in sug] or ['- Keep the thresholds.']
    text = '\n'.join(lines)
    ctx.log('review written' + (' (dry, not saved)' if ctx.dry else ''))
    if not ctx.dry:
        rs = ctx.state.load('reviews', [])
        rs = [x for x in rs if x['date'] != str(today)] + [{'date': str(today), 'text': text}]
        ctx.state.save('reviews', rs[-12:])
    return text


def _slot(now, at):
    h, m = [int(x) for x in at.split(':')]
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def _due(ctx, kind, at, weekday=None):
    now, gate = ctx.now, int(ctx.settings['gate_min'])
    if weekday and now.isoweekday() != int(weekday):
        return False
    slot = _slot(now, at)
    if not (slot <= now < slot + dt.timedelta(minutes=gate)):
        return False
    key = str(now.date())
    if ctx.dry:
        if (kind, key) in _DRY_SEEN:
            return False
        _DRY_SEEN.add((kind, key))
        return True
    return ctx.state.load('done').get(kind) != key


def _mark(ctx, kind):
    if not ctx.dry:
        d = ctx.state.load('done')
        d[kind] = str(ctx.now.date())
        ctx.state.save('done', d)


def run(ctx):
    s = ctx.settings
    if not s['weather_entity'] or not s['rooms']:
        ctx.log('not configured (needs weather_entity and rooms in settings)')
        return False
    tz, tnow = ctx.now.tzinfo or dt.timezone.utc, ctx.now.timestamp()
    fires = ctx.state.load('fires', [])
    changed, ser = detect(ctx, fires, tnow)
    try:
        changed = fire_state(ctx, fires, tnow, ser) or changed
    except Exception as e:
        ctx.log('fire_state failed: %s' % type(e).__name__)
    if changed and not ctx.dry:
        ctx.state.save('fires', fires)
    watch_sample(ctx, fires, tnow)
    if _due(ctx, 'advise', s['advise_at']):
        advise(ctx, tz)
        _mark(ctx, 'advise')
    if _due(ctx, 'review', s['review_at'], s['review_isoweekday']):
        ctx.log(review(ctx, tz))
        _mark(ctx, 'review')
    return True
