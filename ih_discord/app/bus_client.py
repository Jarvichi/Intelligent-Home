#!/usr/bin/env python3
"""Intelligent Home Discord bus, v1: message schema, signing, replay guards, topic helpers, a module-side client and a fake bus.
stdlib only; paho-mqtt (1.6.1) is imported only by PahoBus. Same signing idea as schedule_client.py, extended: the WHOLE payload is signed.

Topics (module = a feature add-on, channel-key = a stable name, never a Discord channel number):
  ih/discord/in/<module>/<channel-key>   transport -> module   a Discord event (message or reaction)
  ih/discord/out/<module>                module -> transport   an operation (post, react, ...)
  ih/discord/res/<module>                transport -> module   the result of an operation
  ih/discord/ack/<module>                module -> transport   "I took message X" (sent BEFORE acting)
  ih/discord/hello/<module>              module -> transport   claims, version, heartbeat (every 30 s)
Signature = HMAC-SHA256(key_of_module, "v1<kind>|<module>|<topic>|<canonical_json(payload without sig)>").
canonical_json = sorted keys, no spaces, UTF-8. Every payload has v (int, 1), ts (unix seconds) and sig (hex).
Checks on receipt (open_message): version, signature (constant time), module/topic match, age (120 s, 60 s ahead), replay.
Keys and message text are never logged by this file: only short reason codes."""
import hashlib, hmac, json, os, re, threading, time, uuid
from collections import OrderedDict, deque

V = 1
ROOT = 'ih/discord'
KINDS = ('in', 'out', 'res', 'ack', 'hello')
MAX_AGE_S = 120
MAX_AHEAD_S = 60
MIN_KEY_LEN = 16
HEARTBEAT_S = 30
REPLAY_TTL_S = 3600
NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,40}$')
KEY_RE = re.compile(r'^[A-Za-z0-9_.-]{1,60}$')   # a channel key (module-created keys look like "module.suffix")
OPS = ('post', 'reply', 'react', 'unreact', 'edit', 'delete', 'delete_tagged', 'get_message', 'typing', 'watch', 'unwatch', 'expect')
ADMIN_OPS = ('create_category', 'create_channel', 'move_channel', 'set_topic', 'delete_channel', 'list_channels')   # S6: channel administration (design section 8); granted by flags, not by the `ops` list
CODES = ('ok', 'denied_channel', 'denied_op', 'rate_limited', 'not_owner', 'discord_error', 'unsupported_version', 'bad_sig', 'dry', 'bad_request')


# ---------- topics ----------
def topic_in(module, channel_key): return '%s/in/%s/%s' % (ROOT, module, channel_key)
def topic_out(module): return '%s/out/%s' % (ROOT, module)
def topic_res(module): return '%s/res/%s' % (ROOT, module)
def topic_ack(module): return '%s/ack/%s' % (ROOT, module)
def topic_hello(module): return '%s/hello/%s' % (ROOT, module)


def parse_topic(topic):
    """-> (direction, module, channel_key or None), or None when the topic is not a valid bus topic."""
    p = str(topic).split('/')
    if len(p) < 4 or p[:2] != ['ih', 'discord'] or p[2] not in KINDS or not NAME_RE.match(p[3]):
        return None
    if p[2] == 'in':
        return ('in', p[3], p[4]) if len(p) == 5 and KEY_RE.match(p[4]) else None
    return (p[2], p[3], None) if len(p) == 4 else None


def topic_matches(filt, topic):
    """MQTT wildcard match (+ and #)."""
    f, t = filt.split('/'), topic.split('/')
    for i, x in enumerate(f):
        if x == '#':
            return True
        if i >= len(t) or (x != '+' and x != t[i]):
            return False
    return len(f) == len(t)


# ---------- schema helpers ----------
def claim(channel_key, any=False, exact=None, prefix=None, regex=None, public=None):
    """One claim for a hello message. `match` entries: {"any":true} | {"exact":[words]} | {"prefix":"x"} | {"regex":"..."}.
    A claim with no match entry means any (catch-all)."""
    m = []
    if exact: m.append({'exact': [str(w) for w in exact]})
    if prefix: m.append({'prefix': str(prefix)})
    if regex: m.append({'regex': str(regex)})
    if any or not m: m.append({'any': True})
    return {'channel_key': channel_key, 'match': m, 'public': [str(w) for w in (public or [])]}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _mac(key, kind, module, topic, body):
    text = 'v1%s|%s|%s|%s' % (kind, module, topic, canonical(body))
    return hmac.new(key.encode('utf-8'), text.encode('utf-8'), hashlib.sha256).hexdigest()


