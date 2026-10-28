"""bus_watch: a recorder for one bus route. Polls the Bus Open Data (BODS SIRI-VM) vehicle feed during morning windows, projects each bus onto the route to estimate
minutes to a stop, keeps a SHADOW log (every poll, for scoring the estimate later) and a PROMISE log (one record per day and stop: which vehicle came and whether it
was a double-decker). It never sends a DM and has no 'run' mode: it is a recorder only. Runs as a `service` plugin (its own thread, one pass every `seconds`, 90 by default).
A pass outside every window returns at once without touching the network.
Day logic (same as the old host script): on a day with rules for today that is not in `closed` and is Monday-Friday, each rule {stop, time} is tracked from
(time - start_before_min) to (time + shadow_after_min): the bus whose estimated arrival is nearest `time` is followed, a 'passed' row ends it, and ONE promise record is written.
Any other day (weekend, closed day, weekday without rules) is a SWEEP from sweep_start to sweep_end: every inbound bus seen within sweep_km of `sweep_stop` is recorded
(an empty window is 'no_info', never a no-show, because no timetable is known).
The estimate is distance along the route x road_factor / avg_speed_kmh; it is NOT a timetable estimate.
Every house value comes from settings (JSON in plugins[].settings); defaults in brackets:
  operator_ref, line_ref   feed filters (required)
  feed_url                 BODS data feed URL (the public one)
  bods_key_option          NAME of the add-on option holding the BODS API key (bods_api_key). The key is never logged.
  presence_entity          reserved for a future DM mode; not used (this plugin never sends)
  direction                DirectionRef to follow (inbound)
  stops                    {key: {name, code, lat, lon}}  the stops that rules refer to
  schedule                 {mon..sun: [{stop: key, time: 'HH:MM'}]}  time = timetabled time at THAT stop
  closed                   [[from 'YYYY-MM-DD', to, reason]]  days with no service (anything unsure counts as closed)
  route_stops              [{code, lat, lon, name}] real stop coordinates of the route in order, ending at or after your stops (best), OR leave empty and
                           put the old route_stops.json in plugin state; with neither, a rough 'memory' route is built from route_polyline
  route_polyline           [[lat, lon], ...] rough route (first 2 points, then the stop, then the last point) and the closing point used with route_stops
  start_before_min [25], shadow_after_min [25], dm_when_eta_min [15] (only marks 'would_dm' in the log), avg_speed_kmh [35], road_factor [1.25], stale_min [5]
  sweep_start ['07:00'], sweep_end ['09:00'], sweep_km [1.5], sweep_stop (key), promise_stop (key of the promised journey; default sweep_stop)
  vehicle_page_url        page per vehicle with an og:description type text; {veh} = vehicle ref lower case (bustimes style); looked up 1 per second, cached
  promise_webhook_url     host URL that receives {"rows": [all promise records]} after every change (page rebuild stays on the host). Empty = not posted.
  keep_days [90]          shadow log rows older than this are dropped once a day
  seconds [90]            pass interval
Config seeding: lists/dicts you leave empty are read from plugin state (the folder /data/bus_watch/): config.json = the old config.json (stops, schedule, closed,
route_polyline), route_stops.json = the old route_stops.json, vehicle_types.json = the old type cache. Shapes are unchanged, so the old files can be copied in.
Files in /data/bus_watch/: shadow.jsonl (same rows as the old shadow.jsonl), promise_log.jsonl (same records as the old promise_log.jsonl), sessions.json, vehicle_types.json.
Dry: fetches the feed (read only) and the type page, logs every decision and every row it would write, writes nothing to disk (a per-process memory copy keeps the logic
running between passes) and posts nothing."""
import datetime as dt
import json
import math
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET

import ihlib.log

