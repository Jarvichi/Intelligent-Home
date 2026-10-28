"""car_charge: one evening heads-up by Discord DM about the electric car: it looks at tomorrow's events (with a place) in ONE calendar, works out the
driving distance from home, and says whether tonight's charge covers it. Sends at most one card + one message per evening, inside a send window.
Only the place text of an event goes to the free OpenStreetMap geocoder (Nominatim, cached) and coordinates to the OSRM router; event titles never leave the add-on
except in the DM itself. Runs every 10 minutes; sends once per day.
Every house value comes from settings (JSON in plugins[].settings):
  user_id, token_option      Discord user id to DM; add-on option holding the bot token (default discord_token)
  calendar_entity            the ONE calendar entity to read
  home_zone                  zone entity whose latitude/longitude is home (default zone.home)
  soc_entity, range_entity, charge_state_entity, plug_entity, target_entity     car sensors (battery %, range, charging state, plug lock state, charge target %)
  ttf_entity                 time-to-full sensor (minutes) for the card
  status_entity              optional text status sensor for the card (falls back to charge_state_entity text)
  plug_value                 plug_entity state meaning cable locked in (default CHARGING_CABLE_LOCKED)
  picture_url                optional URL of a car picture for the card thumbnail
  recipient_name, car_name, signoff   words used in messages ("Evening <recipient_name>!", "The <car_name>'s on ...", "- <signoff>", card title "<recipient_name>'s <car_name>")
  send_at "19:30", latest "21:30"   send window (HH:MM)
  min_miles 5, margin 10, factor 1.2, low_soc 25, mi_per_pct 1.4
  skip_regex                 locations matching this are ignored (online meetings, 'home', links ...)
  user_agent                 sent to the geocoder (it asks for one that identifies the app)
  ignore_gates               true = ignore the send window and the 'already sent today' check (manual dry run to see the text now)
State: 'state' = {"sent": {day: "sent"|"none"}, "events": {event key: day}}; 'cache' = geocode and route cache. Same shape as the old script's json files.
The last charge line on the card is read from the car_log plugin's state (if that plugin has run).
Dry: reads everything, logs the message and card it would send; sends nothing and saves nothing (the cache stays in memory)."""
import datetime as dt
import json
import math
import re
import urllib.parse

import plugins.car_log as car_log

NAME = 'car_charge'
SCHEDULE = {'kind': 'every', 'minutes': 10}
DEFAULTS = {'user_id': '', 'token_option': 'discord_token', 'calendar_entity': '', 'home_zone': 'zone.home',
            'soc_entity': '', 'range_entity': '', 'charge_state_entity': '', 'plug_entity': '', 'target_entity': '', 'ttf_entity': '', 'status_entity': '',
            'plug_value': 'CHARGING_CABLE_LOCKED', 'picture_url': '', 'recipient_name': '', 'car_name': 'car', 'signoff': '',
            'send_at': '19:30', 'latest': '21:30', 'min_miles': 5, 'margin': 10, 'factor': 1.2, 'low_soc': 25, 'mi_per_pct': 1.4,
            'skip_regex': r'(teams|zoom|online|virtual|webex|meet\.google|https?://|remote|^home$|^tbc$|^n/?a$)',
            'user_agent': 'ih-jobs-car-charge/1.0 (private home automation)', 'ignore_gates': False}
PLUGGED_STATES = ('CHARGING', 'CHARGINGPAUSED', 'CHARGING_PAUSED', 'WAITING_FOR_CHARGING', 'FINISHED', 'COMPLETE')
GEO = 'https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=gb&q='
ROUTE = 'https://router.project-osrm.org/route/v1/driving/%f,%f;%f,%f?overview=false'


def _hm(text):
    h, m = str(text).split(':')
    return int(h), int(m)


# ---- geography (pure; fetchers are passed in) ----
def hav(a, b):
    p = math.pi / 180
    x = math.sin((b[0] - a[0]) * p / 2) ** 2 + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin((b[1] - a[1]) * p / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(x))