def good_key(key):
    return isinstance(key, str) and len(key.strip()) >= MIN_KEY_LEN


def encode(key, kind, module, topic, body, clock=time.time):
    """-> the JSON text to publish: body + v + ts + sig. The key must be a string of 16+ characters."""
    if kind not in KINDS:
        raise ValueError('bad kind')
    if not good_key(key):
        raise ValueError('no usable signing key')
    d = {k: v for k, v in dict(body).items() if k != 'sig'}
    d['v'], d['ts'] = V, int(clock())
    d['sig'] = _mac(key.strip(), kind, module, topic, d)
    return canonical(d)


class ReplayGuard:
    """Accepts each key once. Memory is an LRU (max entries) with a time limit; with a path the last hour is also kept on disk, so a
    restart does not forget it (append-only file, compacted when it is read or when it has grown)."""
    def __init__(self, path=None, ttl=REPLAY_TTL_S, max_entries=2000, clock=time.time):
        self.path, self.ttl, self.max, self.clock = path, ttl, max_entries, clock
        self.d, self.lock, self.lines = OrderedDict(), threading.Lock(), 0
        if path:
            self._load()

    def _load(self):
        now = self.clock()
        try:
            with open(self.path, encoding='utf-8') as f:
                for ln in f:
                    ts, _, k = ln.rstrip('\n').partition('\t')
                    try:
                        t = float(ts)
                    except ValueError:
                        continue
                    if k and now - t <= self.ttl:
                        self.d[k] = t
        except OSError:
            return
        while len(self.d) > self.max:
            self.d.popitem(last=False)
        self._rewrite()

    def _rewrite(self):
        if not self.path:
            return
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                for k, t in self.d.items():
                    f.write('%d\t%s\n' % (t, k))
            os.replace(tmp, self.path)
            self.lines = len(self.d)
        except OSError:
            pass

    def seen(self, key):
        now = self.clock()
        with self.lock:
            t = self.d.get(key)
            return t is not None and now - t <= self.ttl

    def add(self, key):
        """True = new (now remembered). False = already seen (a replay)."""
        now = self.clock()
        with self.lock:
            t = self.d.get(key)
            if t is not None and now - t <= self.ttl:
                return False
            self.d[key] = now
            self.d.move_to_end(key)
            while len(self.d) > self.max:
                self.d.popitem(last=False)
            if self.path:
                try:
                    with open(self.path, 'a', encoding='utf-8') as f:
                        f.write('%d\t%s\n' % (now, key))
                    self.lines += 1
                    if self.lines > 4 * self.max:
                        self._rewrite()
                except OSError:
                    pass
            return True


def replay_key(kind, d):
    """What must be unique inside the replay window: op_id for out; id + attempt for in; the signature for the others."""
    if kind == 'out':
        return 'out:%s' % d.get('op_id')
    if kind == 'in':
        return 'in:%s:%s' % (d.get('id'), d.get('attempt', 1))
    return '%s:%s' % (kind, d.get('sig'))


def open_message(key, kind, module, topic, raw, clock=time.time, guard=None, max_age=MAX_AGE_S, max_ahead=MAX_AHEAD_S):
    """Verify one received message. -> (code, payload). code 'ok' means payload is trustworthy; otherwise payload is None, except
    'unsupported_version' (payload is the unverified dict, so a transport may answer with that code, signed by itself).
    Codes: ok, malformed, no_key, unsupported_version, cross_module, bad_sig, stale, future, replay."""
    if not good_key(key):
        return 'no_key', None
    try:
        d = json.loads(raw if isinstance(raw, str) else bytes(raw).decode('utf-8'))
    except Exception:
        return 'malformed', None
    if not isinstance(d, dict) or not isinstance(d.get('sig'), str):
        return 'malformed', None
    if isinstance(d.get('v'), bool) or not isinstance(d.get('v'), int):
        return 'malformed', None
    if d['v'] != V:
        return 'unsupported_version', d
    ts = d.get('ts')
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return 'malformed', None
    pt = parse_topic(topic)
    if pt is None or pt[0] != kind or pt[1] != module or ('module' in d and d['module'] != module):
        return 'cross_module', None
    if not hmac.compare_digest(d['sig'], _mac(key.strip(), kind, module, topic, {k: v for k, v in d.items() if k != 'sig'})):
        return 'bad_sig', None
    age = clock() - ts
    if age > max_age:
        return 'stale', None
    if -age > max_ahead:
        return 'future', None
    if guard is not None and not guard.add(replay_key(kind, d)):
        return 'replay', None
    return 'ok', d