NAME = 'bus_watch'
SCHEDULE = {'kind': 'service', 'seconds': 90}
DEFAULTS = {'operator_ref': '', 'line_ref': '', 'feed_url': 'https://data.bus-data.dft.gov.uk/api/v1/datafeed/', 'bods_key_option': 'bods_api_key',
            'presence_entity': '', 'direction': 'inbound', 'stops': {}, 'schedule': {}, 'closed': [], 'route_stops': [], 'route_polyline': [],
            'start_before_min': 25, 'shadow_after_min': 25, 'dm_when_eta_min': 15, 'avg_speed_kmh': 35, 'road_factor': 1.25, 'stale_min': 5,
            'sweep_start': '07:00', 'sweep_end': '09:00', 'sweep_km': 1.5, 'sweep_stop': '', 'promise_stop': '',
            'vehicle_page_url': 'https://bustimes.org/vehicles/{veh}', 'promise_webhook_url': '', 'keep_days': 90, 'seconds': 90}
NS = {'s': 'http://www.siri.org.uk/siri'}
DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
_MEM = {}   # dry-run memory, per state folder


# ---------------- pure functions (same maths as the old host script) ----------------
def km(a, b):
    la = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * 111.2
    dx = (a[1] - b[1]) * 111.2 * math.cos(la)
    return math.hypot(dx, dy)


def project(p, a, b):
    """Nearest point on segment a-b: (distance km from p, fraction t)."""
    ax = (b[1] - a[1]) * math.cos(math.radians(a[0]))
    ay = b[0] - a[0]
    px = (p[1] - a[1]) * math.cos(math.radians(a[0]))
    py = p[0] - a[0]
    t = 0 if ax == 0 and ay == 0 else max(0, min(1, (px * ax + py * ay) / (ax * ax + ay * ay)))
    return km(p, (a[0] + t * ay, a[1] + t * (b[1] - a[1]))), t


def route(s, stop_key):
    """(polyline, index of the stop): real stop coordinates if route_stops is given, else the rough polyline with the stop inserted."""
    st = s['stops'][stop_key]
    rs = s.get('route_stops') or []
    if rs:
        pts = [[x['lat'], x['lon']] for x in rs]
        codes = [x['code'] for x in rs]
        return pts + [s['route_polyline'][-1]], codes.index(st['code'])
    return s['route_polyline'][:2] + [[st['lat'], st['lon']]] + s['route_polyline'][-1:], 2


def remaining_km(s, stop_key, p):
    """km along the route from the bus to the stop, or None if the bus is past it or far from the route. -> (km or None, off-route km)."""
    pl, si = route(s, stop_key)
    best = None
    for i in range(len(pl) - 1):
        d, t = project(p, pl[i], pl[i + 1])
        if best is None or d < best[0]:
            best = (d, i, t)
    d, i, t = best
    if d > 6:
        return None, d
    if i >= si:
        return None, d
    rem = (1 - t) * km(pl[i], pl[i + 1]) + sum(km(pl[j], pl[j + 1]) for j in range(i + 1, si))
    return rem, d


def parse(xml):
    out = []
    for va in ET.fromstring(xml).iter('{%s}VehicleActivity' % NS['s']):
        def g(path):
            e = va.find(path, NS)
            return e.text if e is not None else None
        m = 's:MonitoredVehicleJourney/'
        try:
            out.append({'dir': g(m + 's:DirectionRef'), 'veh': g(m + 's:VehicleRef'), 'lat': float(g(m + 's:VehicleLocation/s:Latitude')), 'lon': float(g(m + 's:VehicleLocation/s:Longitude')),
                        'rec': dt.datetime.fromisoformat(g('s:RecordedAtTime')), 'origin': g(m + 's:OriginName'), 'dest': g(m + 's:DestinationName'),
                        'code': g(m + 's:FramedVehicleJourneyRef/s:DatedVehicleJourneyRef')})
        except Exception:
            continue
    return out


def candidates(s, stop_key, buses, now):
    res = []
    for b in buses:
        if b['dir'] != s['direction']:
            continue
        age = (now - b['rec']).total_seconds() / 60
        rem, off = remaining_km(s, stop_key, (b['lat'], b['lon']))
        if rem is None:
            continue
        eta = rem * s['road_factor'] / s['avg_speed_kmh'] * 60
        res.append({**b, 'age_min': age, 'rem_km': rem, 'eta_min': eta, 'off_route_km': off, 'stale': age > s['stale_min']})
    return sorted(res, key=lambda r: r['eta_min'])