class Geo:
    """Geocoder + router with a cache dict (saved by the caller). http = ctx.http. 1.1 s gap per host (the geocoder's rule)."""
    def __init__(self, http, cache, ua, log):
        self.http, self.cache, self.ua, self.log, self.dirty = http, cache, ua, log, False

    def _json(self, url):
        return self.http.get_json(url, headers={'User-Agent': self.ua}, min_interval=1.1)

    def geocode1(self, place):
        k = 'geo:' + place.strip().lower()
        if k not in self.cache:
            try:
                r = self._json(GEO + urllib.parse.quote(place))
                self.cache[k] = [float(r[0]['lat']), float(r[0]['lon'])] if r else None
            except Exception as e:
                self.log('geocode failed: ' + type(e).__name__)
                return None   # not cached: try again next time
            self.dirty = True
        return self.cache[k]

    def geocode(self, place):
        """Full string first; if not found, the UK postcode, then the address with leading parts dropped (venue names often aren't in OSM)."""
        tries = [place]
        pc = re.search(r'\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b', place.upper())
        if pc:
            tries.append(pc.group(0))
        parts = [x.strip() for x in place.split(',')]
        tries += [', '.join(parts[i:]) for i in range(1, len(parts) - 1)]
        for t in tries:
            ll = self.geocode1(t)
            if ll:
                return ll
        return None

    def miles(self, a, b):
        k = 'route:%.4f,%.4f:%.4f,%.4f' % (a[0], a[1], b[0], b[1])
        if k not in self.cache:
            try:
                r = self._json(ROUTE % (a[1], a[0], b[1], b[0]))
                self.cache[k] = r['routes'][0]['distance'] / 1609.34
            except Exception as e:
                self.log('route failed, straight-line x1.3: ' + type(e).__name__)
                return hav(a, b) * 1.3
            self.dirty = True
        return self.cache[k]


# ---- car ----
def car_state(s, v):
    """v: {'soc','range','plug','charge','target'} raw state strings (or None). -> dict or None when the battery level is unknown."""
    num = lambda x: float(x) if re.match(r'^-?\d+(\.\d+)?$', str(x)) else None
    soc = num(v.get('soc'))
    if soc is None:
        return None
    rng = num(v.get('range')) or soc * s['mi_per_pct']
    tgt = num(v.get('target')) or 80
    plug = str(v.get('plug') or '').upper()
    cs = str(v.get('charge') or '').upper()
    plugged = plug == str(s['plug_value']).upper() or cs in PLUGGED_STATES
    return dict(soc=soc, range=rng, target=tgt, plugged=plugged, rng_at_target=rng * max(tgt, soc) / soc if soc else rng)


# ---- calendar ----
def short(loc):
    p = re.split(r'[,\n]', loc)[0].strip()
    return (p if not re.match(r'^\d', p) and len(p) > 2 else ', '.join(x.strip() for x in loc.split(',')[:2]))[:40]


def placed_events(s, evs, t0, t1, tz):
    """[(start_datetime_local, location, key)] with a place, between t0 and t1, from raw HA calendar events."""
    skip = re.compile(s['skip_regex'], re.I)
    out = []
    for e in evs:
        loc = (e.get('location') or '').strip()
        if not loc or skip.search(loc):
            continue
        st = e['start']
        if 'dateTime' in st:
            when = dt.datetime.fromisoformat(st['dateTime']).astimezone(tz)
        else:
            when = dt.datetime.fromisoformat(st['date']).astimezone(tz).replace(hour=9)
        if not (t0 <= when < t1):
            continue
        out.append((when, loc, '%s|%s' % (e.get('uid') or loc, when.isoformat())))
    return sorted(out)


