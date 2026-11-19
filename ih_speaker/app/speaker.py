"""ih_speaker core: speak a message on a HomePod. It NEVER writes the message; callers (assistants) do.
Behaviour is ported from the Home Assistant script that speaks at bedtime: engine choice, 20 % volume then restore, wait for the speaker to start.
Everything outside is injectable for tests: ha (call_service / get), clock, sleep, now (aware datetime)."""
import hashlib
import hmac
import math
import threading
import time
from collections import deque
from datetime import datetime, timezone

import cache as cachelib
import voice

MAX_TEXT = 600
SEED_PHRASES = ['One moment', 'Just checking', 'Alarm armed', 'Alarm disarmed', 'Good morning']   # pinned fillers, made for each voice
URL_TTL_S = 600           # a signed clip link works this long
FILLER_START_S = 8        # a filler must start within this time, else it is skipped
OBTAIN_WAIT_S = 90        # longest wait for the real clip to be rendered
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
        'discord_token': str(o.get('discord_token') or ''),
        'discord_users': {k: v for k, v in _pairs(o.get('discord_users')).items() if v.isdigit()},
        'discord_permissions': {k: _names(v) for k, v in _pairs(o.get('discord_permissions')).items()},
        'engines': [e for e in (o.get('engines') or ['tts.piper', 'tts.kokoro']) if isinstance(e, str) and e.startswith('tts.')],
        'queue_max': int(num('queue_max', 5, 1, 20)),
        'queue_ttl_s': int(num('queue_ttl_s', 120, 1, 900)),
        'cache_mb': int(num('cache_mb', 1024, 1, 1000000)),
        'cache_days': int(num('cache_days', 30, 1, 3650)),
        'base_url': str(o.get('base_url') or '').strip().rstrip('/'),
        'seed_fillers': o.get('seed_fillers', True) is not False,
        'auto_pin_uses': int(num('auto_pin_uses', 5, 0, 1000)),   # 0 = never auto-pin
        'auto_pin_days': int(num('auto_pin_days', 30, 1, 3650)),
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
    def __init__(self, ha, opts, clock=time.time, sleep=time.sleep, now=None, log=None, audio=None, encode=None, discord=None, cache=None):
        """audio(engine, text, voice) -> bytes, encode(bytes) -> (ogg, seconds, waveform), discord = module with send_voice_message / send_dm_rich / send_dm.
        All three default to the real ones (HA text-to-speech, ffmpeg, ihlib.discord) and are replaced in tests."""
        self.ha, self.cfg, self.clock, self.sleep = ha, parse_options(opts), clock, sleep
        self._render = audio or (lambda engine, text, v: voice.render(self.ha, engine, text, v))
        self.cache, self._seeded, self._seed_lock = cache, set(), threading.Lock()
        self._encode = encode or voice.encode
        self._discord = discord
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

    # ---- clip cache: every rendered message is kept; same phrase + same engine + same voice = one entry ----
    def _audio(self, engine, text, voice_id, store=True):
        """The audio bytes of a message: from the cache, else rendered, stored (not in a dry run) and returned."""
        return self._obtain(engine, text, voice_id, store=store, count=store)[1]   # a dry run (store=False) is not a use

    def _obtain(self, engine, text, voice_id, label='', pinned=None, store=True, count=True):
        """-> (row or None, audio bytes, hit). Raises what the renderer raises. A cache that cannot store never stops the speech."""
        vid = voice_id if engine == 'tts.piper' else ''
        if self.cache:
            row = self.cache.get(engine, vid, text, count=count)
            if row:
                try:
                    return row, self.cache.read(row), True
                except OSError:
                    self.cache.delete(row['id'])
        data = self._render(engine, text, voice_id)
        row = None
        if self.cache and store:
            try:
                row = self.cache.put(engine, vid, text, data, label=label, pinned=pinned)
            except Exception as e:
                self.log('cache store failed', error=type(e).__name__)
        return row, data, False

    def clip_url(self, row, ttl=URL_TTL_S):
        """Signed link a speaker can fetch (needs base_url and api_token). '' when it cannot be made."""
        base, tok = self.cfg['base_url'], self.cfg['api_token']
        if not base or not tok or not row:
            return ''
        exp = int(self.clock()) + ttl
        return '%s/clip/%d.%s?exp=%d&sig=%s' % (base, row['id'], row['ext'], exp, sign_clip(tok, row['id'], exp))

    def seed(self, voice_id='', engine=''):
        """Make the pinned filler phrases for one voice (those not yet cached). -> number made. Plays nothing."""
        engine = engine or self._engine()
        if not engine:
            return 0
        vid = voice_id if engine == 'tts.piper' else ''
        made = 0
        for ph in SEED_PHRASES:
            row = self.cache.find(engine, vid, ph)
            if row and row['pinned'] and row['label']:
                continue
            try:
                self._obtain(engine, ph, voice_id, label=ph.lower(), pinned=True, count=False)
                made += 1
            except Exception as e:
                self.log('seed failed', error=type(e).__name__)
                break
        return made

    def seed_all(self):
        """Fillers for the engine default voice and every configured voice. Run in a background thread; never raises."""
        if not self.cache:
            return 0
        n = 0
        for v in ['', *sorted(set(self.cfg['voices'].values()))]:
            n += self._seed_once(v)
        return n

    def _seed_once(self, voice_id):
        with self._seed_lock:
            if voice_id in self._seeded:
                return 0
            self._seeded.add(voice_id)
        try:
            return self.seed(voice_id)
        except Exception:
            return 0

    def seed_in_background(self, voice_id):
        if self.cache and self.cfg['seed_fillers'] and self.cfg['mode'] == 'live' and voice_id not in self._seeded:
            threading.Thread(target=self._seed_once, args=(voice_id,), daemon=True).start()

    def cache_plan(self, engine, voice_id, text, filler=''):
        """Dry-run facts about the cache for one message: hit or miss, sizes, and what a store would evict. Changes nothing."""
        if not self.cache:
            return {'enabled': False}
        vid = voice_id if engine == 'tts.piper' else ''
        row = self.cache.find(engine, vid, text) if engine else None
        st = self.cache.stats()
        out = {'enabled': True, 'status': 'hit' if row else 'miss', 'clips': st['clips'], 'bytes': st['bytes'], 'max_bytes': st['max_bytes']}
        if row:
            out['clip_bytes'] = row['size']
        else:
            typical = int(st['bytes'] / st['clips']) if st['clips'] else 20000
            out['would_evict'] = [cachelib.brief(r) for r in self.cache.plan_evict(extra=typical)]
            out['estimated_new_bytes'] = typical
        if filler:
            f = self.cache.by_label(engine, vid, filler) if engine else None
            out['filler'] = 'hit' if f else 'not cached (would be skipped)'
        return out

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
        filler = str(req.get('filler') or '').strip()
        if filler:
            out['filler_label'] = filler
        out['cache'] = self.cache_plan(engine, voice, text, filler)
        return out

    # ---- speak ----
    def speak(self, req):
        """-> {status, reason, ...}. status: spoken | sent | dry | blocked | skipped | queue_full | expired | fallback | error.
        `to`: speaker (default) | discord (a voice message DM, no speaker) | both (speaker result, plus a `discord` part)."""
        to = str(req.get('to') or 'speaker').strip().lower()
        if to not in ('speaker', 'discord', 'both'):
            return self._done('blocked', {'allowed': False, 'reason': 'to must be speaker, discord or both', 'text': str(req.get('text') or '').strip(),
                                          'persona': str(req.get('persona') or '').strip().lower(), 'speaker': str(req.get('speaker') or '').strip().lower()})
        if to == 'discord':
            return self.send_voice(req)
        res = self._speak_speaker(req)
        if to == 'both':
            res['discord'] = self.send_voice(req)
        return res

    def _speak_speaker(self, req):
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
        self.seed_in_background(p['voice'])
        job = self._obtain_async(engine, text, p['voice']) if self.cache else None   # the real clip renders while a filler is spoken
        try:
            if p['volume']['set'] is not None:
                self.ha.call_service('media_player', 'volume_set', {'entity_id': entity, 'volume_level': p['volume']['set']})
                changed = True
            if p.get('filler_label'):
                p['filler'] = self._say_filler(entity, engine, p['voice'], p['filler_label'])
            row = None
            if self.cache and self.cfg['base_url']:
                row = job.value[0] if job.wait(OBTAIN_WAIT_S) and job.ok() else None
            url = self.clip_url(row)
            if url:
                p['via'] = 'cached clip'
                self.ha.call_service('media_player', 'play_media', {'entity_id': entity, 'media_content_id': url, 'media_content_type': 'music'})
            else:
                p['via'] = 'tts.speak'
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

    def _obtain_async(self, engine, text, voice_id):
        """Start getting the clip (cache or render) in a thread. -> Job."""
        job = Job()

        def run():
            try:
                job.value = self._obtain(engine, text, voice_id)
            except Exception as e:
                job.error = e
            finally:
                job.done.set()
        threading.Thread(target=run, daemon=True).start()
        return job

    def _say_filler(self, entity, engine, voice_id, label):
        """Play a cached filler phrase at once and wait until it has finished (or stop it) so the real clip never overlaps. -> short report."""
        if not self.cache:
            return 'skipped: no cache'
        row = self.cache.by_label(engine, voice_id if engine == 'tts.piper' else '', label)
        if not row:
            return 'skipped: not cached'
        url = self.clip_url(row)
        if not url:
            return 'skipped: base_url or api_token not set'
        self.cache.record_use(row['id'])
        self.ha.call_service('media_player', 'play_media', {'entity_id': entity, 'media_content_id': url, 'media_content_type': 'music'})
        if not self._wait_for(entity, 'playing', FILLER_START_S):
            return 'skipped: speaker did not start it'
        if not self._wait_for(entity, 'playing', speech_seconds(row['text']) + 5, until_not=True):
            self.ha.call_service('media_player', 'media_stop', {'entity_id': entity})   # never let it run into the real clip
            self._wait_for(entity, 'playing', 3, until_not=True)
            return 'played (stopped)'
        return 'played'

    def generate(self, text, voice_name='', engine='', label='', pin=False):
        """Render a message (the admin page) and keep it in the cache. -> (row, error). Plays nothing; works in dry mode too (it only stores a file)."""
        text = str(text or '').strip()
        if not self.cache:
            return None, 'the cache is not available'
        if not text or len(text) > MAX_TEXT:
            return None, 'text must be 1 to %d characters' % MAX_TEXT
        vid = self._voice('', voice_name)
        if vid is None:
            return None, 'unknown voice'
        eng = engine if engine in self.cfg['engines'] else (self._engine() if not engine else '')
        if not eng:
            return None, 'engine not available'
        try:
            row, _, _ = self._obtain(eng, text, vid, label=str(label or '').strip()[:60], pinned=True if pin else None, count=False)
        except Exception as e:
            return None, 'audio failed: %s' % (e if isinstance(e, voice.VoiceError) else type(e).__name__)
        if row is None:
            return None, 'could not store the clip'
        if label or pin:   # a clip that was already cached keeps its entry: apply the label and pin
            self.cache.db.execute('UPDATE clips SET label=CASE WHEN ?<>\'\' THEN ? ELSE label END WHERE id=?', (str(label or '').strip()[:60],) * 2 + (row['id'],))
            if pin:
                self.cache.set_pinned(row['id'], True)
            row = self.cache.by_id(row['id'])
        return row, ''

    def play_clip(self, speaker, row, volume=None):
        """Play one cached clip on a named speaker (the admin page). Dry mode plays nothing. Waits its turn like any message."""
        speaker = str(speaker or '').strip().lower()
        if speaker not in self.cfg['speakers']:
            return self._done('blocked', {'allowed': False, 'reason': 'unknown speaker', 'speaker': speaker, 'persona': 'admin', 'text': ''})
        entity = self.cfg['speakers'][speaker]
        p = {'allowed': True, 'reason': '', 'speaker': speaker, 'persona': 'admin', 'entity': entity, 'text': row['text'], 'clip_id': row['id']}
        if self.cfg['mode'] != 'live':
            p['dry_run_reason'] = 'add-on mode is dry'
            return self._done('dry', p)
        url = self.clip_url(row)
        if not url:
            p['reason'] = 'base_url or api_token is not set: a speaker cannot fetch the clip'
            return self._done('error', p)
        turns = self.turns[speaker]
        got = turns.enter(self.cfg['queue_ttl_s'])
        if got != 'ok':
            p['reason'] = 'queue full' if got == 'full' else 'waited too long in the queue'
            return self._done('queue_full' if got == 'full' else 'expired', p)
        try:
            st = self._state(entity) or {}
            before = (st.get('attributes') or {}).get('volume_level')
            vol = self.cfg['default_volume'] if volume is None else volume
            keep = speaker in self.cfg['keep_volume_speakers']
            changed = False
            try:
                if not keep:
                    self.ha.call_service('media_player', 'volume_set', {'entity_id': entity, 'volume_level': vol})
                    changed = True
                self.ha.call_service('media_player', 'play_media', {'entity_id': entity, 'media_content_id': url, 'media_content_type': 'music'})
                if not self._wait_for(entity, 'playing', START_TIMEOUT_S):
                    p['reason'] = 'the speaker never started playing'
                    return self._done('fallback', p)
                self.sleep(speech_seconds(row['text']))
                self._wait_for(entity, 'playing', IDLE_WAIT_S, until_not=True)
                return self._done('spoken', p)
            except Exception as e:
                p['reason'] = 'Home Assistant call failed: %s' % type(e).__name__
                return self._done('error', p)
            finally:
                if changed and isinstance(before, (int, float)):
                    try:
                        self.ha.call_service('media_player', 'volume_set', {'entity_id': entity, 'volume_level': before})
                    except Exception:
                        self.log('volume restore failed', speaker=speaker)
        finally:
            turns.leave()

    # ---- voice clip to a Discord DM (true voice-message bubble, ogg/opus) ----
    def check_discord(self, req):
        """-> (allowed, reason). Quiet hours do NOT apply: a DM makes no sound."""
        persona = str(req.get('persona') or '').strip().lower()
        user = str(req.get('discord_user') or '').strip().lower()
        text = str(req.get('text') or '').strip()
        if not persona:
            return False, 'persona is required'
        if not text:
            return False, 'text is empty'
        if len(text) > MAX_TEXT:
            return False, 'text is longer than %d characters' % MAX_TEXT
        if user not in self.cfg['discord_users']:
            return False, 'unknown discord_user'
        allowed = self.cfg['discord_permissions'].get(persona)
        if allowed is None or not ('*' in allowed or user in allowed):
            return False, '%s may not send voice messages to %s' % (persona, user)
        return True, ''

    def send_voice(self, req):
        """Render the speech, convert it, send it as a voice message DM. Dry (request or add-on mode): render and measure, send nothing."""
        persona = str(req.get('persona') or '').strip().lower()
        user = str(req.get('discord_user') or '').strip().lower()
        text = str(req.get('text') or '').strip()
        ok, why = self.check_discord(req)
        p = {'allowed': ok, 'reason': why, 'text': text, 'persona': persona, 'discord_user': user, 'to': 'discord'}
        if not ok:
            return self._done('blocked', p)
        voice_id = self._voice(persona, req.get('voice'))
        if voice_id is None:
            p.update(allowed=False, reason='unknown voice')
            return self._done('blocked', p)
        engine = self._engine()
        dry = bool(req.get('dry_run')) or self.cfg['mode'] != 'live'
        p.update(engine=engine or 'fallback', voice=voice_id if engine == 'tts.piper' else '', format='ogg/opus voice message',
                 token_set=bool(self.cfg['discord_token']), caption=req.get('caption') is True)
        if dry:
            p['dry_run_reason'] = 'requested' if req.get('dry_run') else 'add-on mode is dry'
        if not engine:
            p['reason'] = 'neither TTS engine is available'
            return self._done('fallback', p)
        if not dry and not self.cfg['discord_token']:
            p['reason'] = 'discord_token is not set'
            return self._done('error', p)
        try:   # rendering plays nothing anywhere: no speaker, no volume change
            ogg, secs, wave = self._encode(self._audio(engine, text, voice_id, store=not dry))
        except Exception as e:
            p['reason'] = 'audio failed: %s' % (e if isinstance(e, voice.VoiceError) else type(e).__name__)
            return self._done('error', p)
        p.update(audio_bytes=len(ogg), duration_s=secs, waveform_bytes=len(wave) * 3 // 4)
        if dry:
            return self._done('dry', p)
        return self._deliver(p, self.cfg['discord_users'][user], ogg, secs, wave)

    def _deliver(self, p, user_id, ogg, secs, wave):
        d, tok = self._discord or _real_discord(), self.cfg['discord_token']
        try:
            d.send_voice_message(tok, user_id, ogg, secs, wave)
            p['delivery'] = 'voice_message'
        except Exception as e:
            self.log('voice message failed, trying a plain attachment', error=type(e).__name__)
            try:
                d.send_dm_rich(tok, user_id, '', file=(voice_filename(), ogg))
                p['delivery'] = 'attachment'
                p['reason'] = 'voice message refused (%s): sent as a plain audio file' % type(e).__name__
            except Exception as e2:
                p['reason'] = 'Discord send failed: %s' % type(e2).__name__
                return self._done('error', p)
        if p.get('caption'):   # a voice message cannot carry text: the caption is its own message
            try:
                d.send_dm(tok, user_id, p['text'])
            except Exception as e:
                p['reason'] = (p.get('reason', '') + ' caption failed: %s' % type(e).__name__).strip()
        return self._done('sent', p)

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
        res = dict(p, status=status, ok=status in ('spoken', 'sent', 'dry'))
        self.history.append({'at': self.now().isoformat(timespec='seconds'), 'status': status, 'persona': p.get('persona'),
                             'speaker': p.get('speaker') or p.get('discord_user'), 'to': p.get('to', 'speaker'), 'delivery': p.get('delivery', ''), 'reason': p.get('reason', ''), 'chars': len(p.get('text', ''))})
        self.log('speak', status=status, persona=p.get('persona'), speaker=p.get('speaker'), reason=p.get('reason', ''))
        return res


class Job:
    """The result of a clip fetch running in a thread."""
    def __init__(self):
        self.done, self.value, self.error = threading.Event(), None, None

    def wait(self, timeout):
        return self.done.wait(timeout)

    def ok(self):
        return self.done.is_set() and self.error is None

    def result(self, timeout=None):
        self.done.wait(timeout)
        if self.error:
            raise self.error
        return self.value


def sign_clip(token, cid, exp):
    """HMAC for a clip link (the speaker has no bearer header)."""
    return hmac.new(token.encode(), ('%d|%d' % (cid, exp)).encode(), hashlib.sha256).hexdigest()[:32]


def voice_filename():
    return 'voice-message.ogg'


def _real_discord():
    from ihlib import discord
    return discord