def closed_reason(closed, day):
    for a, b, why in closed:
        if a <= day.isoformat() <= b:
            return why
    return None


def rules_today(s, now):
    return s['schedule'].get(DAYS[now.weekday()], [])


# ---------------- promise log (same rules as the old promise.py) ----------------
DOUBLE = re.compile(r'(?-i:(?<![A-Za-z])[A-Z0-9]*DD\b)|Enviro ?400|Enviro ?500|Streetdeck|Olympian|Gemini|B9TL|Trident|Routemaster|Double|Titan|Scania N\d+UD|Volvo B5TL|Citaro? ?DD', re.I)
SINGLE = re.compile(r'Enviro ?(200|300|350)|StreetLite|Solo|Versa|B7RLE|B8RLE|B7R\b|Citaro|Sprinter|Dart(?! ?\w*DD)|MMC|Streetair|Yutong E1[0-9]|Tourismo|OmniLink|Wright Eclipse(?! Gemini)|Plaxton Pointer|Optare', re.I)


def classify(text):
    """'double' / 'single' / 'unknown' from a vehicle-type description. Single only for known single-deck models."""
    if not text or text == '?':
        return 'unknown'
    if DOUBLE.search(text):
        return 'double'
    if SINGLE.search(text):
        return 'single'
    return 'unknown'


def verdict(veh, kind, timetabled=True):
    if not veh:
        return 'no_bus' if timetabled else 'no_info'
    return {'double': 'kept', 'single': 'broken'}.get(kind, 'unknown')


def make_record(day, stop, stop_name, sched, veh, vtype, first, last, day_type='college', source='live (Bus Open Data)', timetabled=True, promise_stop=''):
    kind = classify(vtype) if veh else 'unknown'
    return {'date': day, 'stop': stop, 'stop_name': stop_name, 'scheduled': sched, 'promise_journey': stop == promise_stop, 'day_type': day_type, 'source': source,
            'vehicle': veh, 'vehicle_type': vtype if veh else None, 'deck': kind, 'bus_seen': bool(veh), 'first_seen': first, 'last_seen': last, 'timetabled': timetabled,
            'verdict': verdict(veh, kind, timetabled)}


def upsert_rows(rows, rec):
    """-> (new rows, changed). Idempotent per (date, stop, scheduled). A decided verdict is never overwritten by unknown/no_bus/no_info."""
    out, found, changed = [], False, False
    for r in rows:
        if r['date'] == rec['date'] and r['stop'] == rec['stop'] and r['scheduled'] == rec['scheduled']:
            found = True
            if r['verdict'] in ('kept', 'broken') and rec['verdict'] in ('unknown', 'no_bus', 'no_info'):
                out.append(r)
            else:
                out.append(rec)
                changed = rec != r
        else:
            out.append(r)
    if not found:
        out.append(rec)
        changed = True
    if changed:
        out.sort(key=lambda r: (r['date'], r['scheduled']))
    return out, changed


