"""Cook engine (ih_cooking 0.2): port of the host cook.py + cooklib.py + plan.py + done/ack/tick/delay + the three oven scripts.
stdlib only. Everything that touches the house goes through four small objects so tests can fake them:
  ha       .call(path, data=None) -> parsed JSON (GET when data is None, POST otherwise); raises on error
  discord  .send(channel_id, text=None, embed=None)   .dm(user_id, text=None, embed=None)
  clocks   now() -> datetime, ts() -> epoch seconds, sleep(s)
DRY by default: in dry mode every write (HA service, HA state, Discord, voice, oven) is only logged as 'would ...'.
LIVE needs cook_mode=live AND the guard (see Engine.guard): it refuses while another controller's heartbeat is fresh."""
import datetime, json, os, re, threading, time, urllib.request, urllib.error
from ihlib.ha import HA   # shared library: shipped as app/ihlib

LIVE_PHRASE = 'I-AM-THE-ONLY-COOK-CONTROLLER'
RK = 'ro' + 'b_'          # recipe keys written by the older host scripts (<RK>minutes, <RK>note, <RK>nudges)
RUN = 'BSH.Common.EnumType.OperationState.Run'


# ---------------------------------------------------------------- pure helpers (verbatim from cooklib.py)
def started(plan, st, gate):
    """True if work is clearly under way even though the gate's DONE was not tapped: any other step ticked, or any prep item ticked."""
    gk = (gate['at'] + ' ' + gate['text']) if gate else None
    auto = {x['at'] + ' ' + x['text'] for x in plan.get('steps', []) if x.get('auto')}
    other = [k for k in st.get('done', []) if k != gk and k not in auto]
    if other: return True
    return any(x.get('done') for x in plan.get('prep', []))


def gate_status(plan, st, hm, minutes_between):
    """-> (gate_open, lag_minutes, released_reason). See host cooklib.gate_status."""
    gate = next((x for x in plan.get('steps', []) if x.get('gate')), None)
    if not gate: return False, 0, None
    key = gate['at'] + ' ' + gate['text']
    if key in st.get('done', []): return False, 0, None
    if started(plan, st, gate): return False, 0, 'work started (later step or prep ticked)'
    lag = minutes_between(gate['at'], hm) if gate['at'] < hm else 0
    return True, lag, None


def speak_with_music(call, state, speak, group, player, text, log, sleep=time.sleep, now=time.time, marker=None, retries=3, memory=300):
    """Pause the group's music, speak, resume. Resume is unconditional (finally). See host cooklib.speak_with_music."""
    mem = {}
    if marker:
        try: mem = json.load(open(marker))
        except Exception: mem = {}
    t = now()
    for p in group:
        if state(p).get('state') == 'playing': mem[p] = t
    was = [p for p in group if t - mem.get(p, 0) <= memory]
    if marker:
        try: json.dump(mem, open(marker, 'w'))
        except Exception: pass
    try:
        for p in was:
            try: call('/services/media_player/media_pause', {'entity_id': p})
            except Exception as e: log('pause failed ' + p + ' ' + str(e))
        if was: sleep(2); log('music paused for voice: ' + ', '.join(was))
        speak(text)
    except Exception as e: log('voice failed ' + str(e))
    finally:
        for p in was:
            for i in range(retries):
                try: call('/services/media_player/media_play', {'entity_id': p})
                except Exception as e: log('resume failed ' + p + ' ' + str(e))
                sleep(2 if i < retries - 1 else 0)
                if state(p).get('state') == 'playing': mem[p] = now(); break
            else: log('resume not confirmed ' + p)
        if marker:
            try: json.dump(mem, open(marker, 'w'))
            except Exception: pass
        if was: log('music resume sent')


def shift_step(plan, want, minutes, now_dt):
    """Move the step whose 'at text' key == want by `minutes` (+5 / -5 buttons). Returns (old, new, text) or None. See host cooklib.shift_step."""
    for s in plan['steps']:
        if s['at'] + ' ' + s['text'] != want: continue
        old = s['at']; h, m = int(old[:2]), int(old[3:5])
        t = h * 60 + m + minutes
        if minutes < 0: t = max(t, min(h * 60 + m, now_dt.hour * 60 + now_dt.minute))
        t = max(0, min(23 * 60 + 59, t))
        s['at'] = '%02d:%02d' % divmod(t, 60)
        plan['steps'].sort(key=lambda x: x['at'])
        return old, s['at'], s['text']
    plan['steps'].sort(key=lambda x: x['at'])
    return None


