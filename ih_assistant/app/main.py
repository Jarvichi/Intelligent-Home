"""Intelligent Home - Assistant - Personas (ih_assistant): transport, schedule and profile for several personas (stdlib only).
The reply text is written elsewhere (the host); this add-on moves messages in and out, queues check-in events and keeps one
SEPARATE datastore per persona. Dry-run by default."""
import base64, binascii, datetime, hmac, json, logging, os, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
from urllib.parse import urlparse, parse_qs
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store as storemod
from store import PersonaStore, StoreError, valid_key
from discord_io import Discord, DiscordError
from ihlib import log as ihlog

VERSION = '0.3.0'
MAX_BODY = 2 * 1024 * 1024   # the identity call carries a base64 avatar (up to 1 MiB)
NAME_CHANGES_PER_HOUR = 2
NAME_WINDOW_S = 3600
AVATAR_MAX = 1024 * 1024
BAD_NAME = ('discord', '@', '#', ':', '```')
TEXT_MAX = 4000
TELL_MAX = 2000
TELL_PER_DAY = 20
EVENT_TYPES = ('game_session', 'game_reset', 'reminder_due')
EVENT_PER_DAY = 30
EVENT_FACTS_MAX, EVENT_FACT_LEN, EVENT_SOURCE_LEN, EVENT_TEXT_LEN = 20, 300, 40, 1000
CHECKIN_WINDOW_MIN = 30
POLL_S = 8
TYPING_MAX_S = 300
ASK_KEEP_S = 86400
ASK_MAX = 10
REACT_EVERY_S = 20
ASK_EMOJI = {'yes': '\u2705', 'no': '\u274c', 'unsure': '\u2753'}
HHMM = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
MARK_RE = re.compile(r'^\d{1,25}$')


def hhmm(v, default):
    return v if isinstance(v, str) and HHMM.match(v) else default


def to_min(s):
    return int(s[:2]) * 60 + int(s[3:])


def load_options(path):
    """-> (opts, problems). Bad persona entries are skipped and listed in problems (no values quoted)."""
    problems = []
    try:
        o = json.load(open(path))
    except Exception:
        o = {}
    mode = 'live' if o.get('mode') == 'live' else 'dry'
    personas, seen = [], set()
    for i, p in enumerate(o.get('personas') or []):
        if not isinstance(p, dict) or not valid_key(p.get('key')):
            problems.append('persona #%d: bad key' % (i + 1)); continue
        if p['key'] in seen:
            problems.append('persona %s: duplicate key' % p['key']); continue
        seen.add(p['key'])
        try:
            mpd = int(p.get('max_per_day') if p.get('max_per_day') is not None else 4)
        except (TypeError, ValueError):
            mpd = 4
        personas.append({
            'key': p['key'], 'user_id': str(p.get('user_id') or ''), 'bot_token': str(p.get('bot_token') or ''),
            'quiet_start': hhmm(p.get('quiet_start'), '22:00'), 'quiet_end': hhmm(p.get('quiet_end'), '08:00'),
            'max_per_day': max(0, mpd),
            'checkins': sorted({c for c in (p.get('checkins') or []) if isinstance(c, str) and HHMM.match(c)}),
            'can_tell': [k for k in (p.get('can_tell') or []) if valid_key(k) and k != p['key']],
            'live': p.get('live') is True})
    return {'mode': mode, 'api_token': str(o.get('api_token') or ''), 'timezone': str(o.get('timezone') or 'UTC'),
            'personas': personas}, problems


def in_quiet(now_min, start, end):
    s, e = to_min(start), to_min(end)
    if s == e:
        return False
    return (s <= now_min < e) if s < e else (now_min >= s or now_min < e)