# ---------------- storage (live = files in the plugin state folder, dry = memory) ----------------
class Box:
    def __init__(self, ctx):
        self.ctx, self.dry, self.folder = ctx, ctx.dry, ctx.state.folder
        self.mem = _MEM.setdefault(self.folder, {})

    def load(self, name, default=None):
        d = {} if default is None else default
        if self.dry:
            if name in self.mem:
                return self.mem[name]
            return self.ctx.state.load(name, d)   # read-only look at seeded files
        return self.ctx.state.load(name, d)

    def save(self, name, obj):
        if self.dry:
            self.mem[name] = obj
        else:
            self.ctx.state.save(name, obj)

    def path(self, fname):
        return os.path.join(self.folder, fname)

    def promise_rows(self):
        if self.dry:
            return self.mem.setdefault('promise_rows', [])
        try:
            with open(self.path('promise_log.jsonl')) as f:
                return [json.loads(l) for l in f if l.strip()]
        except OSError:
            return []

    def save_promise(self, rows):
        if self.dry:
            self.mem['promise_rows'] = rows
            return
        os.makedirs(self.folder, exist_ok=True)
        tmp = self.path('promise_log.jsonl.tmp')
        with open(tmp, 'w') as f:
            f.write(''.join(json.dumps(r) + '\n' for r in rows))
        os.replace(tmp, self.path('promise_log.jsonl'))

    def shadow(self, row):
        if self.dry:
            self.ctx.log('[DRY] would log shadow row: ' + ihlib.log.one_line(json.dumps(row), 220))
            return
        os.makedirs(self.folder, exist_ok=True)
        with open(self.path('shadow.jsonl'), 'a') as f:
            f.write(json.dumps(row) + '\n')

    def prune_shadow(self, cutoff_day):
        if self.dry or not os.path.exists(self.path('shadow.jsonl')):
            return
        keep = []
        with open(self.path('shadow.jsonl')) as f:
            lines = f.readlines()
        for l in lines:
            try:
                r = json.loads(l)
            except ValueError:
                continue
            if (r.get('t') or r.get('first_past') or '9999')[:10] >= cutoff_day:
                keep.append(l if l.endswith('\n') else l + '\n')
        tmp = self.path('shadow.jsonl.tmp')
        with open(tmp, 'w') as f:
            f.write(''.join(keep))
        os.replace(tmp, self.path('shadow.jsonl'))


# ---------------- feed and vehicle type ----------------
def fetch(ctx, s):
    key = ctx.option(s['bods_key_option'])
    if not key:
        raise RuntimeError('no BODS key option set')
    q = urllib.parse.urlencode({'api_key': key, 'operatorRef': s['operator_ref'], 'lineRef': s['line_ref']})
    return ctx.http.get_bytes(s['feed_url'] + '?' + q, timeout=30)


def vehicle_type(ctx, box, s, veh):
    """Vehicle type text from the vehicle page (cached by the number after the last '-'). '?' when the lookup failed (not cached); None if the page has no text."""
    num = veh.split('-')[-1]
    cache = box.load('vehicle_types')
    if num not in cache:
        try:
            h = ctx.http.get_bytes(s['vehicle_page_url'].format(veh=veh.lower()), min_interval=1.0, timeout=15).decode('utf8', 'ignore')
            m = re.search(r'og:description" content="([^"]+)"', h)
            t = m.group(1) if m else None
        except Exception:
            return '?'
        cache[num] = t
        box.save('vehicle_types', cache)
    return cache[num]


# ---------------- steps ----------------
def new_session(now):
    return {'started': now.isoformat(), 'veh': None, 'pveh': None, 'dm_done': False, 'prev_t': None, 'mind_d': 9e9, 'mind_t': None, 'first': None, 'last': None,
            'done': False, 'recorded': False, 'seen': {}}


def record_promise(ctx, box, s, rec):
    rows, changed = upsert_rows(box.promise_rows(), rec)
    ctx.log('%spromise record: %s %s %s %s' % ('[DRY] would write ' if ctx.dry else '', rec['date'], rec['stop'], rec['scheduled'], rec['verdict']))
    if changed:
        box.save_promise(rows)
        box.save('webhook', {'pending': True})
    return changed


def flush_webhook(ctx, box, s):
    if not box.load('webhook').get('pending'):
        return
    url = s['promise_webhook_url']
    if not url:
        return
    rows = box.promise_rows()
    if ctx.dry:
        ctx.log('[DRY] would POST %d promise record(s) to the configured webhook' % len(rows))
        return
    try:
        code = ctx.http.post_json(url, {'source': NAME, 'rows': rows})
        ok = 200 <= int(code) < 300
    except Exception as e:
        ctx.log('promise webhook failed: ' + type(e).__name__)
        ok = False
    if ok:
        box.save('webhook', {'pending': False})
        ctx.log('promise log posted (%d records)' % len(rows))