def build_plan(r, name, serve, today=None, child='Child'):
    """Recipe -> cooking plan (port of host plan.py). Steps are minutes BEFORE serving. Only the HH:MM of `today` matters."""
    today = today or datetime.date.today()
    t0 = datetime.datetime.combine(today, datetime.time.fromisoformat(serve))
    steps = []
    for s in r['steps']:
        d = {'at': (t0 - datetime.timedelta(minutes=s['before'])).strftime('%H:%M'), 'text': s['text'], 'short': s.get('short', s['text'][:30])}
        if s.get('anchor'): d['anchor'] = True
        if isinstance(s.get('oven'), dict): d['oven'] = s['oven']          # new in 0.2: {"action": "start|set|stop", "temp": 200}
        steps.append(d)
    if r.get(RK + 'minutes', 15):
        steps.append({'at': (t0 - datetime.timedelta(minutes=r.get(RK + 'minutes', 15))).strftime('%H:%M'), 'text': child + ' messaged: food in 15 minutes',
                      'short': child + ' messaged (automatic)', 'auto': True, 'silent': True})
    ing = [x for x in r.get('shopping', []) if str(x).strip()]
    first = min((s['at'] for s in steps if not s.get('auto')), default=serve)
    first = (datetime.datetime.combine(today, datetime.time.fromisoformat(first)) - datetime.timedelta(minutes=1)).strftime('%H:%M')
    steps.append({'at': first, 'gate': True, 'short': 'Get everything out', 'items': ing + [x for x in r.get('equipment', []) if str(x).strip()],
                  'text': ('Get the ingredients out: ' + ', '.join(ing) + '.') if ing else 'Get out everything you need for this recipe.'})
    steps.sort(key=lambda s: (s['at'], not s.get('gate')))
    return {'title': r.get('title', name), 'eat': serve, 'oven_target': r.get('oven'), 'voice': True, RK + 'minutes': r.get(RK + 'minutes', 15),
            'recipe': name, RK + 'note': r.get(RK + 'note', ''), RK + 'nudges': r.get(RK + 'nudges', []), 'steps': steps,
            'prep': [{'text': p, 'done': False} for p in r.get('prep', [])]}


# ---------------------------------------------------------------- real clients
class HAClient:
    def __init__(self, url, token, opener=None):
        self.url, self.token, self.opener = url.rstrip('/'), token, opener or urllib.request.urlopen
        self._ha = HA(token=token, opener=self.opener, rest_base=self.url + '/api/')

    def call(self, path, data=None):
        """GET when data is None, POST (JSON body) otherwise; 30 s timeout; raises urllib errors like before."""
        return self._ha.get(path) if data is None else self._ha.post(path, data)


class DiscordClient:
    API = 'https://discord.com/api/v10'

    def __init__(self, token, opener=None):
        self.token, self.opener, self.dm_cache = token, opener or urllib.request.urlopen, {}

    def _post(self, path, body):
        req = urllib.request.Request(self.API + path, data=json.dumps(body).encode(), method='POST',
                                     headers={'Authorization': 'Bot ' + self.token, 'Content-Type': 'application/json', 'User-Agent': 'DiscordBot (ih_cooking, 0.2)'})
        with self.opener(req, timeout=20) as x:
            return json.loads(x.read() or b'null')

    def send(self, channel_id, text=None, embed=None):
        body = {}
        if text: body['content'] = text[:1900]
        if embed: body['embeds'] = [embed]
        return self._post('/channels/%s/messages' % channel_id, body)

    def dm(self, user_id, text=None, embed=None):
        if user_id not in self.dm_cache:
            self.dm_cache[user_id] = self._post('/users/@me/channels', {'recipient_id': str(user_id)})['id']
        return self.send(self.dm_cache[user_id], text, embed)


