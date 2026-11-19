"""Hearth: the shared household assistant (everyone in the house, no servant role-play). It decides WHAT to say and asks ih_speaker to speak it.
Two jobs, ported from the Home Assistant versions that stay switched on until each cut-over is approved:
  1. the bedtime message when the alarm is armed (or fails to arm);
  2. a welcome from the Picard voice when a person has just arrived home and the kitchen door opens.
Everything outside is injectable for tests: ha (get, calendar_events), speak (callable), now, sleep, log. Nothing here talks to a speaker directly."""
import datetime
import json
import re
import threading
import urllib.request

ENTITY = re.compile(r'^[a-z_]+\.[a-z0-9_]+$')
HHMM = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
ARMED = ('armed_home', 'armed_away', 'armed_night')
DISHWASHER_READY = ('Ready', 'Inactive', 'Finished')
AWAKE_GRACE_S = 300
CHECK_WAIT_S = 12
OFF_PATTERN = r'off work|holiday|annual leave|day off|A/L'
DEFAULT_LINES = [
    'Welcome home, {name}. Make it so.',
    'Ah, {name}. The bridge has been awaiting your return. Engage.',
    '{name} has arrived on board. Tea, earl grey, hot.',
    'Welcome home, {name}. O brave new world, that has such people in it.',
    'Ah, {name}. Be not afeard. The house is full of noises, and all of them are friendly.',
    '{name}, you are home. Our revels now are ended. The kettle, however, is only beginning.',
    'Welcome, {name}. We are such stuff as dreams are made on, and tonight, the sofa is waiting.',
]


def _ents(v):
    return [e.strip() for e in (v or []) if isinstance(e, str) and ENTITY.match(e.strip())] if isinstance(v, list) else []


def _ent(v):
    return v.strip() if isinstance(v, str) and ENTITY.match(v.strip()) else ''


def _pairs(v):
    """['entity=spoken name'] -> [(entity, name)] (bad entries skipped)."""
    out = []
    for x in (v or []) if isinstance(v, list) else []:
        if isinstance(x, str) and '=' in x:
            e, n = (t.strip() for t in x.split('=', 1))
            if ENTITY.match(e) and n:
                out.append((e, n[:60]))
    return out


def _name(v, d=''):
    return v.strip()[:40] if isinstance(v, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,39}', v.strip()) else d


def _hhmm(v, d):
    return v if isinstance(v, str) and HHMM.match(v) else d


def _num(v, lo, hi, d):
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return d


def parse_options(o):
    """The `hearth` option -> a clean config. Disabled (and harmless) unless `enabled` is true. Bad entries are dropped."""
    o = o if isinstance(o, dict) else {}
    pc = o.get('picard') if isinstance(o.get('picard'), dict) else {}
    try:
        re.compile(str(o.get('off_pattern') or OFF_PATTERN))
        off = str(o.get('off_pattern') or OFF_PATTERN)[:200]
    except re.error:
        off = OFF_PATTERN
    return {
        'enabled': o.get('enabled') is True,
        'persona': _name(o.get('persona'), 'hearth').lower(),
        'speaker': _name(o.get('speaker')), 'voice': _name(o.get('voice')),
        'alarm_entity': _ent(o.get('alarm_entity')),
        'bedroom_lamps': _ents(o.get('bedroom_lamps')),
        'locks': _pairs(o.get('locks')), 'doors': _pairs(o.get('doors')),
        'lock_door_pairs': [(a, b) for a, b in _pairs(o.get('lock_door_pairs')) if ENTITY.match(b)],   # 'lock.x=binary_sensor.y'
        'check_door_label': str(o.get('check_door_label') or 'the back door')[:40],
        'lights_downstairs': _ents(o.get('lights_downstairs')), 'lights_outside': _ents(o.get('lights_outside')),
        'dishwasher_state_entity': _ent(o.get('dishwasher_state_entity')), 'dishwasher_unload_entity': _ent(o.get('dishwasher_unload_entity')),
        'forecast_entity': _ent(o.get('forecast_entity')), 'frost_at': _num(o.get('frost_at'), -30, 15, 2.0),
        'workday_calendar': _ent(o.get('workday_calendar')), 'own_calendar': _ent(o.get('own_calendar')),
        'event_calendars': _ents(o.get('event_calendars')), 'wake_up_entity': _ent(o.get('wake_up_entity')), 'off_pattern': off,
        'picard': {
            'enabled': pc.get('enabled') is True,
            'door_entity': _ent(pc.get('door_entity')), 'person_switch': _ent(pc.get('person_switch')),
            'person_name': _name(pc.get('person_name'), 'there'),
            'speaker': _name(pc.get('speaker')), 'voice': _name(pc.get('voice'), 'picard'),
            'home_minutes': _num(pc.get('home_minutes'), 1, 120, 10),
            'window_start': _hhmm(pc.get('window_start'), '07:00'), 'window_end': _hhmm(pc.get('window_end'), '22:00'),
            'min_gap_min': _num(pc.get('min_gap_min'), 1, 1440, 60),
            'lines': [x.strip()[:200] for x in (pc.get('lines') if isinstance(pc.get('lines'), list) else []) if isinstance(x, str) and x.strip()][:20],
        },
    }