def poll_rule(ctx, box, s, rule, sess, buses, now):
    """One poll of the shadow tracker for one rule. Mutates sess. Same decisions as the old shadow1 loop body."""
    u = dt.datetime.combine(now.date(), dt.time.fromisoformat(rule['time']), now.tzinfo)
    stop = s['stops'][rule['stop']]
    box.shadow({'kind': 'all', 'stop': rule['stop'], 't': now.isoformat(), 'buses': [[b['veh'], b['lat'], b['lon']] for b in buses]})
    cands = [r for r in candidates(s, rule['stop'], buses, now) if not r['stale']]
    if sess['veh'] is None and cands:
        sess['veh'] = min(cands, key=lambda r: abs((now + dt.timedelta(minutes=r['eta_min']) - u).total_seconds()))['veh']
    veh = sess['veh']
    if not veh:
        ctx.log('rule %s %s: %d inbound bus(es), none to track yet' % (rule['stop'], rule['time'], len(buses)))
        return
    b = next((x for x in buses if x['veh'] == veh), None)
    c = next((r for r in cands if r['veh'] == veh), None)
    if b:
        sess['first'] = sess['first'] or now.isoformat(timespec='seconds')
        sess['pveh'] = veh
        sess['last'] = now.isoformat(timespec='seconds')
        d = km((b['lat'], b['lon']), (stop['lat'], stop['lon']))
        if d < sess['mind_d']:
            sess['mind_d'], sess['mind_t'] = d, now.isoformat()
    if c:
        row = {'kind': 'poll', 'stop': rule['stop'], 'due': rule['time'], 't': now.isoformat(), 'veh': veh, 'lat': c['lat'], 'lon': c['lon'], 'rem_km': round(c['rem_km'], 2),
               'eta_min': round(c['eta_min'], 1), 'pred_arrival': (now + dt.timedelta(minutes=c['eta_min'])).isoformat(), 'route': 'stops' if s.get('route_stops') else 'memory'}
        if not sess['dm_done'] and c['eta_min'] <= s['dm_when_eta_min'] and c['eta_min'] > 3:
            row['would_dm'] = True
            sess['dm_done'] = True
        box.shadow(row)
        sess['prev_t'] = now.isoformat()
        ctx.log('tracking %s: %.1f km to the stop, about %.0f min' % (veh, c['rem_km'], c['eta_min']))
    elif sess['prev_t'] and b:
        box.shadow({'kind': 'passed', 'stop': rule['stop'], 'veh': veh, 'last_before': sess['prev_t'], 'first_past': now.isoformat(), 'closest_km': round(sess['mind_d'], 2), 'closest_t': sess['mind_t']})
        ctx.log('bus passed the stop')
        sess['done'] = True


def finish_rule(ctx, box, s, rule, sess, now):
    """Write the ONE promise record for a tracked rule (after a pass or the end of its window)."""
    if sess['recorded']:
        return
    sess['recorded'] = True
    if now.weekday() > 4:
        return
    veh = sess['pveh']
    vtype = vehicle_type(ctx, box, s, veh) if veh else None
    rec = make_record(now.date().isoformat(), rule['stop'], s['stops'][rule['stop']]['name'], rule['time'], veh, vtype, sess['first'], sess['last'], 'college',
                      promise_stop=s['promise_stop'] or s['sweep_stop'])
    record_promise(ctx, box, s, rec)


def sweep_poll(ctx, s, sess, buses, now):
    st = s['stops'][s['sweep_stop']]
    for b in buses:
        d = km((b['lat'], b['lon']), (st['lat'], st['lon']))
        if d <= s['sweep_km'] and (now - b['rec']).total_seconds() < 300:
            x = sess['seen'].setdefault(b['veh'], {'first': now.isoformat(timespec='seconds'), 'last': None, 'min': 9e9})
            x['last'] = now.isoformat(timespec='seconds')
            x['min'] = min(x['min'], d)
    ctx.log('sweep: %d bus(es) seen so far' % len(sess['seen']))