def parse_ts(s):
    """HA last_updated string -> epoch seconds, or None."""
    try:
        return datetime.datetime.fromisoformat(str(s).replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


# ---------------------------------------------------------------- the engine
class Engine:
    def __init__(self, cfg, data_dir, ha=None, discord=None, now=datetime.datetime.now, ts=time.time, sleep=time.sleep, log=None, spawn=None):
        self.cfg, self.dir = cfg, data_dir
        os.makedirs(self.dir, exist_ok=True)
        self.ha, self.discord = ha, discord
        self.now, self.ts, self._sleep = now, ts, sleep
        self._log = log or (lambda m: None)
        self.spawn = spawn or (lambda f: threading.Thread(target=f, daemon=True).start())
        self.lock = threading.RLock()
        self.start_lock = threading.RLock()
        self.thread = None
        self.stop_flag = threading.Event()
        self._guard_cache = (0, False, 'not checked')
        self._demoted = False
        self._pub_sig = None
        p = cfg.get('oven_prefix', '')
        self.OVEN = 'sensor.%scooking_oven_status_currentcavitytemperature' % p
        self.OVEN_POWER = 'sensor.%sbsh_common_setting_powerstate' % p
        self.OVEN_SET = 'sensor.%scooking_oven_option_setpointtemperature' % p
        self.OVEN_OP = 'sensor.%sbsh_common_status_operationstate' % p
        self.OVEN_REMOTE = 'binary_sensor.%sbsh_common_status_remotecontrolstartallowed' % p
        self.PLAYER = cfg.get('player_entity', '')
        self.GROUP = tuple(cfg.get('player_group') or ([self.PLAYER] if self.PLAYER else []))

    # --- files
    def p(self, name): return os.path.join(self.dir, name)

    def log(self, m):
        line = time.strftime('%H:%M:%S ') + m
        try:
            f = self.p('cook.log')
            if os.path.exists(f) and os.path.getsize(f) > 300000:
                os.replace(f, f + '.1')
            open(f, 'a').write(line + '\n')
        except OSError:
            pass
        self._log(m)

    def load(self, name, default):
        try:
            with open(self.p(name)) as f: return json.load(f)
        except Exception: return default

    def dump(self, name, obj, **kw):
        tmp = self.p(name + '.tmp')
        with open(tmp, 'w') as f: json.dump(obj, f, **kw)
        os.replace(tmp, self.p(name))

    # --- the guard (hard rule: never two live controllers)
    def guard(self, force=False):
        """-> (ok, reason). Live control is allowed only if ALL hold:
        cook_mode == live; cook_confirm == LIVE_PHRASE; no /data/cook/STOP_LIVE file; the 'other controller' heartbeat is configured
        (other_controller_entity) or explicitly declared absent (other_controller_none) and, if configured, is readable and NOT fresh
        (older than other_controller_max_age_s); the heartbeat entity differs from our own plan sensor. Fails closed on any doubt.
        Cached for 15 s unless force."""
        t = self.ts()
        if not force and t - self._guard_cache[0] < 15:
            return self._guard_cache[1], self._guard_cache[2]
        ok, why = self._guard()
        self._guard_cache = (t, ok, why)
        return ok, why

    def _guard(self):
        c = self.cfg
        if c.get('mode') != 'live': return False, 'cook_mode is %s' % c.get('mode')
        if c.get('confirm') != LIVE_PHRASE: return False, 'cook_confirm is not set to the live phrase'
        if os.path.exists(self.p('STOP_LIVE')): return False, 'STOP_LIVE file present'
        if self.ha is None: return False, 'no Home Assistant client (ha_token not set)'
        ent = c.get('other_entity')
        if not ent:
            return (True, 'no other controller declared (other_controller_none)') if c.get('other_none') else (False, 'other_controller_entity not set (and other_controller_none is off)')
        if ent == c.get('plan_sensor'): return False, 'other_controller_entity must not be our own plan sensor'
        try:
            s = self.ha.call('/states/' + ent)
        except urllib.error.HTTPError as e:
            return False, 'heartbeat entity not readable (HTTP %s): the host daemon must publish it first' % e.code
        except Exception as e:
            return False, 'heartbeat entity not readable (%s)' % type(e).__name__
        lu = parse_ts((s or {}).get('last_updated'))
        if lu is None: return False, 'heartbeat has no last_updated'
        age = self.ts() - lu
        if age < float(c.get('other_max_age', 120)):
            return False, 'other controller is alive (heartbeat %ds old)' % max(0, int(age))
        return True, 'other controller silent for %ds' % int(age)

    @property
    def live(self):
        ok, why = self.guard()
        if self.cfg.get('mode') == 'live' and not ok and not self._demoted and self.running():
            self._demoted = True
            self.log('GUARD: live control refused while running (%s): staying dry' % why)
        if ok: self._demoted = False
        return ok

    # --- wrapped I/O (dry-aware)
    def call(self, path, data=None):
        if data is None:
            if self.ha is None: raise RuntimeError('no HA')
            return self.ha.call(path)
        if not self.live:
            self.log('would call %s %s' % (path, json.dumps(data, ensure_ascii=False)[:300]))
            return {}
        return self.ha.call(path, data)

    def state(self, e):
        try: return self.call('/states/' + e) or {}
        except Exception: return {}

    def sleep(self, s):
        if self.live: self._sleep(s)

    def discord_send(self, channel, text=None, embed=None):
        if not channel: self.log('discord: no channel set, message dropped'); return
        if not self.live or self.discord is None:
            self.log('would post to discord %s: %s' % (channel, (text or json.dumps(embed, ensure_ascii=False))[:200])); return
        self.discord.send(channel, text, embed)

    def discord_dm(self, user, text=None, embed=None):
        if not user: self.log('discord: no DM target set, message dropped'); return
        if not self.live or self.discord is None:
            self.log('would DM %s: %s' % (user, (text or json.dumps(embed, ensure_ascii=False))[:200])); return
        self.discord.dm(user, text, embed)

    # --- voice
    def _speak_raw(self, t):
        v = self.state(self.PLAYER).get('attributes', {}).get('volume_level')
        self.call('/services/media_player/volume_set', {'entity_id': self.PLAYER, 'volume_level': 0.5})
        try:
            self.call('/services/tts/speak', {'entity_id': self.cfg.get('tts_entity') or 'tts.google_translate_en_com', 'media_player_entity_id': self.PLAYER, 'message': t, 'language': 'en-uk'})
            self.sleep(max(4, len(t) // 12 + 2))
        finally:
            if isinstance(v, (int, float)):
                try: self.call('/services/media_player/volume_set', {'entity_id': self.PLAYER, 'volume_level': v})
                except Exception as e: self.log('volume restore failed ' + str(e))

    def voice(self, text):
        if not self.PLAYER: return
        if not self.live:
            self.log('would say on %s: %s' % (self.PLAYER, text)); return
        speak_with_music(self.call, self.state, self._speak_raw, self.GROUP, self.PLAYER, text, self.log, sleep=self.sleep, now=self.ts, marker=self.p('music.json'))

    def tell(self, text, voice):
        try: self.discord_send(self.cfg.get('channel'), '🍳 ' + text)
        except Exception as e: self.log('discord failed ' + str(e))
        if voice: self.voice(text)
        self.log('told: ' + text)

    def say_list(self, intro, items, outro, voice):
        try: self.discord_send(self.cfg.get('channel'), '🍳 ' + intro + '\n' + '\n'.join('- ' + i for i in items) + '\n' + outro)
        except Exception as e: self.log('discord failed ' + str(e))

        def run():
            try:
                v = self.state(self.PLAYER).get('attributes', {}).get('volume_level')
                self.call('/services/media_player/volume_set', {'entity_id': self.PLAYER, 'volume_level': 0.5})
                for i, t in enumerate([intro] + items + [outro]):
                    self.call('/services/tts/speak', {'entity_id': self.cfg.get('tts_entity') or 'tts.google_translate_en_com', 'media_player_entity_id': self.PLAYER, 'message': t, 'language': 'en-uk'})
                    self.sleep(max(3, len(t) // 12 + 2) + (4 if 0 < i <= len(items) else 0))
                if isinstance(v, (int, float)): self.call('/services/media_player/volume_set', {'entity_id': self.PLAYER, 'volume_level': v})
            except Exception as e: self.log('voice failed ' + str(e))
        if voice and self.PLAYER: self.spawn(run)
        self.log('told list: ' + intro + ' ' + ', '.join(items))

    # --- oven (port of oven_remote_start / oven_set_temp / oven_stop). Each returns True / False / 'dry'.
    def _svc(self, service, data, domain='home_connect_alt'):
        """-> (ok, message). A blocked oven answers with an HTTP error."""
        try:
            self.ha.call('/services/%s/%s' % (domain, service), data)
            return True, ''
        except urllib.error.HTTPError as e:
            return False, 'HTTP %s' % e.code
        except Exception as e:
            return False, type(e).__name__

    def _signpost(self, text): self.voice(text)

    def _shout(self, text):
        """Retried for over a minute with no answer: HomePod (even if voice is off) + DM to the owner."""
        try: self.voice(text)
        except Exception as e: self.log('shout voice failed ' + str(e))
        try: self.discord_dm(self.cfg.get('owner_dm'), '🔥 ' + text)
        except Exception as e: self.log('shout dm failed ' + str(e))

    def _opstate(self):
        return str(self.state(self.OVEN_OP).get('state', ''))

    def oven_start(self, temp=220, prog='HotAir', mins=60):
        if not self.live:
            self.log('would set oven: start %s at %s C for %s min (dry: no oven call made)' % (prog, temp, mins)); return 'dry'
        dev = self.cfg.get('oven_device')
        body = {'device_id': dev, 'program_key': 'Cooking.Oven.Program.HeatingMode.%s' % prog, 'validate': False,
                'options': [{'key': 'Cooking.Oven.Option.SetpointTemperature', 'value': temp}, {'key': 'BSH.Common.Option.Duration', 'value': mins * 60},
                            {'key': 'Cooking.Oven.Option.FastPreHeat', 'value': True}]}
        self._signpost('Turning the oven on to %s degrees now.' % temp)
        ok, msg = self._svc('start_program', body)
        for _ in range(15):          # the oven blocks remote commands for ~1 min after anyone touches it: keep retrying
            if ok: break
            self.log('oven blocked, retrying: ' + msg); self.sleep(20)
            ok, msg = self._svc('start_program', body)
        t_start = self.ts(); shouted = False
        for i in range(20):          # "Always retry turning oven on and off - it's vital": re-send until it reports Run
            self.sleep(20)
            if self._opstate() == RUN:
                self.log('oven reports Run: STARTED'); return True
            self.log('oven not running yet, re-sending start %d' % (i + 1))
            if not shouted and self.ts() - t_start > 60:
                self._shout('The oven has not started yet. I am still trying. Please check the oven knob is on remote start.'); shouted = True
            self._svc('start_program', body)
        self._shout('I could not start the oven. Please switch it on by hand.')
        self.log('oven FAILED: never reported Run'); return False

    def oven_set_temp(self, want, max_min=5):
        if not self.live:
            self.log('would set oven: temperature %s C (dry: no oven call made)' % want); return 'dry'
        if 'Run' not in self._opstate():
            self.log('oven is not running: cannot change temperature'); return False
        self._signpost('Changing the oven to %s degrees now.' % want)

        def cavity():
            try: return float(self.state(self.OVEN).get('state'))
            except (TypeError, ValueError): return None
        t0 = self.ts(); end = t0 + 60 * float(max_min); c0 = cavity(); shouted = False
        while self.ts() < end:
            ok, msg = self._svc('set_program_option', {'device_id': self.cfg.get('oven_device'), 'key': 'Cooking.Oven.Option.SetpointTemperature', 'value': want})
            self.log('oven set sent: ' + ('ok' if ok else msg))
            self.sleep(30)
            c = cavity()
            rising = c is not None and c0 is not None and ((want > c0 and c >= c0 + 4) or (want < c0 and c <= c0 - 4) or abs(c - want) <= 3)
            if rising: self.log('oven WORKING: heading for %s' % want); return True
            if not shouted and self.ts() - t0 > 60:
                self._shout('The oven has not changed to %s degrees yet. I am still trying.' % want); shouted = True
        self._shout('I could not change the oven to %s degrees. Please set it by hand.' % want)
        self.log('oven set FAILED'); return False

    def oven_stop(self, max_min=30):
        if not self.live:
            self.log('would set oven: switch off (dry: no oven call made)'); return 'dry'
        t0 = self.ts(); shouted = False
        if 'Run' in self._opstate() or 'Pause' in self._opstate(): self._signpost('Turning the oven off now.')
        end = self.ts() + 60 * float(max_min)
        while self.ts() < end:
            op = self._opstate()
            if 'Run' not in op and 'Pause' not in op:
                self.log('oven off'); return True
            ok, msg = self._svc('stop_program', {'device_id': self.cfg.get('oven_device')})
            if not ok: self.log('oven blocked, retrying: ' + msg)
            if not shouted and self.ts() - t0 > 60:
                self._shout('The oven has not switched off yet. I am still trying. Please check it.'); shouted = True
            self.sleep(20)
        self._shout('I could not switch the oven off. Please turn it off by hand.')
        self.log('oven stop GAVE UP'); return False

    def oven_action(self, spec):
        """spec = {"action": "start"|"set"|"stop", "temp": 200, ...}. Runs in the background so the cook loop carries on."""
        a = (spec or {}).get('action')
        fn = {'start': lambda: self.oven_start(int(spec.get('temp', 220)), spec.get('program', 'HotAir'), int(spec.get('minutes', 60))),
              'set': lambda: self.oven_set_temp(int(spec['temp'])), 'stop': lambda: self.oven_stop()}.get(a)
        if not fn: self.log('oven action ignored: %r' % (spec,)); return False
        self.spawn(lambda: self._safe(fn)); return True

    def _safe(self, fn):
        try: fn()
        except Exception as e: self.log('oven action error ' + type(e).__name__)

    def oven_on(self):
        try: return str(self.state(self.OVEN_POWER).get('state', '')).endswith('.On')
        except Exception: return True

    # --- wall display cooking screen: the plan sensor (+ rerender script at the end)
    def publish(self, plan, st, on=True):
        try: t = round(float(self.state(self.OVEN).get('state'))) if self.oven_on() else 'off'
        except Exception: t = None
        try: ts = round(float(self.state(self.OVEN_SET).get('state')))
        except Exception: ts = None
        if (plan or {}).get('ignore_set'): ts = None
        steps = [dict(at=s['at'], text=s.get('short') or s['text'], done=(s['at'] + ' ' + s['text']) in st['done']) for s in (plan or {}).get('steps', [])]
        full = [dict(x, key=s['at'] + ' ' + s['text']) for x, s in zip(steps, (plan or {}).get('steps', []))]
        hmn = self.now().strftime('%H:%M')
        nd = [x for x in full if not x['done']]
        grp = [x for x in nd if x['at'] <= hmn][:3] or ([x for x in nd if x['at'] == nd[0]['at']][:3] if nd else [])
        next_n = len(grp)
        try: self.dump('shownext.json', [x['key'] for x in grp])
        except Exception: pass
        dn = [x for x in steps if x['done']]; steps = dn[-1:] + [x for x in steps if not x['done']]
        shown = [x['text'] for x in (plan or {}).get('prep', []) if not x.get('done')][:5]
        try: self.dump('shown.json', shown)
        except Exception: pass
        sensor = self.cfg.get('plan_sensor') or 'sensor.cooking_plan'
        body = {'state': 'on' if on else 'off', 'attributes': {'title': (plan or {}).get('title', ''), 'eat': (plan or {}).get('eat', ''),
                'prep': [x['text'] for x in (plan or {}).get('prep', []) if not x.get('done')][:5], 'prep_left': sum(1 for x in (plan or {}).get('prep', []) if not x.get('done')),
                'next_n': next_n, 'oven': t, 'set': ts, 'target': (plan or {}).get('oven_target'), 'steps': steps, 'friendly_name': 'Cooking plan', 'icon': 'mdi:chef-hat'}}
        sig = json.dumps(body, sort_keys=True)
        if self.live:
            self.call('/states/' + sensor, body)
        elif sig != self._pub_sig:
            self.log('would publish %s state=%s next_n=%s steps=%d' % (sensor, body['state'], next_n, len(steps)))
        self._pub_sig = sig
        return body

    def save_state(self, st):
        """state.json without losing DONE taps made by the API while this loop held an older copy (host save_state)."""
        with self.lock:
            disk = self.load('state.json', {})
            st['done'] = st.get('done', []) + [k for k in disk.get('done', []) if k not in st.get('done', [])]
            st['ack'] = st.get('ack', []) + [k for k in disk.get('ack', []) if k not in st.get('ack', [])]
            st['last_done_ts'] = max(st.get('last_done_ts', 0), disk.get('last_done_ts', 0))
            st['done_rows'] = dict(disk.get('done_rows', {}), **st.get('done_rows', {}))
            self.dump('state.json', st)

    # --- one iteration of the cook loop (port of cook.py's while-loop body). Returns True to carry on, False when finished.
    def tick(self):
        cfg = self.cfg
        with self.lock:
            plan = self.load('plan.json', None)
        if not plan:
            self.log('no plan, stopping')
            try: self.publish(None, {'done': []}, False)
            except Exception: pass
            return False
        st = self.load('state.json', {'done': [], 'oven': False})
        now = self.now()
        hm = now.strftime('%H:%M')
        gate = next((x for x in plan.get('steps', []) if x.get('gate')), None)
        mins = lambda a, b: int((datetime.datetime.combine(now.date(), datetime.time.fromisoformat(b)) - datetime.datetime.combine(now.date(), datetime.time.fromisoformat(a))).total_seconds() // 60)
        gate_open, lag, released = gate_status(plan, st, hm, mins)
        if released and not st.get('gate_released'):
            st['gate_released'] = True; st['done'].append(gate['at'] + ' ' + gate['text']); self.log('gate released: ' + released)
        if gate_open and lag > 0:
            sh = lambda t: (datetime.datetime.combine(now.date(), datetime.time.fromisoformat(t)) + datetime.timedelta(minutes=lag)).strftime('%H:%M')
            tk = set(st.get('told', []))
            for x in plan['steps']:
                k = x['at'] + ' ' + x['text']; x['at'] = sh(x['at'])
                if k in tk: st['told'] = [x['at'] + ' ' + x['text'] if t == k else t for t in st['told']]
            plan['eat'] = sh(plan['eat'])
            with self.lock:
                self.dump('plan.json', plan, indent=1, ensure_ascii=False)
            self.log(f'waiting for the ingredients: plan moved {lag} min later, eating at {plan["eat"]}')
        voice = plan.get('voice', False)
        if not st.get('intro'):
            st.setdefault('told', [])
            due = [s for s in plan.get('steps', []) if s['at'] <= hm and (s['at'] + ' ' + s['text']) not in st['done'] and not s.get('auto')]
            if gate_open: due = [s for s in due if s.get('gate')]
            nxt = ([gate] if gate_open else []) + [s for s in plan.get('steps', []) if not s.get('auto') and not s.get('gate')][:1]
            nxt = nxt[:1]
            msg = f"OK, we're cooking {plan.get('title', 'dinner')}, eating at {plan.get('eat', '')}."
            if due and due[0].get('gate'):
                st['told'] += [due[0]['at'] + ' ' + due[0]['text']]; st['intro'] = True
                self.save_state(st)
                self.say_list(msg + " Let's start by getting everything we need out.", [str(x) for x in (due[0].get('items') or [due[0]['text']])],
                              "That's everything. Tap done on the wall display when you're ready, and the clock starts.", voice)
                msg = None
            elif due:
                msg += " Let's start with the first step. " + ' And '.join(s['text'].rstrip('.') + '.' for s in due[:3])
                st['told'] += [s['at'] + ' ' + s['text'] for s in due[:3]]
                for s in due[:3]:
                    if s.get('oven'): self.oven_action(s['oven'])
            elif nxt:
                msg += f" The first step is at {nxt[0]['at']}: {nxt[0]['text']}"
            if msg: self.tell(msg, voice)
            st['intro'] = True
        for s in plan.get('steps', []):
            key = s['at'] + ' ' + s['text']
            if gate_open and not s.get('gate'): continue
            if key not in st['done'] and key not in st.setdefault('told', []) and s['at'] <= hm:
                late = (datetime.datetime.combine(now.date(), datetime.time.fromisoformat(s['at'])) < now - datetime.timedelta(minutes=10))
                if not late:
                    self.tell(s['text'], voice)
                    if s.get('oven'): self.oven_action(s['oven'])
                else: self.log('skipped (more than 10 min late): ' + key)
                st['told'].append(key)
                if s.get('auto'): st['done'].append(key)
        st.setdefault('told_at', {}); st.setdefault('nagged', [])
        for key in st['told']: st['told_at'].setdefault(key, self.ts())
        for s in plan.get('steps', []):
            key = s['at'] + ' ' + s['text']
            urgent = s.get('anchor') or 'oven on' in (s.get('short') or '').lower()
            if urgent and key in st['told'] and key not in st['done'] and key not in st['nagged'] and key not in st.get('ack', []) and self.ts() - st['told_at'][key] > 150:
                self.tell("This is important, it needs doing now: " + s['text'], voice); st['nagged'].append(key)
        ndone = len(st['done'])
        if ndone > st.get('gap_seen', ndone):
            rest = sorted([s for s in plan.get('steps', []) if (s['at'] + ' ' + s['text']) not in st['done'] and not s.get('auto')], key=lambda s: s['at'])
            if rest:
                gap = int((datetime.datetime.combine(now.date(), datetime.time.fromisoformat(rest[0]['at'])) - now).total_seconds() // 60)
                if gap >= 15: self.tell(f"Nothing to do for {gap} minutes. Next, at {rest[0]['at']}: {rest[0].get('short') or rest[0]['text']}", voice)
        st['gap_seen'] = ndone
        tgt = plan.get('oven_target')
        try: oset = round(float(self.state(self.OVEN_SET).get('state')))
        except Exception: oset = None
        if tgt and oset is not None and not plan.get('ignore_set') and oset != int(tgt) and st.get('setwarn') != oset:
            self.tell(f'The oven is set to {oset} degrees, but the plan needs {int(tgt)}', voice); st['setwarn'] = oset
        if tgt and not st.get('oven') and self.oven_on():
            try: t = float(self.state(self.OVEN).get('state'))
            except Exception: t = None
            if t is not None and t >= float(tgt) - 10:
                self.tell(f'The oven is up to {int(t)} degrees', voice); st['oven'] = True
        eat = plan.get('eat')
        name = cfg.get('child_name') or ''
        who = name or 'Someone'
        if eat and not st.get('child_sent'):
            due = (datetime.datetime.combine(now.date(), datetime.time.fromisoformat(eat)) - datetime.timedelta(minutes=plan.get(RK + 'minutes', 15))).strftime('%H:%M')
            if hm >= due:
                try:
                    self.discord_dm(cfg.get('child_dm'), None, {'title': f"🍽️ {plan.get('title', 'Food')} in 15 minutes, {name or 'you'}!",
                                    'description': f'Dishing up at {eat}.' + (' ' + plan[RK + 'note'] if plan.get(RK + 'note') else ''), 'color': 15105570})
                    self.log('messaged child: dishing up at ' + eat)
                    self.tell(f'{who} has been told food is in 15 minutes', voice)
                except Exception as e: self.log('child message failed ' + str(e))
                st['child_sent'] = True
        if eat:
            default = [[10, f"Oi {name}! Table and drinks. Now please 🍽️"], [0, f"Food's on the table, {name}. Get down here before it goes cold 😤"]]
            for m_, txt in plan.get(RK + 'nudges', default):
                key = f'nudge{m_}'
                due = (datetime.datetime.combine(now.date(), datetime.time.fromisoformat(eat)) - datetime.timedelta(minutes=m_)).strftime('%H:%M')
                if hm >= due and not st.get(key):
                    try:
                        self.discord_dm(cfg.get('child_dm'), txt); self.log('nudged child: ' + txt)
                    except Exception as e: self.log('child nudge failed ' + str(e))
                    st[key] = True
        self.save_state(st)
        soon = (now + datetime.timedelta(minutes=plan.get('screen_lead', 30))).strftime('%H:%M')
        active = any((x['at'] + ' ' + x['text']) not in st['done'] and x['at'] <= soon for x in plan.get('steps', []))
        try: self.publish(plan, st, active)
        except Exception as e: self.log('publish failed ' + str(e))
        if plan.get('steps') and all((x['at'] + ' ' + x['text']) in st['done'] for x in plan['steps']):
            self.log('all steps done, stopping')
            try: self.publish(plan, st, False)
            except Exception: pass
            if cfg.get('rerender_script'):
                try: self.call('/services/script/turn_on', {'entity_id': cfg['rerender_script']})
                except Exception: pass
            return False
        eat = plan.get('eat')
        if eat and hm > (datetime.datetime.combine(now.date(), datetime.time.fromisoformat(eat)) + datetime.timedelta(minutes=30)).strftime('%H:%M'):
            self.log('meal over, stopping')
            try: self.publish(plan, st, False)
            except Exception: pass
            return False
        return True

    # --- running it (replaces cookd.sh + cook_request.json)
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def _loop(self):
        self.log('started (%s)' % ('LIVE' if self.live else 'dry'))
        try:
            while not self.stop_flag.is_set():
                try:
                    if not self.tick(): break
                except Exception as e:
                    self.log('tick error %s %s' % (type(e).__name__, str(e)[:120]))
                for _ in range(50):
                    if self.stop_flag.is_set(): break
                    time.sleep(0.1)
        finally:
            self.log('loop ended')

    def start_cook(self, recipe_obj, name, eat):
        """Request arrives by HTTP. Replaces a running cook (old plan -> plan.replaced.json), like cookd.sh."""
        if self.cfg.get('mode') == 'off':
            return {'ok': False, 'why': 'cook_mode is off'}
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', str(eat)):
            return {'ok': False, 'why': 'eat must be HH:MM'}
        with self.start_lock:                      # not self.lock: the old loop needs it to finish its tick
            if self.running():
                self.stop_flag.set(); self.thread.join(timeout=30)
        with self.lock, self.start_lock:
            if os.path.exists(self.p('plan.json')): os.replace(self.p('plan.json'), self.p('plan.replaced.json'))
            plan = build_plan(recipe_obj, name, eat, self.now().date(), self.cfg.get('child_name') or 'Child')
            self.dump('plan.json', plan, indent=1, ensure_ascii=False)
            self.dump('state.json', {'done': [], 'told': [], 'oven': False})
            self.stop_flag = threading.Event()
            self.thread = threading.Thread(target=self._loop, daemon=True); self.thread.start()
        ok, why = self.guard(force=True)
        self.log('started %s for %s (%s)' % (name, eat, 'LIVE' if ok else 'dry: ' + why))
        return {'ok': True, 'live': ok, 'dry': not ok, 'why': why, 'steps': [(s['at'], s['short']) for s in plan['steps']]}

    def stop_cook(self):
        with self.lock:
            if os.path.exists(self.p('plan.json')): os.replace(self.p('plan.json'), self.p('plan.stopped.json'))
        self.log('stop requested')
        return {'ok': True}

    # --- wall display buttons (ports of done.py / ack.py / tick.py / delay.py); the shown*.json files are written by publish()
    def _row_key(self, row):
        keys = self.load('shownext.json', [])
        return keys[row] if 0 <= row < len(keys) else None

    def done(self, row=0, did=False):
        with self.lock:
            p = self.load('plan.json', None)
            if not p: return {'ok': False, 'why': 'no plan'}
            st = self.load('state.json', {'done': []})
            if self.ts() - st.get('last_done_ts', 0) < 10:
                self.log('DONE ignored (within 10 s of the last tap)'); return {'ok': False, 'why': 'double tap'}
            keys = self.load('shownext.json', [])
            if row < len(keys) and keys[row] not in st['done']:
                k = keys[row]
                st.setdefault('done_rows', {})[str(row)] = self.ts()
                st['done'].append(k); st['last_done_ts'] = self.ts(); self.log('done (wall display, did it): ' + k if did else 'done (wall display): ' + k)
                s = next((x for x in p['steps'] if x['at'] + ' ' + x['text'] == k), None)
                if s and s.get('anchor') and not did:
                    now = self.now().replace(second=0, microsecond=0); at = lambda t: datetime.datetime.combine(now.date(), datetime.time.fromisoformat(t))
                    gap = int((now - at(s['at'])).total_seconds() // 60)
                    if gap:
                        sh = lambda t: (at(t) + datetime.timedelta(minutes=gap)).strftime('%H:%M')
                        for x in p['steps']:
                            xk = x['at'] + ' ' + x['text']
                            if x is s or xk in st['done'] or x['at'] < s['at']: continue
                            x['at'] = sh(x['at']); st['told'] = [x['at'] + ' ' + x['text'] if t == xk else t for t in st.get('told', [])]
                        p['eat'] = sh(p['eat']); p['steps'].sort(key=lambda x: x['at'])
                        self.dump('plan.json', p, indent=1, ensure_ascii=False)
                        self.log(f'anchor done {gap:+d} min: rest of plan moved, eating at {p["eat"]}')
                self.dump('state.json', st)
                return {'ok': True, 'key': k}
        return {'ok': False, 'why': 'no such row'}

    def ack(self, row=0):
        with self.lock:
            st = self.load('state.json', {'done': []}); k = self._row_key(row)
            if k and k not in st.get('ack', []):
                st.setdefault('ack', []).append(k); self.log('on it (wall display): ' + k); self.dump('state.json', st)
                return {'ok': True, 'key': k}
        return {'ok': False, 'why': 'no such row'}

    def tick_prep(self, row):
        with self.lock:
            p = self.load('plan.json', None)
            if not p: return {'ok': False, 'why': 'no plan'}
            shown = self.load('shown.json', None)
            if shown is None: shown = [x['text'] for x in p.get('prep', []) if not x.get('done')][:5]
            want = shown[row] if 0 <= row < len(shown) else None
            hit = [x for x in p.get('prep', []) if x['text'] == want and not x.get('done')]
            if hit:
                hit[0]['done'] = True; self.log('prep ticked: ' + want); self.dump('plan.json', p, indent=1)
                return {'ok': True}
        return {'ok': False, 'why': 'no such row'}

    def delay(self, minutes=5, row=0):
        with self.lock:
            p = self.load('plan.json', None)
            if not p: return {'ok': False, 'why': 'no plan'}
            st = self.load('state.json', {'done': []})
            if self.ts() - st.get('done_rows', {}).get(str(row), 0) < 90:
                self.log(f'+5 ignored: row {row} was just done'); return {'ok': False, 'why': 'just done'}
            r = shift_step(p, self._row_key(row), minutes, self.now())
            if r: self.log(f'delayed {r[0]} -> {r[1]}: {r[2]}')
            p['steps'].sort(key=lambda s: s['at'])
            self.dump('plan.json', p, indent=1)
            return {'ok': bool(r), 'moved': r}

    def status(self):
        ok, why = self.guard()
        plan = self.load('plan.json', None) or {}
        st = self.load('state.json', {})
        return {'mode': self.cfg.get('mode'), 'live': ok, 'guard': why, 'running': self.running(), 'title': plan.get('title'), 'eat': plan.get('eat'),
                'steps': len(plan.get('steps', [])), 'done': len(st.get('done', []))}