# ---- messages ----
def evening(s, day, cars, home_ll, events, geo, seen=(), trace=None):
    """Decide the message for the evening of `day`. events = placed_events(...) for day 19:30 .. day+2. Returns (text or None, [event keys])"""
    trips = {}
    for when, loc, key in events:
        trips.setdefault(when.date(), []).append((loc, key))
    worst = None
    for d, evs in trips.items():
        pts = []
        for loc, key in evs:
            ll = geo.geocode(loc)
            if ll:
                pts.append((short(loc), ll, key))
        if not pts:
            continue
        legs = []
        prev = home_ll
        for n, ll, k in pts:
            legs.append(geo.miles(prev, ll))
            prev = ll
        legs.append(geo.miles(prev, home_ll))
        total = sum(legs)
        furthest = max(geo.miles(home_ll, ll) for _, ll, _ in pts)
        if trace:
            trace('[trip %s] %s: %.0f mi round trip (furthest %.0f), need %.0f' % (d, ' + '.join(n for n, _, _ in pts), total, furthest, total * s['factor'] + s['margin']))
        if furthest < s['min_miles']:
            continue
        item = dict(day=d, need=total * s['factor'] + s['margin'], total=total, names=[n for n, _, _ in pts], keys=[k for _, _, k in pts], furthest=furthest)
        if not worst or item['need'] > worst['need']:
            worst = item
    low = cars['soc'] < s['low_soc'] and not cars['plugged']
    r = round(cars['range'])
    soc = round(cars['soc'])
    car = s['car_name']
    msg = None
    keys = []
    if worst and not all(k in seen for k in worst['keys']):
        have = cars['rng_at_target'] if cars['plugged'] else cars['range']
        if worst['need'] > have:
            tom = 'tomorrow' if worst['day'] == day + dt.timedelta(days=1) else 'tonight'
            names = worst['names']
            place = names[0] if len(set(names)) == 1 else ' and '.join(dict.fromkeys(names))
            trip = f"about {round(worst['total'])} miles in all" if len(names) > 1 or worst['total'] > worst['furthest'] * 2.2 else f"about {round(worst['total'])} miles there and back"
            if cars['plugged']:
                tail = f"It's plugged in, but only charges to {round(cars['target'])}% (roughly {round(cars['rng_at_target'])} miles), so it may be worth topping that up."
            else:
                tail = f"The {car}'s on {soc}% (about {r} miles), so best plug it in tonight."
            msg = f"\U0001f50c You're off to {place} {tom} ({trip}). {tail}"
            keys = worst['keys']
    if msg is None and low:
        msg = f"\U0001f50b The {car}'s down to {soc}% (about {r} miles). Worth plugging in tonight."
    return (msg + f"\n\n- {s['signoff']}" if msg else None), keys