def finish_sweep(ctx, box, s, sess, now):
    if sess['recorded']:
        return
    sess['recorded'] = True
    st = s['stops'][s['sweep_stop']]
    day_type = 'weekend' if now.weekday() > 4 else 'holiday'
    day = now.date().isoformat()
    ps = s['promise_stop'] or s['sweep_stop']
    for veh, x in sorted(sess['seen'].items(), key=lambda kv: kv[1]['first']):
        record_promise(ctx, box, s, make_record(day, s['sweep_stop'], st['name'], x['first'][11:16], veh, vehicle_type(ctx, box, s, veh), x['first'], x['last'], day_type,
                                                'live (Bus Open Data), seen within %g km of the stop' % s['sweep_km'], False, ps))
    if not sess['seen']:
        record_promise(ctx, box, s, make_record(day, s['sweep_stop'], st['name'], '%s-%s' % (s['sweep_start'], s['sweep_end']), None, None, None, None, day_type,
                                                'live (Bus Open Data)', False, ps))


def load_cfg(ctx):
    s = dict(ctx.settings)
    old = ctx.state.load('config')
    for k in ('stops', 'schedule', 'closed', 'route_polyline'):
        if not s.get(k) and old.get(k):
            s[k] = old[k]
    if not s.get('route_stops'):
        s['route_stops'] = ctx.state.load('route_stops').get('stops') or []
    return s


def run(ctx):
    s = load_cfg(ctx)
    need = [k for k in ('operator_ref', 'line_ref', 'stops', 'sweep_stop') if not s.get(k)]
    if need or not s['route_polyline']:
        ctx.log('not configured (needs %s in settings or plugin state)' % ', '.join(need + ([] if s['route_polyline'] else ['route_polyline'])))
        return False
    now = ctx.now
    box = Box(ctx)
    day = now.date()
    sessions = box.load('sessions')
    sessions = {k: v for k, v in sessions.items() if k.startswith(day.isoformat() + '|')}
    why = closed_reason(s['closed'], day)
    rs = rules_today(s, now)
    college = bool(rs and not why and now.weekday() <= 4)
    ok = True
    try:
        if college:
            for r in rs:
                key = '%s|%s|%s' % (day.isoformat(), r['stop'], r['time'])
                u = dt.datetime.combine(day, dt.time.fromisoformat(r['time']), now.tzinfo)
                start, end = u - dt.timedelta(minutes=s['start_before_min']), u + dt.timedelta(minutes=s['shadow_after_min'])
                sess = sessions.get(key)
                if now >= end or (sess and sess['done']):
                    if sess and not sess['recorded']:
                        finish_rule(ctx, box, s, r, sess, now)
                    continue
                if now < start:
                    continue
                if sess is None:
                    sess = sessions[key] = new_session(now)
                    if box.load('pruned').get('day') != day.isoformat():
                        box.prune_shadow((day - dt.timedelta(days=int(s['keep_days']))).isoformat())
                        box.save('pruned', {'day': day.isoformat()})
                try:
                    buses = [b for b in parse(fetch(ctx, s)) if b['dir'] == s['direction']]
                except Exception as e:
                    ctx.log('feed failed: ' + type(e).__name__)
                    ok = False
                    break
                poll_rule(ctx, box, s, r, sess, buses, now)
                if sess['done']:
                    finish_rule(ctx, box, s, r, sess, now)
                break   # one rule at a time, in order, like the old script
        else:
            key = day.isoformat() + '|sweep'
            h0, m0 = (int(x) for x in s['sweep_start'].split(':'))
            h1, m1 = (int(x) for x in s['sweep_end'].split(':'))
            start, end = now.replace(hour=h0, minute=m0, second=0, microsecond=0), now.replace(hour=h1, minute=m1, second=0, microsecond=0)
            sess = sessions.get(key)
            if now >= end:
                if sess and not sess['recorded']:
                    finish_sweep(ctx, box, s, sess, now)
            elif now >= start:
                if sess is None:
                    sess = sessions[key] = new_session(now)
                try:
                    buses = [b for b in parse(fetch(ctx, s)) if b['dir'] == s['direction']]
                    sweep_poll(ctx, s, sess, buses, now)
                except Exception as e:
                    ctx.log('feed failed: ' + type(e).__name__)
                    ok = False
    finally:
        box.save('sessions', sessions)
    flush_webhook(ctx, box, s)
    return ok
