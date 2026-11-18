"""ih_speaker core: speak a message on a HomePod. It NEVER writes the message; callers (assistants) do.
Behaviour is ported from the Home Assistant script that speaks at bedtime: engine choice, 20 % volume then restore, wait for the speaker to start.
Everything outside is injectable for tests: ha (call_service / get), clock, sleep, now (aware datetime)."""
import math
import threading
import time
from collections import deque
from datetime import datetime, timezone

MAX_TEXT = 600
DEFAULT_VOLUME = 0.2
START_TIMEOUT_S = 20      # the speaker must be 'playing' within this time, else the caller plays the old clip
IDLE_WAIT_S = 10          # after the speech: wait this long for 'playing' to end before restoring the volume


def speech_seconds(text):
    """How long to wait for a message to finish: same rule as the Home Assistant script (max(5, ceil(len/12)+3))."""
    return max(5, math.ceil(len(text) / 12) + 3)


def _hm(text, default):
    try:
        h, m = str(text or default).split(':')[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        h, m = str(default).split(':')
        return int(h) * 60 + int(m)


def in_window(now, start, end):
    """True when the clock time of `now` is in [start, end), 'HH:MM' text. Wraps midnight. start == end means never."""
    a, b, t = _hm(start, '00:00'), _hm(end, '00:00'), now.hour * 60 + now.minute
    if a == b:
        return False
    return a <= t < b if a < b else (t >= a or t < b)


def _pairs(items):
    """['a=b', 'c=d,e'] -> {'a': 'b', 'c': 'd,e'} (bad entries are ignored)."""
    out = {}
    for s in items or []:
        if isinstance(s, str) and '=' in s:
            k, v = s.split('=', 1)
            if k.strip():
                out[k.strip().lower()] = v.strip()
    return out


def _names(v):
    return [x.strip().lower() for x in str(v or '').split(',') if x.strip()]


def parse_options(o):
    """Options (from /data/options.json) -> a clean config dict. Unknown or bad values fall back to safe defaults."""
    o = o or {}

    def num(k, d, lo, hi):
        try:
            return min(hi, max(lo, float(o.get(k, d))))
        except (TypeError, ValueError):
            return d
    return {
        'mode': 'live' if o.get('mode') == 'live' else 'dry',
        'api_token': str(o.get('api_token') or ''),
        'timezone': str(o.get('timezone') or 'UTC'),
        'default_volume': num('default_volume', DEFAULT_VOLUME, 0.0, 1.0),
        'quiet_start': str(o.get('quiet_start') or '00:00'),
        'quiet_end': str(o.get('quiet_end') or '07:00'),
        'night_start': str(o.get('night_start') or '22:00'),
        'alert_personas': _names(','.join(o.get('alert_personas') or [])),
        'speakers': {k: v for k, v in _pairs(o.get('speakers')).items() if v.startswith('media_player.')},
        'voices': _pairs(o.get('voices')),
        'permissions': {k: _names(v) for k, v in _pairs(o.get('permissions')).items()},
        'night_deny': {k: _names(v) for k, v in _pairs(o.get('night_deny')).items()},
        'keep_volume_speakers': _names(','.join(o.get('keep_volume_speakers') or [])),
        'engines': [e for e in (o.get('engines') or ['tts.piper', 'tts.kokoro']) if isinstance(e, str) and e.startswith('tts.')],
        'queue_max': int(num('queue_max', 5, 1, 20)),
        'queue_ttl_s': int(num('queue_ttl_s', 120, 1, 900)),
    }


class Turns:
    """One speaker, one message at a time, first come first served. A waiting message is dropped when the line is full or it waited too long."""
    def __init__(self, max_len, clock=time.time):
        self.max_len, self.clock = max_len, clock
        self.cond, self.q, self.busy = threading.Condition(), deque(), False

    def enter(self, ttl):
        """'ok' (your turn; call leave() later), 'full' or 'expired'."""
        deadline = self.clock() + ttl
        with self.cond:
            if len(self.q) >= self.max_len:
                return 'full'
            me = object()
            self.q.append(me)
            while not (self.q[0] is me and not self.busy):
                left = deadline - self.clock()
                if left <= 0:
                    self.q.remove(me)
                    self.cond.notify_all()
                    return 'expired'
                self.cond.wait(min(left, 0.5))
            self.q.popleft()
            self.busy = True
            return 'ok'

    def leave(self):
        with self.cond:
            self.busy = False
            self.cond.notify_all()

    def waiting(self):
        with self.cond:
            return len(self.q)


class Speaker:
    def __init__(self, ha, opts, clock=time.time, sleep=time.sleep, now=None, log=None):
        self.ha, self.cfg, self.clock, self.sleep = ha, parse_options(opts), clock, sleep
        self._now, self.log = now, log or (lambda *a, **k: None)
        self.turns = {n: Turns(self.cfg['queue_max'], clock) for n in self.cfg['speakers']}
        self.history = deque(maxlen=50)   # recent results (no message text), for /status

    # ---- helpers ----
    def now(self):
        if self._now:
            return self._now()
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(self.cfg['timezone']))
        except Exception:
            return datetime.now(timezone.utc)

    def _state(self, entity):
        try:
            return self.ha.get('states/' + entity) or None
        except Exception:
            return None

    def _engine(self):
        for e in self.cfg['engines']:
            st = self._state(e)
            if st and st.get('state') != 'unavailable':
                return e
        return ''

    def _voice(self, persona, voice):
        """Voice id for Piper. `voice` may be a voice name from the options, or one of the configured voice ids. '' = none. None = unknown voice."""
        v = str(voice or '').strip()
        voices = self.cfg['voices']
        if not v:
            return voices.get(persona, '')
        if v.lower() in voices:
            return voices[v.lower()]
        return v if v in voices.values() else None

    # ---- guards: callers cannot switch these off ----
    def check(self, req):
        """-> (allowed, reason). Looks only at the request and the clock."""
        persona = str(req.get('persona') or '').strip().lower()
        speaker = str(req.get('speaker') or '').strip().lower()
        text = str(req.get('text') or '').strip()
        if not persona:
            return False, 'persona is required'
        if not text:
            return False, 'text is empty'
        if len(text) > MAX_TEXT:
            return False, 'text is longer than %d characters' % MAX_TEXT
        if speaker not in self.cfg['speakers']:
            return False, 'unknown speaker'
        allowed = self.cfg['permissions'].get(persona)
        if allowed is None or not ('*' in allowed or speaker in allowed):
            return False, '%s may not use the %s speaker' % (persona, speaker)
        now = self.now()
        alert = req.get('urgency') == 'alert' and persona in self.cfg['alert_personas']
        if not alert:
            if in_window(now, self.cfg['quiet_start'], self.cfg['quiet_end']):
                return False, 'quiet hours (alerts only until %s)' % self.cfg['quiet_end']
            if speaker in self.cfg['night_deny'].get(persona, []) and in_window(now, self.cfg['night_start'], self.cfg['quiet_end']):
                return False, '%s is not allowed in the %s at night' % (persona, speaker)
        return True, ''

    def plan(self, req):
        """Everything that would happen, without doing it."""
        ok, why = self.check(req)
        persona = str(req.get('persona') or '').strip().lower()
        speaker = str(req.get('speaker') or '').strip().lower()
        text = str(req.get('text') or '').strip()
        out = {'allowed': ok, 'reason': why, 'text': text, 'persona': persona, 'speaker': speaker}
        if not ok:
            return out
        voice = self._voice(persona, req.get('voice'))
        if voice is None:
            out.update(allowed=False, reason='unknown voice')
            return out
        engine = self._engine()
        entity = self.cfg['speakers'][speaker]
        keep = str(req.get('volume', '')).lower() == 'keep' or speaker in self.cfg['keep_volume_speakers']
        vol = self.cfg['default_volume']
        if not keep and req.get('volume') not in (None, ''):
            try:
                vol = min(1.0, max(0.0, float(req['volume'])))
            except (TypeError, ValueError):
                out.update(allowed=False, reason='volume must be a number 0 to 1 or "keep"')
                return out
        st = self._state(entity) or {}
        before = (st.get('attributes') or {}).get('volume_level')
        out.update(engine=engine or 'fallback', voice=voice if engine == 'tts.piper' else '', entity=entity,
                   volume={'set': None if keep else vol, 'restore_to': None if keep or not isinstance(before, (int, float)) else before},
                   wait_s=speech_seconds(text), playing_now=st.get('state') == 'playing',
                   skip_if_playing=req.get('skip_if_playing', True) is not False)
        return out

    # ---- speak ----
    def speak(self, req):
        """-> {status, reason, ...}. status: spoken | dry | blocked | skipped | queue_full | expired | fallback | error."""
        p = self.plan(req)
        if not p['allowed']:
            return self._done('blocked', p)
        if req.get('dry_run') or self.cfg['mode'] != 'live':
            p['dry_run_reason'] = 'requested' if req.get('dry_run') else 'add-on mode is dry'
            return self._done('dry', p)
        if p['engine'] == 'fallback':
            p['reason'] = 'neither TTS engine is available: play the old clip'
            return self._done('fallback', p)
        turns = self.turns[p['speaker']]
        got = turns.enter(self.cfg['queue_ttl_s'])
        if got != 'ok':
            p['reason'] = 'queue full' if got == 'full' else 'waited too long in the queue'
            return self._done('queue_full' if got == 'full' else 'expired', p)
        try:
            return self._play(req, p)
        finally:
            turns.leave()

    def _play(self, req, p):
        entity, text, engine = p['entity'], p['text'], p['engine']
        st = self._state(entity) or {}
        if p['skip_if_playing'] and st.get('state') == 'playing':
            p['reason'] = 'speaker is playing'
            return self._done('skipped', p)
        before = (st.get('attributes') or {}).get('volume_level')
        changed = False
        try:
            if p['volume']['set'] is not None:
                self.ha.call_service('media_player', 'volume_set', {'entity_id': entity, 'volume_level': p['volume']['set']})
                changed = True
            data = {'entity_id': engine, 'media_player_entity_id': entity, 'message': text}
            if engine == 'tts.piper' and p['voice']:
                data['options'] = {'voice': p['voice']}
            self.ha.call_service('tts', 'speak', data)
            started = self._wait_for(entity, 'playing', START_TIMEOUT_S)
            if not started:
                p['reason'] = 'the speaker never started playing: play the old clip'
                return self._done('fallback', p)
            self.sleep(p['wait_s'])
            self._wait_for(entity, 'playing', IDLE_WAIT_S, until_not=True)
            return self._done('spoken', p)
        except Exception as e:   # HA refused or unreachable
            p['reason'] = 'Home Assistant call failed: %s' % type(e).__name__
            return self._done('error', p)
        finally:
            if changed and isinstance(before, (int, float)):
                try:
                    self.ha.call_service('media_player', 'volume_set', {'entity_id': entity, 'volume_level': before})
                except Exception:
                    self.log('volume restore failed', speaker=p['speaker'])

    def _wait_for(self, entity, state, timeout, until_not=False):
        """Poll every 0.5 s. True when the speaker is in `state` (or, with until_not, no longer in it) within `timeout` seconds."""
        waited = 0.0
        while True:
            s = (self._state(entity) or {}).get('state')
            if (s != state) if until_not else (s == state):
                return True
            if waited >= timeout:
                return False
            self.sleep(0.5)
            waited += 0.5

    def _done(self, status, p):
        res = dict(p, status=status, ok=status in ('spoken', 'dry'))
        self.history.append({'at': self.now().isoformat(timespec='seconds'), 'status': status, 'persona': p.get('persona'),
                             'speaker': p.get('speaker'), 'reason': p.get('reason', ''), 'chars': len(p.get('text', ''))})
        self.log('speak', status=status, persona=p.get('persona'), speaker=p.get('speaker'), reason=p.get('reason', ''))
        return res