# ---------- buses ----------
class FakeBus:
    """In-memory bus for tests (several clients can share one). Delivery is inline (synchronous), in publish order.
    Same interface as PahoBus: subscribe(topic, handler), start(), publish(topic, payload, retain=False), connected."""
    def __init__(self):
        self.subs, self.sent, self.connected, self.started = [], [], True, False
        self.drop = None   # optional fn(topic, payload) -> True to lose a message (QoS test)
        self.retained = {}

    def subscribe(self, topic, handler):
        self.subs.append((topic, handler))

    def start(self):
        self.started = True
        return True

    def publish(self, topic, payload, retain=False):
        if not self.connected:
            return False
        self.sent.append((topic, payload))
        if retain:
            self.retained[topic] = payload
        if self.drop and self.drop(topic, payload):
            return True
        for filt, h in list(self.subs):
            if topic_matches(filt, topic):
                h(topic, payload)
        return True

    def topics(self, kind=None):
        return [t for t, _ in self.sent if kind is None or t.split('/')[2] == kind]


class PahoBus:
    """Real MQTT. Connects only to the host you pass. NOT covered by tests (needs a broker). Wildcard topics work.
    will = (topic, payload): retained last-will. Reconnects by itself and re-subscribes."""
    def __init__(self, host, port, user, password, client_id, log=print, will=None):
        self.host, self.port, self.user, self.password, self.client_id, self.log, self.will = host, int(port), user, password, client_id, log, will
        self.connected, self._subs, self._c, self._last = False, [], None, 0
        self.on_connected = None   # optional callback run after each (re)connect

    def _say(self, m):
        if time.time() - self._last >= 60:
            self._last = time.time(); self.log(m)

    def subscribe(self, topic, handler):
        self._subs.append((topic, handler))
        if self.connected and self._c:
            self._c.subscribe(topic, 1)

    def start(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self._say('paho-mqtt not installed'); return False
        c = mqtt.Client(client_id=self.client_id)
        c.username_pw_set(self.user, self.password)
        c.reconnect_delay_set(min_delay=2, max_delay=60)
        if self.will:
            c.will_set(self.will[0], self.will[1], qos=1, retain=True)

        def on_connect(cl, ud, flags, rc):
            self.connected = rc == 0
            for t, _ in self._subs:
                cl.subscribe(t, 1)
            self._say('mqtt connected' if rc == 0 else 'mqtt refused (%s)' % rc)
            if rc == 0 and self.on_connected:
                try: self.on_connected()
                except Exception: pass

        def on_disconnect(cl, ud, rc):
            self.connected = False

        def on_message(cl, ud, msg):
            for t, h in list(self._subs):
                if topic_matches(t, msg.topic):
                    try:
                        h(msg.topic, msg.payload.decode('utf-8', 'replace'))
                    except Exception:
                        self._say('mqtt handler error')
        c.on_connect, c.on_disconnect, c.on_message = on_connect, on_disconnect, on_message
        self._c = c
        c.connect_async(self.host, self.port, keepalive=60)
        c.loop_start()
        return True

    def publish(self, topic, payload, retain=False):
        if not (self.connected and self._c):
            return False
        return self._c.publish(topic, payload, qos=1, retain=retain).rc == 0


def _snip(s, n=200):
    """First n chars as ONE line: backslash, quote and newlines escaped. Only used when log_payloads is true."""
    s = str(s or '')[:n]
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n')


def _tag(v, default='-'):
    """A short safe token for a log line (letters, digits and . _ : - only)."""
    t = re.sub(r'[^A-Za-z0-9._:-]', '_', str(v if v is not None else default))[:64]
    return t or default


# ---------- module side ----------
class ModuleClient:
    """What a feature add-on uses. It holds ONLY its own key and sees only channel keys.
        c = ModuleClient('ih_example', key, bus); c.on_message('chat', handler); c.start()
        handler(payload) gets the verified `in` dict (type message or reaction). An ack is sent BEFORE the handler runs.
        c.post('chat', 'hello') -> the `res` dict ({ok, code, discord_id, ...}) or None after the retries.
    Handlers run in one worker thread (threaded=True, keeps the bus thread free) or inline (threaded=False, tests)."""
    def __init__(self, module, key, bus, version='0', log=print, clock=time.time, threaded=True, claims=None, ops_needed=None,
                 offline_notice=False, heartbeat_s=HEARTBEAT_S, dry=False, retries=3, boot_id=None, seen_max=500, guard=None, log_payloads=False):
        if not NAME_RE.match(module):
            raise ValueError('bad module name')
        self.module, self.key, self.bus, self.version, self.log, self.clock = module, str(key or '').strip(), bus, version, log, clock
        self.threaded, self.claims, self.ops_needed, self.offline_notice = threaded, list(claims or []), list(ops_needed or []), offline_notice
        self.heartbeat_s, self.dry, self.retries = heartbeat_s, dry, retries
        self.boot_id = boot_id or uuid.uuid4().hex[:8]
        self.guard = guard or ReplayGuard(clock=clock)
        self.handlers, self.rejected, self.counter = {}, {}, 0
        self.seen_ids = OrderedDict(); self.seen_max = seen_max
        self.pending, self.last_hello = {}, 0.0
        self.lock = threading.Lock()
        self.q, self.cv, self._stop = deque(), threading.Condition(), False
        self.started = False
        self.log_payloads = bool(log_payloads)   # true: the invocation line also holds 200 chars of the request and the reply text
        self._tl = threading.local()

    @property
    def active(self):
        return good_key(self.key)

    # -- registration --
    def on_message(self, channel_key, handler, any=False, exact=None, prefix=None, regex=None, public=None):
        """Register a handler for one channel key, and claim it (default claim = catch-all)."""
        if not KEY_RE.match(channel_key):
            raise ValueError('bad channel key')
        self.handlers[channel_key] = handler
        self.claims = [c for c in self.claims if c['channel_key'] != channel_key] + [claim(channel_key, any=any, exact=exact, prefix=prefix, regex=regex, public=public)]
        self.bus.subscribe(topic_in(self.module, channel_key), self._on_in)
        return self

    def on_events(self, channel_key, handler):
        """Receive `in` events for a key WITHOUT claiming it (S6: the transport sends `channel_gone` on the key '_admin')."""
        if not KEY_RE.match(channel_key):
            raise ValueError('bad channel key')
        self.handlers[channel_key] = handler
        self.bus.subscribe(topic_in(self.module, channel_key), self._on_in)
        return self

    def start(self):
        if not self.active:
            self.log('bus: inactive (no usable signing key)')
            return False
        self.bus.subscribe(topic_res(self.module), self._on_res)
        ok = self.bus.start()
        if ok is False:
            return False
        self.started = True
        if self.threaded:
            threading.Thread(target=self._worker, daemon=True, name='bus-worker').start()
            threading.Thread(target=self._beat, daemon=True, name='bus-beat').start()
        self.hello(restart=True)
        return True

    def stop(self):
        self._stop = True
        with self.cv:
            self.cv.notify_all()

    # -- hello / heartbeat --
    def hello(self, restart=False):
        body = {'bus_api': V, 'module': self.module, 'version': str(self.version), 'boot_id': self.boot_id, 'claims': self.claims,
                'ops_needed': self.ops_needed, 'offline_notice': bool(self.offline_notice), 'restart': bool(restart)}
        self.last_hello = self.clock()
        return self._publish('hello', topic_hello(self.module), body)

    def tick(self, now=None):
        """Heartbeat: call about once a second (the thread does it). Sends hello when heartbeat_s has passed."""
        now = self.clock() if now is None else now
        if now - self.last_hello >= self.heartbeat_s:
            self.hello(); return True
        return False

    def _beat(self):
        while not self._stop:
            try: self.tick()
            except Exception: self.log('bus: heartbeat failed')
            time.sleep(1)

    def _publish(self, kind, topic, body):
        try:
            return self.bus.publish(topic, encode(self.key, kind, self.module, topic, body, self.clock))
        except Exception:
            self.log('bus: could not publish %s' % kind)
            return False

    def _reject(self, code, topic):
        self.rejected[code] = self.rejected.get(code, 0) + 1
        self.log('bus: rejected a message (%s)' % code)   # never the payload

    # -- receiving --
    def _on_in(self, topic, raw):
        pt = parse_topic(topic)
        code, d = open_message(self.key, 'in', self.module, topic, raw, self.clock, self.guard)
        if code != 'ok':
            self._reject(code, topic); return
        h = self.handlers.get(pt[2]) if pt else None
        if h is None:
            return
        mid = d.get('id')
        if mid is not None:
            if mid in self.seen_ids:   # a redelivered message we already took: ack again, do not act again
                self._publish('ack', topic_ack(self.module), {'ids': [mid]}); return
            self.seen_ids[mid] = True
            while len(self.seen_ids) > self.seen_max:
                self.seen_ids.popitem(last=False)
            self._publish('ack', topic_ack(self.module), {'ids': [mid]})   # ACK BEFORE ACT
        if self.threaded:
            with self.cv:
                self.q.append((h, d, pt[2])); self.cv.notify()
        else:
            self._run(h, d, pt[2])

    def _run(self, h, d, ck=None):
        """Run one handler = ONE invocation: logs ONE line (id, source, what, chars in/out, ms, ok, why). Text only when log_payloads is on."""
        t0 = time.time()
        self._tl.out, self._tl.ans = 0, ''
        ok, why = True, '-'
        try:
            h(d)
        except Exception as e:
            ok, why = False, type(e).__name__
            self.log('bus: handler failed (%s)' % type(e).__name__)
        try:
            self.log(self._inv_line(d, ok, why, t0, ck))
        except Exception:
            pass

    def _inv_line(self, d, ok, why, t0, ck=None):
        what = '%s:%s' % (_tag(d.get('type') or 'message'), _tag(ck or d.get('channel_key')))
        txt = str(d.get('content') or d.get('emoji') or '')
        f = ['invoke', 'id=' + _tag(d.get('id')), 'src=' + _tag(d.get('author_id') or d.get('user_id')), 'module=' + _tag(self.module), 'what=' + what,
             'in=%d' % len(txt), 'out=%d' % getattr(self._tl, 'out', 0), 'ms=%d' % int((time.time() - t0) * 1000), 'ok=%s' % bool(ok), 'why=' + _tag(why)]
        if self.log_payloads:
            f += ['req="%s"' % _snip(txt), 'ans="%s"' % _snip(getattr(self._tl, 'ans', ''))]
        return ' '.join(f)

    def _note_out(self, text):
        """Count (and keep the first 200 chars of) text this handler sends out; only read when log_payloads is on."""
        if text is None or not hasattr(self._tl, 'out'):
            return
        t = str(text)
        self._tl.out += len(t)
        if len(self._tl.ans) < 200:
            self._tl.ans = (self._tl.ans + (' | ' if self._tl.ans else '') + t)[:200]

    def _worker(self):
        while not self._stop:
            with self.cv:
                while not self.q and not self._stop:
                    self.cv.wait(1.0)
                item = self.q.popleft() if self.q else None
            if item:
                self._run(*item)

    def _on_res(self, topic, raw):
        code, d = open_message(self.key, 'res', self.module, topic, raw, self.clock, self.guard)
        if code != 'ok':
            self._reject(code, topic); return
        ev = self.pending.get(d.get('op_id'))
        if ev is not None:
            ev['res'] = d; ev['event'].set()

    # -- operations --
    def _op(self, op, args, wait=10.0):
        """Send one op. wait=0: do not wait, return the op_id. Otherwise wait for `res`, re-sending with the SAME op_id
        (the transport answers a repeated op_id with the old result, so nothing is done twice). -> res dict or None."""
        with self.lock:
            self.counter += 1
            op_id = '%s-%s-%d' % (self.module, self.boot_id, self.counter)
        body = dict(args); body['op'], body['op_id'] = op, op_id
        if self.dry:
            body['dry'] = True
        topic = topic_out(self.module)
        ev = {'event': threading.Event(), 'res': None}
        self.pending[op_id] = ev
        try:
            for _ in range(max(1, self.retries)):
                self._publish('out', topic, body)
                if not wait:
                    return op_id
                # threaded: wait up to `wait` s for the res (this is also the retry spacing). inline (tests): it has arrived already or never will
                if (ev['event'].wait(wait) if self.threaded else ev['event'].is_set()):
                    return ev['res']
            return None
        finally:
            if wait:
                self.pending.pop(op_id, None)

    def post(self, channel_key, text, reply_to=None, tag=None, **kw):
        a = {'channel_key': channel_key, 'text': text}
        if reply_to: a['reply_to'] = reply_to
        if tag: a['tag'] = tag
        self._note_out(text)
        return self._op('post', a, **kw)

    def reply(self, channel_key, text, reply_to, tag=None, **kw):
        a = {'channel_key': channel_key, 'text': text, 'reply_to': reply_to}
        if tag: a['tag'] = tag
        self._note_out(text)
        return self._op('reply', a, **kw)

    def react(self, channel_key, msg_id, emoji, **kw): return self._op('react', {'channel_key': channel_key, 'msg_id': msg_id, 'emoji': emoji}, **kw)

    def unreact(self, channel_key, msg_id, emoji, user_id=None, **kw):
        a = {'channel_key': channel_key, 'msg_id': msg_id, 'emoji': emoji}
        if user_id: a['user_id'] = user_id
        return self._op('unreact', a, **kw)

    def edit(self, channel_key, msg_id, text, **kw):
        self._note_out(text)
        return self._op('edit', {'channel_key': channel_key, 'msg_id': msg_id, 'text': text}, **kw)
    def delete(self, channel_key, msg_id, **kw): return self._op('delete', {'channel_key': channel_key, 'msg_id': msg_id}, **kw)
    def delete_tagged(self, channel_key, tag, **kw): return self._op('delete_tagged', {'channel_key': channel_key, 'tag': tag}, **kw)
    def get_message(self, channel_key, msg_id, **kw): return self._op('get_message', {'channel_key': channel_key, 'msg_id': msg_id}, **kw)
    def typing(self, channel_key, **kw): return self._op('typing', {'channel_key': channel_key}, **kw)

    def watch(self, channel_key, msg_id, emojis, ttl_s=180, tag=None, **kw):
        a = {'channel_key': channel_key, 'msg_id': msg_id, 'emojis': list(emojis), 'ttl_s': int(ttl_s)}
        if tag: a['tag'] = tag
        return self._op('watch', a, **kw)

    def unwatch(self, channel_key, msg_id, **kw): return self._op('unwatch', {'channel_key': channel_key, 'msg_id': msg_id}, **kw)
    def expect(self, channel_key, user_id, ttl_s=120, **kw): return self._op('expect', {'channel_key': channel_key, 'user_id': user_id, 'ttl_s': int(ttl_s)}, **kw)

    # -- channel administration (S6; the transport checks the module's grants, quotas and kill switches) --
    def create_category(self, name, **kw): return self._op('create_category', {'name': name}, **kw)

    def create_channel(self, name, category, key, topic=None, template=None, **kw):
        a = {'name': name, 'category': category, 'key': key}
        if topic: a['topic'] = topic
        if template: a['template'] = template
        return self._op('create_channel', a, **kw)

    def move_channel(self, channel_key, category, **kw): return self._op('move_channel', {'channel_key': channel_key, 'category': category}, **kw)
    def set_topic(self, channel_key, topic, **kw): return self._op('set_topic', {'channel_key': channel_key, 'topic': topic}, **kw)
    def delete_channel(self, channel_key, **kw): return self._op('delete_channel', {'channel_key': channel_key}, **kw)

    def list_channels(self, category=None, **kw):
        a = {'category': category} if category else {}
        return self._op('list_channels', a, **kw)

    def status(self):
        """Counts only: never keys, signatures or message content."""
        return {'active': self.active, 'rejected': dict(self.rejected), 'handled_ids': len(self.seen_ids)}