def join_and(items):
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ', and ' + items[-1]


def _tmin(s):
    return int(s[:2]) * 60 + int(s[3:])


def make_speaker_client(url, token, opener=None, timeout=200):
    """-> callable(request dict) -> ih_speaker answer. Never raises: a failure comes back as status 'error'."""
    base = str(url or '').strip().rstrip('/')

    def speak(req):
        if not (base.startswith('http://') or base.startswith('https://')) or not token:
            return {'status': 'error', 'reason': 'speaker_url or speaker_token not set'}
        r = urllib.request.Request(base + '/speak', data=json.dumps(req).encode(), method='POST',
                                   headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with (opener or urllib.request.urlopen)(r, timeout=timeout) as resp:
                out = json.loads(resp.read() or b'{}')
            return out if isinstance(out, dict) else {'status': 'error', 'reason': 'bad answer'}
        except Exception as e:
            return {'status': 'error', 'reason': 'speaker call failed: %s' % type(e).__name__}
    return speak


class Hearth:
    def __init__(self, ha, cfg, speak, now, sleep, log, live, store=None, rng=None):
        """live: callable -> bool (add-on live AND the Hearth persona live). store: PersonaStore-like (state(), update_state(fn)) or None."""
        self.ha, self.cfg, self.speak, self.now, self.sleep, self.log = ha, cfg, speak, now, sleep, log
        self.live, self.store, self.rng = live, store, rng
        self.lock = threading.Lock()       # bedtime messages one at a time, in order
        self._mem = {}

    # ---- reading Home Assistant ----
    def _st(self, entity):
        try:
            return self.ha.get('states/' + entity) or {}
        except Exception:
            return {}

    def _state(self, entity):
        return self._st(entity).get('state')

    def _on(self, entity):
        return self._state(entity) == 'on'

    def _age_s(self, st):
        try:
            t = datetime.datetime.fromisoformat(str(st.get('last_changed')))
            n = self.now()
            return (n - (t if t.tzinfo else t.replace(tzinfo=n.tzinfo))).total_seconds()
        except (TypeError, ValueError):
            return None

    # ---- bedtime ----
    def awake(self):
        """Bedroom lamps on now, or off for less than 5 minutes."""
        for e in self.cfg['bedroom_lamps']:
            st = self._st(e)
            if st.get('state') == 'on':
                return True
            age = self._age_s(st)
            if st.get('state') == 'off' and age is not None and age < AWAKE_GRACE_S:
                return True
        return False

    def suspect(self):
        """A locked door whose sensor still reads open (the lock re-sends 'lock' on arming and the sensor can lag): [(lock, door)]."""
        return [(a, b) for a, b in self.cfg['lock_door_pairs'] if self._state(a) == 'locked' and self._on(b)]

    def items(self):
        out = []
        for e, n in self.cfg['locks']:
            if self._state(e) not in ('locked', 'unavailable', 'unknown', None):
                out.append('%s is unlocked' % n)
        for e, n in self.cfg['doors']:
            if self._on(e):
                out.append('%s is open' % n)
        for key, where in (('lights_downstairs', 'downstairs'), ('lights_outside', 'outside')):
            k = sum(1 for e in self.cfg[key] if self._on(e))
            if k:
                out.append('%s still on %s' % ('a light is' if k == 1 else '%d lights are' % k, where))
        ds, ub = self.cfg['dishwasher_state_entity'], self.cfg['dishwasher_unload_entity']
        if ds and ub and str(self._state(ds) or '').split('.')[-1] in DISHWASHER_READY and self._state(ub) == 'off':
            out.append("you haven't scheduled the dishwasher")
        return out

    def frost_low(self):
        """Forecast low for today if it is at or below the frost limit, else None."""
        e = self.cfg['forecast_entity']
        if not e:
            return None
        try:
            low = float((self._st(e).get('attributes', {}).get('daily') or [{}])[0].get('templow'))
        except (TypeError, ValueError, IndexError, AttributeError):
            return None
        return low if low <= self.cfg['frost_at'] else None

    def _day0(self):
        n = self.now()
        return (n if n.hour < 4 else n + datetime.timedelta(days=1)).date()

    def _events(self, entity, day):
        tz = self.now().tzinfo
        start = datetime.datetime.combine(day, datetime.time(0, 0), tz)
        return self.ha.calendar_events(entity, start, start + datetime.timedelta(days=1)) or []

    def tomorrow_line(self):
        """'Tomorrow is a work day...' or the lie-in line with the first event before 12:00. Empty when the calendars cannot be read."""
        c, day0 = self.cfg, self._day0()
        if not c['workday_calendar']:
            return ''
        off = re.compile(c['off_pattern'], re.I)
        try:
            work = any((ev.get('start') or {}).get('date') == day0.isoformat() for ev in self._events(c['workday_calendar'], day0))
            off_day = bool(c['own_calendar']) and any(off.search(str(ev.get('summary') or '')) for ev in self._events(c['own_calendar'], day0))
            first = None
            if not (work and not off_day):
                tz = self.now().tzinfo
                for cal in c['event_calendars']:
                    for ev in self._events(cal, day0):
                        dt = (ev.get('start') or {}).get('dateTime')
                        if not dt or off.search(str(ev.get('summary') or '')):
                            continue
                        t = datetime.datetime.fromisoformat(dt)
                        t = t.replace(tzinfo=tz) if t.tzinfo is None else t.astimezone(tz)
                        if t.hour < 12 and (first is None or t < first[0]):
                            first = (t, str(ev.get('summary') or '').strip())
        except Exception as e:
            self.log('hearth: calendars unreadable (%s)' % type(e).__name__)
            return ''
        if work and not off_day:
            wake = str(self._state(c['wake_up_entity']) or '')[:5] if c['wake_up_entity'] else ''
            return 'Tomorrow is a work day, your wake-up is at %s.' % wake if HHMM.match(wake) else 'Tomorrow is a work day.'
        if first:
            t, name = first
            return 'You can have a lie in, but you need to be up for %s at %d%s %s.' % (name, t.hour % 12 or 12, ':%02d' % t.minute if t.minute else '', 'am' if t.hour < 12 else 'pm')
        return 'You can have a lie in.'

    def build(self, event, checked=False):
        """The spoken text. checked = the 'just checking' two-step was done, so say what the check found."""
        if event == 'failed':
            return 'Arming the alarm failed. Please check the doors and windows.'
        items = self.items()
        if checked:
            label = self.cfg['check_door_label']
            sus_names = {n for e, n in self.cfg['doors'] if e in {b for _, b in self.cfg['lock_door_pairs']}}
            still = [i for i in items if i in ['%s is open' % n for n in sus_names]]
            rest = [i for i in items if i not in still]
            text = 'I checked to see if %s was open, and %s.' % (label, 'it is still open' if still else "it's ok")
            text += ' Everything else is looking good too.' if not rest else ' But %s.' % join_and(rest)
        elif not items:
            text = "All is looking good, I've just armed the alarm."
        else:
            text = "I've armed the alarm, but %s." % join_and(items)
        low = self.frost_low()
        if low is not None:
            d = int(round(low))
            text += " There's a frost tonight, going down to %d %s." % (d, 'degree' if d == 1 else 'degrees')
        tl = self.tomorrow_line()
        return (text + ' ' + tl).strip()

    def _say(self, text, dry, **extra):
        req = {'text': text, 'persona': self.cfg['persona'], 'speaker': self.cfg['speaker'], 'voice': self.cfg['voice'], 'dry_run': dry}
        req.update(extra)
        return self.speak(req)

    def bedtime(self, event, force_dry=False):
        """Alarm armed / failed -> maybe speak. Returns what happened (also in dry mode, where nothing is played)."""
        with self.lock:
            dry = force_dry or not self.live()
            awake = self.awake()
            if event != 'failed' and not awake:
                return {'event': event, 'spoke': False, 'why': 'bedroom is dark: silent'}
            checked = False
            if event != 'failed' and self.suspect():
                t0 = self.now()
                first = self._say('Alarm armed, just checking a few other things.', dry, urgency='normal')
                if not dry and first.get('status') in ('spoken',):
                    checked = True
                    waited = (self.now() - t0).total_seconds()
                    while self.suspect() and waited < CHECK_WAIT_S:
                        self.sleep(1)
                        waited += 1
                elif dry:
                    checked = True
            text = self.build(event, checked)
            res = self._say(text, dry, urgency='alert' if event == 'failed' else 'normal')
            self.log('hearth: bedtime %s %s (%s)' % (event, 'dry' if dry else 'live', res.get('status')))
            return {'event': event, 'spoke': res.get('status') == 'spoken', 'dry': dry, 'checked': checked, 'text': text, 'speaker': res.get('status'), 'reason': res.get('reason', '')}

    # ---- Picard welcome ----
    def _last_welcome(self):
        if self.store is not None:
            try:
                return float(self.store.state().get('hearth_picard_at') or 0)
            except (TypeError, ValueError):
                return 0.0
        return float(self._mem.get('picard_at', 0))

    def _mark_welcome(self, at):
        self._mem['picard_at'] = at
        if self.store is not None:
            self.store.update_state(lambda s: s.__setitem__('hearth_picard_at', at))

    def picard(self, force_dry=False):
        """Door opened. Spoken only if: person home < N minutes, inside the window, not welcomed in the last hour. The speaker skips itself while playing."""
        p = self.cfg['picard']
        if not p['enabled'] or not p['person_switch']:
            return {'spoke': False, 'why': 'off'}
        sw = self._st(p['person_switch'])
        age = self._age_s(sw)
        if sw.get('state') != 'on' or age is None or age >= p['home_minutes'] * 60:
            return {'spoke': False, 'why': 'person did not just arrive'}
        n = self.now()
        nm = n.hour * 60 + n.minute
        if not (_tmin(p['window_start']) <= nm < _tmin(p['window_end'])):
            return {'spoke': False, 'why': 'outside the welcome hours'}
        ts = n.timestamp()
        if ts - self._last_welcome() <= p['min_gap_min'] * 60:
            return {'spoke': False, 'why': 'welcomed less than %d minutes ago' % p['min_gap_min']}
        dry = force_dry or not self.live()
        lines = p['lines'] or DEFAULT_LINES
        line = (lines[int(self.rng() * len(lines)) % len(lines)] if self.rng else lines[ts.__int__() % len(lines)]).replace('{name}', p['person_name'])
        res = self.speak({'text': line, 'persona': self.cfg['persona'], 'speaker': p['speaker'] or self.cfg['speaker'], 'voice': p['voice'],
                          'volume': 'keep', 'skip_if_playing': True, 'dry_run': dry})
        st = res.get('status')
        if st == 'spoken':
            self._mark_welcome(ts)
        elif dry and st == 'dry':
            self._mem['picard_at'] = ts     # dry: remember in memory only, so a dry run does not use up the hour on disk
        self.log('hearth: welcome %s (%s)' % ('dry' if dry else 'live', st))
        return {'spoke': st == 'spoken', 'dry': dry, 'text': line, 'speaker': st, 'reason': res.get('reason', '')}

    # ---- events ----
    def on_state_change(self, data):
        """data of a Home Assistant state_changed event. Starts the work on its own thread (the listener must not block)."""
        e = data.get('entity_id')
        old, new = (data.get('old_state') or {}).get('state'), (data.get('new_state') or {}).get('state')
        if not self.cfg['enabled']:
            return None
        if e and e == self.cfg['alarm_entity'] and old != new:
            if new in ARMED and old in ('arming', 'disarmed'):
                return self._spawn(self.bedtime, 'armed')
            if old == 'arming' and new == 'disarmed':
                return self._spawn(self.bedtime, 'failed')
        if e and e == self.cfg['picard']['door_entity'] and old == 'off' and new == 'on':
            return self._spawn(self.picard)
        return None

    def _spawn(self, fn, *a):
        def run():
            try:
                fn(*a)
            except Exception as ex:
                self.log('hearth: %s failed (%s)' % (fn.__name__, type(ex).__name__))
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def listen(self, stop):
        """Thread body: follow state_changed events; reconnect after a drop. `stop` is a threading.Event."""
        while not stop.is_set():
            try:
                self.ha.subscribe_events('state_changed', self.on_state_change, max_seconds=600)
            except Exception as ex:
                self.log('hearth: event listener dropped (%s), retrying' % type(ex).__name__)
                stop.wait(10)