def brief(s, evs, cars, home_ll, geo, tz):
    """Every evening with something on tomorrow: list tomorrow's calendar events (time, title, place + round-trip miles) and say whether the charge covers it.
    evs = raw HA calendar events of tomorrow."""
    if not evs:
        return None
    skip = re.compile(s['skip_regex'], re.I)
    rows, pts = [], []
    for e in sorted(evs, key=lambda e: e['start'].get('dateTime') or e['start'].get('date')):
        st = e['start']
        tm = dt.datetime.fromisoformat(st['dateTime']).astimezone(tz).strftime('%H:%M') if 'dateTime' in st else 'all day'
        loc = (e.get('location') or '').strip()
        extra = ''
        if loc and not skip.search(loc):
            ll = geo.geocode(loc)
            if ll:
                m = geo.miles(home_ll, ll)
                if m >= s['min_miles']:
                    pts.append(ll)
                    extra = f" at {short(loc)} (about {round(m * 2)} miles there and back)"
                else:
                    extra = f" ({short(loc)}, local)"
            else:
                extra = f" ({short(loc)})"
        rows.append(f"• **{tm}** {e.get('summary', '').strip()}{extra}")
    total = 0
    prev = home_ll
    for ll in pts:
        total += geo.miles(prev, ll)
        prev = ll
    if pts:
        total += geo.miles(prev, home_ll)
    need = total * s['factor'] + s['margin'] if pts else 0
    have = cars['rng_at_target'] if cars['plugged'] else cars['range']
    soc, r = round(cars['soc']), round(cars['range'])
    car = s['car_name']
    if cars['plugged']:
        car_l = f"The {car}'s on {soc}% and plugged in, charging to {round(cars['target'])}% overnight (about {round(cars['rng_at_target'])} miles)."
    else:
        car_l = f"The {car}'s on {soc}% (about {r} miles)."
    if pts and need > have:
        verdict = "That's cutting it fine for tomorrow, so best " + ("top it up beyond the usual target \U0001f50c" if cars['plugged'] else "plug it in tonight \U0001f50c")
    elif not cars['plugged']:
        verdict = ("It's getting low, so " if cars['soc'] < s['low_soc'] else "It's not plugged in, so ") + "please plug it in tonight \U0001f50c"
    elif pts and cars['plugged']:
        verdict = "That's plenty for tomorrow, nothing to do \U0001f44d"
    elif pts:
        verdict = "Plenty for tomorrow \U0001f44d"
    else:
        verdict = "You're all set \U0001f44d"
    return f"Evening {s['recipient_name']}! Tomorrow you've got:\n" + "\n".join(rows) + f"\n\n{car_l} {verdict}\n\n- {s['signoff']}"


def card_embed(s, v, plugged, last_charge):
    """The car card (embed dict). v: raw state strings {'soc','target','range','ttf','status','charge_state'} (None when missing)."""
    soc = float(v['soc'])
    tgt, rng, ttf = v.get('target'), v.get('range'), v.get('ttf')
    status = v.get('status') or str(v.get('charge_state') or '').replace('_', ' ').lower().capitalize()
    try:
        ttf_s = '%dh %02dm' % divmod(int(float(ttf)), 60) if float(ttf) > 0 else '-'
    except (TypeError, ValueError):
        ttf_s = '-'
    emb = {'title': f"\U0001f50b {s['recipient_name']}'s {s['car_name']} · {round(soc)}%", 'color': 3066993 if soc >= 50 else 15105570 if soc >= 25 else 15158332,
           'fields': [{'name': 'Charge', 'value': f"{round(soc)}% → {tgt}%" if plugged else f"{round(soc)}%", 'inline': True},
                      {'name': 'Range', 'value': f"about {round(float(rng))} mi" if re.match(r'^[\d.]+$', str(rng)) else '-', 'inline': True},
                      {'name': 'Time to full' if plugged else 'Cable', 'value': ttf_s if plugged else 'not plugged in', 'inline': True},
                      {'name': 'Status', 'value': status, 'inline': False}] + ([{'name': 'Last charge', 'value': last_charge, 'inline': False}] if last_charge else []),
           'footer': {'text': f"{s['signoff']} · live from the car"}}
    return emb