class Assistant:
    def __init__(self, opts, data_dir='/data', log=print, opener=None, sleep=time.sleep, clock=time.time, tz=None):
        self.opts, self.log, self.clock, self.sleep = opts, log, clock, sleep
        self.tz = tz
        self.cfg, self.stores, self.discord = {}, {}, {}
        for p in opts['personas']:
            k = p['key']
            self.cfg[k] = p
            self.stores[k] = PersonaStore(data_dir, k, clock)
            if p['bot_token'] and p['user_id']:
                self.discord[k] = Discord(p['bot_token'], opener, sleep)

    # ---- helpers ----
    def now(self):
        return datetime.datetime.fromtimestamp(self.clock(), self.tz) if self.tz else datetime.datetime.fromtimestamp(self.clock(), datetime.timezone.utc)

    def is_live(self, key):
        return self.opts['mode'] == 'live' and self.cfg[key]['live']

    def authed(self, header):
        tok = self.opts['api_token']
        if not tok or not header or not header.startswith('Bearer '):
            return False
        return hmac.compare_digest(header[7:].strip().encode(), tok.encode())

    def _day(self):
        return self.now().strftime('%Y-%m-%d')

    def _channel(self, key):
        """DM channel id of this persona's person (cached in the persona's own state)."""
        st = self.stores[key].state()
        if st.get('dm_channel'):
            return st['dm_channel']
        ch = self.discord[key].open_dm(self.cfg[key]['user_id'])
        self.stores[key].update_state(lambda s: s.__setitem__('dm_channel', ch))
        return ch

    def _count_out(self, key, kind):
        day = self._day()
        def f(s):
            o = s.setdefault('out', {})
            if o.get('day') != day:
                o.clear(); o['day'] = day
            o[kind] = int(o.get(kind, 0)) + 1
        self.stores[key].update_state(f)

    def out_today(self, key):
        o = self.stores[key].state().get('out', {})
        return (int(o.get('reply', 0)) + int(o.get('checkin', 0))) if o.get('day') == self._day() else 0

    # ---- scheduled work ----
    def tick_poll(self, key):
        """Fetch new private messages of the person into the persona's inbox. -> number added."""
        if key not in self.discord:
            return 0
        d, st, uid = self.discord[key], self.stores[key], self.cfg[key]['user_id']
        ch = self._channel(key)
        last = st.state().get('last_seen')
        if last is None:   # first run: start from the newest message, do not replay history
            m = d.messages_after(ch, None, 1)
            st.update_state(lambda s: s.__setitem__('last_seen', m[0]['id'] if m else '0'))
            return 0
        msgs = d.messages_after(ch, last, 50)
        if not msgs:
            return 0
        newest = max(msgs, key=lambda m: int(m['id']))['id']
        added = 0
        for m in sorted(msgs, key=lambda m: int(m['id'])):
            if str(m.get('author', {}).get('id')) != uid:
                continue
            self.maybe_intro(key)
            st.append_event({'type': 'message', 'msg_id': m['id'], 'text': str(m.get('content') or ''),
                             'attachments': len(m.get('attachments') or [])})
            added += 1
        st.update_state(lambda s: s.__setitem__('last_seen', newest))
        return added

    def maybe_intro(self, key):
        """Not introduced yet: put ONE intro_due event first (no name; the host writes the text)."""
        st = self.stores[key]
        if st.profile()['introduced'] or st.state().get('intro_requested'):
            return False
        st.append_event({'type': 'intro_due'})
        st.update_state(lambda s: s.__setitem__('intro_requested', True))
        return True

    def tick_checkins(self, key):
        """Queue checkin_due events whose time just passed (within CHECKIN_WINDOW_MIN), outside quiet hours, under max_per_day. -> list of times fired."""
        c, st, now = self.cfg[key], self.stores[key], self.now()
        day, nm = now.strftime('%Y-%m-%d'), now.hour * 60 + now.minute
        fired = []
        def done_for(s):
            cd = s.setdefault('checkins_done', {})
            if cd.get('day') != day:
                cd.clear(); cd['day'] = day; cd['times'] = []
            return cd['times']
        for t in c['checkins']:
            if t in done_for(st.state()):
                continue
            delta = nm - to_min(t)
            if delta < 0:
                continue
            if delta > CHECKIN_WINDOW_MIN:
                st.update_state(lambda s, t=t: done_for(s).append(t)); continue   # missed (add-on was off): drop, never fire late
            skip = in_quiet(nm, c['quiet_start'], c['quiet_end']) or self.out_today(key) >= c['max_per_day']
            st.update_state(lambda s, t=t: done_for(s).append(t))
            if skip:
                continue
            self.maybe_intro(key)
            st.append_event({'type': 'checkin_due', 'slot': t})
            self._count_out(key, 'checkin')
            fired.append(t)
        return fired

    def tick(self, key):
        n = f = 0
        try:
            n = self.tick_poll(key)
        except DiscordError as e:
            self.log('persona %s: poll failed (%s)' % (key, e))
        for fn in (self.tick_reactions, self.tick_typing):
            try:
                fn(key)
            except DiscordError as e:
                self.log('persona %s: %s failed (%s)' % (key, fn.__name__, e))
        try:
            f = len(self.tick_checkins(key))
        except StoreError as e:
            self.log('persona %s: checkin failed (%s)' % (key, e))
        return n, f

    # ---- API actions (return (http code, body)) ----
    def reply(self, key, body):
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
            return 400, {'ok': False, 'why': 'text required (1..%d characters)' % TEXT_MAX}
        rt = body.get('reply_to')
        rt = str(rt) if rt is not None and MARK_RE.match(str(rt)) else None
        st = self.stores[key]
        if not self.is_live(key):
            st.log_outbox({'kind': 'reply', 'chars': len(text), 'mode': 'dry'})
            self._count_out(key, 'reply')
            st.update_state(lambda s: s.pop('keepers', None))
            self.log('persona %s: [DRY] would send %d chars' % (key, len(text)))
            return 200, {'ok': True, 'sent': False, 'dry': True, 'chars': len(text)}
        if key not in self.discord:
            return 409, {'ok': False, 'why': 'no bot configured'}
        try:
            ok = self.discord[key].send(self._channel(key), text, rt)
        except DiscordError as e:
            self.log('persona %s: send failed (%s)' % (key, e))
            return 502, {'ok': False, 'why': 'discord: %s' % e}
        st.log_outbox({'kind': 'reply', 'chars': len(text), 'mode': 'live', 'ok': ok})
        if ok:
            st.update_state(lambda s: s.pop('keepers', None))
            self._count_out(key, 'reply')
            if not st.profile()['introduced'] and st.state().get('intro_requested'):
                st.merge_profile({'introduced': True})
        self.log('persona %s: sent %d chars' % (key, len(text)))
        return 200, {'ok': ok, 'sent': ok, 'dry': False, 'chars': len(text)}

    def typing(self, key, body=None):
        mid = (body or {}).get('msg_id')
        if mid is not None:
            if not MARK_RE.match(str(mid)):
                return 400, {'ok': False, 'why': 'msg_id must be a message id'}
            mid, now = str(mid), self.clock()
            def add(s):
                ks = [k for k in s.get('keepers', []) if k['msg_id'] != mid][-4:]
                ks.append({'msg_id': mid, 'start': now}); s['keepers'] = ks
            self.stores[key].update_state(add)
            if not self.is_live(key):
                self.log('persona %s: [DRY] would keep typing up to %d s' % (key, TYPING_MAX_S))
            return 200, {'ok': True, 'keeping': True, 'dry': not self.is_live(key)}
        if not self.is_live(key) or key not in self.discord:
            self.log('persona %s: [DRY] would show typing' % key)
            return 200, {'ok': True, 'sent': False, 'dry': True}
        try:
            self.discord[key].typing(self._channel(key))
        except DiscordError as e:
            return 502, {'ok': False, 'why': 'discord: %s' % e}
        return 200, {'ok': True, 'sent': True, 'dry': False}

    def ask(self, key, body):
        """Post a question as the persona with reaction buttons (yes / no / unsure). The user's reactions come back as 'reaction' events."""
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
            return 400, {'ok': False, 'why': 'text required (1..%d characters)' % TEXT_MAX}
        opts = body.get('options')
        if opts is None:
            opts = list(ASK_EMOJI)
        if not isinstance(opts, list) or not opts or any(o not in ASK_EMOJI for o in opts):
            return 400, {'ok': False, 'why': 'options: a list of yes, no, unsure'}
        st = self.stores[key]
        if not self.is_live(key):
            st.log_outbox({'kind': 'ask', 'chars': len(text), 'mode': 'dry', 'options': len(opts)})
            self._count_out(key, 'reply')
            self.log('persona %s: [DRY] would ask %d chars with %d reactions' % (key, len(text), len(opts)))
            return 200, {'ok': True, 'sent': False, 'dry': True, 'chars': len(text)}
        if key not in self.discord:
            return 409, {'ok': False, 'why': 'no bot configured'}
        d = self.discord[key]
        try:
            ch = self._channel(key)
            r = d.call('POST', '/channels/%s/messages' % ch, {'content': text[:1900]})
            mid = str(r['id'])
            for o in opts:
                d.react(ch, mid, ASK_EMOJI[o])
        except (DiscordError, KeyError, TypeError) as e:
            self.log('persona %s: ask failed (%s)' % (key, type(e).__name__))
            return 502, {'ok': False, 'why': 'discord error'}
        now = self.clock()
        def add(s):
            a = [x for x in s.get('asks', []) if now - x['ts'] < ASK_KEEP_S][-(ASK_MAX - 1):]
            a.append({'msg_id': mid, 'ts': now}); s['asks'] = a
            s.pop('keepers', None)
        st.update_state(add)
        st.log_outbox({'kind': 'ask', 'chars': len(text), 'mode': 'live', 'options': len(opts)})
        self._count_out(key, 'reply')
        self.log('persona %s: asked, %d chars' % (key, len(text)))
        return 200, {'ok': True, 'sent': True, 'dry': False, 'msg_id': mid}

    def tick_reactions(self, key):
        """Look at the reactions of the person on recent asks; new ones (and re-added ones) become 'reaction' events. -> number added."""
        if key not in self.discord:
            return 0
        st, d, uid, now = self.stores[key], self.discord[key], self.cfg[key]['user_id'], self.clock()
        s0 = st.state()
        asks = [a for a in s0.get('asks', []) if now - a['ts'] < ASK_KEEP_S]
        if not asks or now - s0.get('react_at', 0) < REACT_EVERY_S:
            return 0
        st.update_state(lambda s: s.__setitem__('react_at', now))
        ch = self._channel(key)
        seen, cur, added = set(s0.get('react_seen', [])), set(), 0
        for a in asks:
            m = d.call('GET', '/channels/%s/messages/%s' % (ch, a['msg_id']))
            for r in (m or {}).get('reactions', []):
                if r.get('count', 1) - (1 if r.get('me') else 0) <= 0:
                    continue
                e = r['emoji']
                name = e['name'] + (':' + e['id'] if e.get('id') else '')
                users = d.call('GET', '/channels/%s/messages/%s/reactions/%s?limit=10' % (ch, a['msg_id'], urllib.parse.quote(name))) or []
                if any(str(u.get('id')) == uid for u in users):
                    k = '%s:%s' % (a['msg_id'], name)
                    cur.add(k)
                    if k not in seen:
                        st.append_event({'type': 'reaction', 'msg_id': a['msg_id'], 'emoji': e['name']}); added += 1
        st.update_state(lambda s: s.__setitem__('react_seen', sorted(cur)[-100:]))
        return added

    def tick_typing(self, key):
        """Typing keepers: show typing every tick until the persona has posted after msg_id, or TYPING_MAX_S passes (then a still_waiting event)."""
        st = self.stores[key]
        ks = st.state().get('keepers', [])
        if not ks:
            return 0
        now, live, keep, done = self.clock(), self.is_live(key) and key in self.discord, [], 0
        for k in ks:
            if live:
                ch = self._channel(key)
                if any(str(m.get('author', {}).get('id')) != self.cfg[key]['user_id'] for m in self.discord[key].messages_after(ch, k['msg_id'], 20)):
                    continue   # the persona (or its bot) has answered: stop
            if now - k['start'] >= TYPING_MAX_S:
                st.append_event({'type': 'still_waiting', 'msg_id': k['msg_id']}); done += 1
                continue
            if live:
                self.discord[key].typing(self._channel(key))
            keep.append(k)
        st.update_state(lambda s: s.__setitem__('keepers', [k for k in s.get('keepers', []) if k in keep]))
        return done

    def tell(self, frm, to, body):
        """Persona-to-persona message: the ONLY cross-persona write. It hands one event to the target's own store and reads nothing of it."""
        if frm not in self.stores or to not in self.stores:
            return 404, {'ok': False, 'why': 'unknown persona'}
        if frm == to:
            return 400, {'ok': False, 'why': 'cannot tell yourself'}
        if to not in self.cfg[frm]['can_tell']:
            return 403, {'ok': False, 'why': 'not allowed (can_tell)'}
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > TELL_MAX:
            return 400, {'ok': False, 'why': 'text required (1..%d characters)' % TELL_MAX}
        day = self._day()
        def take(s):
            t = s.setdefault('tells', {})
            if t.get('day') != day:
                t.clear(); t['day'] = day
            if int(t.get(to, 0)) >= TELL_PER_DAY:
                return False
            t[to] = int(t.get(to, 0)) + 1
            return True
        if not self.stores[frm].update_state(take):
            return 429, {'ok': False, 'why': 'limit of %d a day for this pair' % TELL_PER_DAY}
        ev = {'type': 'from_persona', 'from': frm, 'text': text}
        if isinstance(body.get('kind'), str) and re.match(r'^[a-z_]{1,20}$', body['kind']):
            ev['kind'] = body['kind']
        stored = self.stores[to].append_event(ev)
        self.stores[frm].log_outbox({'kind': 'tell', 'to': to, 'chars': len(text)})
        self.log('persona %s: tell to %s, %d chars' % (frm, to, len(text)))
        return 200, {'ok': True, 'id': stored['id']}

    def event(self, key, body):
        """An add-on (e.g. ih_games) hands this persona one event for its mind to act on. Lands ONLY in this persona's own inbox. Logs type and lengths only."""
        if key not in self.stores:
            return 404, {'ok': False, 'why': 'unknown persona'}
        typ, src, facts, sug = body.get('type'), body.get('source'), body.get('facts'), body.get('suggested_text')
        if typ not in EVENT_TYPES:
            return 400, {'ok': False, 'why': 'type must be one of ' + ', '.join(EVENT_TYPES)}
        if not isinstance(src, str) or not src.strip() or len(src) > EVENT_SOURCE_LEN:
            return 400, {'ok': False, 'why': 'source: text 1..%d characters' % EVENT_SOURCE_LEN}
        if not isinstance(facts, dict) or len(facts) > EVENT_FACTS_MAX:
            return 400, {'ok': False, 'why': 'facts: object with up to %d keys' % EVENT_FACTS_MAX}
        for k, v in facts.items():
            if not isinstance(k, str) or not k or len(k) > 60 or not (v is None or isinstance(v, (str, int, float, bool))):
                return 400, {'ok': False, 'why': 'facts: flat, values text/number/true-false'}
            if isinstance(v, str) and len(v) > EVENT_FACT_LEN:
                return 400, {'ok': False, 'why': 'facts: each text up to %d characters' % EVENT_FACT_LEN}
        if sug is not None and (not isinstance(sug, str) or len(sug) > EVENT_TEXT_LEN):
            return 400, {'ok': False, 'why': 'suggested_text: up to %d characters' % EVENT_TEXT_LEN}
        day = self._day()
        def take(s):
            t = s.setdefault('events_in', {})
            if t.get('day') != day:
                t.clear(); t['day'] = day
            if int(t.get(src, 0)) >= EVENT_PER_DAY:
                return False
            t[src] = int(t.get(src, 0)) + 1
            return True
        if not self.stores[key].update_state(take):
            return 429, {'ok': False, 'why': 'limit of %d a day for this source' % EVENT_PER_DAY}
        stored = self.stores[key].append_event({'type': typ, 'source': src, 'facts': facts, 'suggested_text': sug})
        self.log('persona %s: event %s from %s, %d facts, %d chars' % (key, typ, src[:EVENT_SOURCE_LEN], len(facts), len(sug or '')))
        return 200, {'ok': True, 'id': stored['id']}

    def identity(self, key, body):
        """Change the persona's own username / avatar / server nickname. A username change is limited to 2 an hour (Discord's rule), counted per persona."""
        un, av, nick = body.get('username'), body.get('avatar'), body.get('nickname')
        if un is None and av is None and nick is None:
            return 400, {'ok': False, 'why': 'give username, avatar or nickname'}
        if un is not None:
            un = un.strip() if isinstance(un, str) else None
            if not un or not 2 <= len(un) <= 32 or any(b in un.lower() for b in BAD_NAME):
                return 400, {'ok': False, 'why': "username: 2..32 characters, without 'discord', '@', '#', ':' or ```"}
        raw = None
        if av is not None:
            try:
                if not isinstance(av, str):
                    raise ValueError
                raw = base64.b64decode(av.split(',', 1)[1] if av.startswith('data:') else av, validate=True)
            except (ValueError, binascii.Error, IndexError):
                return 400, {'ok': False, 'why': 'avatar: base64 PNG or JPEG data'}
            if len(raw) > AVATAR_MAX:
                return 400, {'ok': False, 'why': 'avatar: up to 1 MiB'}
            if raw[:8] == b'\x89PNG\r\n\x1a\n':
                mime = 'image/png'
            elif raw[:3] == b'\xff\xd8\xff':
                mime = 'image/jpeg'
            else:
                return 400, {'ok': False, 'why': 'avatar: must be a PNG or JPEG'}
        if nick is not None:
            nick = nick.strip() if isinstance(nick, str) else None
            if nick is None or len(nick) > 32 or any(b in nick.lower() for b in BAD_NAME):
                return 400, {'ok': False, 'why': "nickname: up to 32 characters (empty clears it), without 'discord', '@', '#', ':' or ```"}
        st, now = self.stores[key], self.clock()
        recent = [t for t in st.state().get('name_changes', []) if now - t < NAME_WINDOW_S]
        if un is not None and len(recent) >= NAME_CHANGES_PER_HOUR:
            wait = int(NAME_WINDOW_S - (now - min(recent))) + 1
            return 429, {'ok': False, 'why': 'username changes are limited to %d an hour' % NAME_CHANGES_PER_HOUR, 'retry_after': wait}
        if not self.is_live(key):
            st.log_outbox({'kind': 'identity', 'mode': 'dry', 'username': len(un) if un else 0, 'avatar': len(raw) if raw else 0, 'nickname': len(nick) if nick is not None else -1})
            self.log('persona %s: [DRY] would set username (%d chars) / avatar (%d bytes) / nickname (%d chars)' % (key, len(un or ''), len(raw or b''), len(nick or '')))
            return 200, {'ok': True, 'dry': True, 'changed': []}
        if key not in self.discord:
            return 409, {'ok': False, 'why': 'no bot configured'}
        d, changed = self.discord[key], []
        try:
            if un is not None or raw is not None:
                patch = {}
                if un is not None:
                    patch['username'] = un
                if raw is not None:
                    patch['avatar'] = 'data:%s;base64,%s' % (mime, base64.b64encode(raw).decode())
                d.call('PATCH', '/users/@me', patch)
                if un is not None:
                    st.update_state(lambda s: s.__setitem__('name_changes', recent + [now]))
                    st.merge_profile({'name': un})
                    changed.append('username')
                if raw is not None:
                    changed.append('avatar')
            if nick is not None:
                n = 0
                for g in d.call('GET', '/users/@me/guilds') or []:
                    d.call('PATCH', '/guilds/%s/members/@me' % g['id'], {'nick': nick or None}); n += 1
                changed.append('nickname (%d servers)' % n)
        except (DiscordError, KeyError, TypeError) as e:
            self.log('persona %s: identity failed (%s)' % (key, type(e).__name__))
            return 502, {'ok': False, 'why': 'discord error', 'changed': changed}
        st.log_outbox({'kind': 'identity', 'mode': 'live', 'changed': changed})
        self.log('persona %s: identity changed (%s)' % (key, ', '.join(changed)))
        return 200, {'ok': True, 'dry': False, 'changed': changed}

    def status(self):
        out = {}
        for k, c in self.cfg.items():
            s = self.stores[k]
            out[k] = {'live': self.is_live(k), 'bot': k in self.discord, 'inbox': s.inbox_count(), 'outbox': s.outbox_count(),
                      'out_today': self.out_today(k), 'checkins': len(c['checkins']), 'introduced': s.profile()['introduced'],
                      'can_tell': len(c['can_tell'])}
        return {'ok': True, 'version': VERSION, 'mode': self.opts['mode'], 'token_set': bool(self.opts['api_token']), 'personas': out}


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct='application/json'):
            b = (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode()
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(b)

        def _route(self, method):
            u = urlparse(self.path)
            parts = [p for p in u.path.split('/') if p]
            if method == 'GET' and parts == ['healthz']:
                return self._send(200, 'ok', 'text/plain')
            if not app.authed(self.headers.get('Authorization')):
                return self._send(401, {'ok': False, 'why': 'bearer token required'})
            if os.path.exists(os.path.join(os.environ.get('IHP_DATA', '/data'), 'STOP')):
                return self._send(503, {'ok': False, 'why': 'STOP file present'})
            if method == 'GET' and parts == ['status']:
                return self._send(200, app.status())
            if len(parts) < 3 or parts[0] != 'persona' or parts[1] not in app.stores:
                return self._send(404, {'ok': False})
            key, act = parts[1], parts[2]
            if method == 'GET' and act == 'inbox' and len(parts) == 3:
                q = parse_qs(u.query)
                try:
                    after = int(q.get('after', ['0'])[0]); limit = int(q.get('limit', ['100'])[0])
                except ValueError:
                    return self._send(400, {'ok': False, 'why': 'after and limit are numbers'})
                return self._send(200, {'ok': True, 'events': app.stores[key].read_inbox(after, limit)})
            if method == 'GET' and act == 'profile' and len(parts) == 3:
                return self._send(200, {'ok': True, 'profile': app.stores[key].profile()})
            if method != 'POST':
                return self._send(404, {'ok': False})
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413, {'ok': False, 'why': 'body too large'})
            try:
                body = json.loads(self.rfile.read(n) or b'{}')
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {'ok': False, 'why': 'JSON object required'})
            if act == 'reply' and len(parts) == 3:
                return self._send(*app.reply(key, body))
            if act == 'typing' and len(parts) == 3:
                return self._send(*app.typing(key, body))
            if act == 'ask' and len(parts) == 3:
                return self._send(*app.ask(key, body))
            if act == 'identity' and len(parts) == 3:
                return self._send(*app.identity(key, body))
            if act == 'event' and len(parts) == 3:
                return self._send(*app.event(key, body))
            if act == 'tell' and len(parts) == 4:
                return self._send(*app.tell(key, parts[3], body))
            if act == 'profile' and len(parts) == 3:
                try:
                    prof, ign = app.stores[key].merge_profile(body)
                except StoreError as e:
                    return self._send(400, {'ok': False, 'why': str(e)})
                return self._send(200, {'ok': True, 'profile': prof, 'ignored': ign})
            self._send(404, {'ok': False})

        def do_GET(self):
            self._route('GET')

        def do_POST(self):
            self._route('POST')

        def log_message(self, *a):
            pass
    return H


def worker(app, key):
    while True:
        try:
            app.tick(key)
        except Exception as e:
            app.log('persona %s: tick error %s' % (key, type(e).__name__))
        time.sleep(POLL_S)


def main():
    opts, problems = load_options(os.environ.get('IHP_OPTIONS', '/data/options.json'))
    secrets = [opts['api_token']] + [p['bot_token'] for p in opts['personas']] + [p['user_id'] for p in opts['personas']]
    lg = logging.getLogger('ih_assistant'); lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    log = ihlog.Logger(lg.info, [s for s in secrets if len(s) >= 4])
    tz = None
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(opts['timezone'])
    except Exception:
        log('timezone %s not available: using UTC' % opts['timezone'])
    for p in problems:
        log('option problem: ' + p)
    app = Assistant(opts, os.environ.get('IHP_DATA', '/data'), log, tz=tz)
    log('ih_assistant %s starting: mode %s, %d personas (%s), token %s' % (
        VERSION, opts['mode'], len(opts['personas']), ', '.join('%s=%s' % (k, 'live' if app.is_live(k) else 'dry') for k in app.cfg),
        'set' if opts['api_token'] else 'NOT set (all requests refused)'))
    for k in app.cfg:
        threading.Thread(target=worker, args=(app, k), daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('IHP_PORT', '8830'))), make_handler(app)).serve_forever()


if __name__ == '__main__':
    main()