# ---- run ----
def run(ctx):
    s = ctx.settings
    need = ('user_id', 'calendar_entity', 'soc_entity', 'range_entity', 'charge_state_entity', 'plug_entity', 'target_entity', 'recipient_name', 'signoff')
    if not all(s[k] for k in need):
        ctx.log('not configured (needs %s in settings)' % ', '.join(need))
        return False
    now, tz = ctx.now, ctx.now.tzinfo
    hm = (now.hour, now.minute)
    gates = not s['ignore_gates']
    if gates and not (_hm(s['send_at']) <= hm <= _hm(s['latest'])):
        return True
    st = ctx.state.load('state')
    st.setdefault('sent', {})
    st.setdefault('events', {})
    key = now.date().isoformat()
    if gates and key in st['sent']:
        return True
    g = lambda k: ctx.ha.state(s[k]) if s.get(k) else None
    cars = car_state(s, {'soc': g('soc_entity'), 'range': g('range_entity'), 'plug': g('plug_entity'), 'charge': g('charge_state_entity'), 'target': g('target_entity')})
    if not cars:
        ctx.log('car state unavailable')
        return True
    try:
        a = ctx.ha.get('states/' + s['home_zone'])['attributes']
        home_ll = [a['latitude'], a['longitude']]
    except Exception as e:
        ctx.log('home location unavailable: ' + type(e).__name__)
        return False
    cache = ctx.state.load('cache')
    geo = Geo(ctx.http, cache, s['user_agent'], ctx.log)
    trace = ctx.log if ctx.dry else None

    def events_raw(t0, t1):
        try:
            return ctx.ha.calendar_events(s['calendar_entity'], t0, t1)
        except Exception as e:
            ctx.log('calendar failed: ' + type(e).__name__)
            return None
    day = now.date()
    t0 = dt.datetime.combine(day, dt.time(19, 30), tz)
    t1 = dt.datetime.combine(day + dt.timedelta(days=2), dt.time(0, 0), tz)
    raw = events_raw(t0, t1)
    msg, keys = evening(s, day, cars, home_ll, placed_events(s, raw or [], t0, t1, tz) if raw is not None else [], geo, st['events'], trace)
    b0 = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 0), tz)
    rawb = events_raw(b0, b0 + dt.timedelta(days=1))
    b = brief(s, rawb, cars, home_ll, geo, tz) if rawb else None   # the daily briefing wins when there is something on tomorrow
    if b:
        msg = b
    if geo.dirty and not ctx.dry:
        ctx.state.save('cache', cache)
    if not msg:
        if ctx.dry:
            ctx.log('[DRY] nothing to say tonight')
            return True
        st['sent'][key] = 'none'
        _prune(st, day)
        ctx.state.save('state', st)
        return True
    vals = {k: g(e) for k, e in (('soc', 'soc_entity'), ('target', 'target_entity'), ('range', 'range_entity'), ('ttf', 'ttf_entity'), ('status', 'status_entity'),
                                 ('charge_state', 'charge_state_entity'))}
    plugged = (g('plug_entity') or '').upper() == str(s['plug_value']).upper()
    try:
        last = car_log.last_line(ctx.sibling_state('car_log').load('charges', {'sessions': [], 'cur': None})) if ctx.data else ''
    except Exception:
        last = ''
    try:
        emb = card_embed(s, vals, plugged, last)
    except (TypeError, ValueError):
        ctx.log('card data unavailable')
        return True
    file = None
    if s['picture_url']:
        if ctx.dry:
            ctx.log('[DRY] would attach the picture from picture_url')
            emb['thumbnail'] = {'url': 'attachment://car_card.png'}
        else:
            try:
                file = ('car_card.png', ctx.http.get_bytes(s['picture_url'], timeout=30))
                emb['thumbnail'] = {'url': 'attachment://car_card.png'}
            except Exception as e:
                ctx.log('picture fetch failed (%s): card without picture' % type(e).__name__)
    body = msg.strip()
    if ctx.dry:
        ctx.discord.dm(s['user_id'], '', token_option=s['token_option'], embeds=[emb], file=('car_card.png', b'') if file is None and s['picture_url'] else None)
        ctx.discord.dm(s['user_id'], body, token_option=s['token_option'])
        return True
    # the card holds only car data; the message goes below it as a second plain DM
    ctx.discord.dm(s['user_id'], '', token_option=s['token_option'], embeds=[emb], file=file)
    ctx.discord.dm(s['user_id'], body, token_option=s['token_option'])
    st['sent'][key] = 'sent'
    for k in keys:
        st['events'][k] = key
    _prune(st, day)
    ctx.state.save('state', st)
    ctx.log('sent card and message')
    return True


def _prune(st, day):
    keep = (day - dt.timedelta(days=30)).isoformat()
    st['sent'] = {k: v for k, v in st['sent'].items() if k >= keep}
    st['events'] = {k: v for k, v in st['events'].items() if v >= keep}
