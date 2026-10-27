#!/usr/bin/env python3
"""ih_discord_ai_bot 0.1.0: answers Discord messages with the AI layer, as a signed bus MODULE of the ih_discord transport.
The AI layer knows nothing about Discord. This module takes a message from a granted channel key, sends its text to the AI layer HTTP API
(POST /request, bearer api_token) and posts the answer back with a `post` op (reply to the message). It holds no Discord token, no channel number
and no AI key. Every detail (channel keys, allowed user ids, URL, token) comes from the add-on options, none from code.
Rules: only users in `allowed_user_ids` get an answer (empty = nobody); one answer per message (message ids remembered on disk, ack-before-act);
a global rate limit (`rate_per_min`); bot messages and this module's own posts are ignored; /data/STOP ignores everything; /data/STOP_LIVE forces dry.
If the AI layer is in shadow, down, refused the token or gave no answer, ONE short plain error is posted (never silence).
Message text and answers are never written to the log or /status.
SHADOW (option, default true): the AI layer IS asked, but the post op is sent with dry=true and logged '[DRY] would post'.
V1 = transport channels only: DMs and private channels are not on the bus. No Home Assistant access. stdlib only (paho-mqtt for the real bus)."""
import json, os, re, sys, threading, time, logging, collections, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bus_client

VERSION = '0.1.0'
DATA = os.environ.get('IHB_DATA', '/data')
OPTIONS = os.path.join(DATA, 'options.json')
PORT = int(os.environ.get('IHB_PORT', '8803'))
# no network address in code: ai_layer_url and ingest_url come from the options (config.yaml holds the shipping default)
PUSH_EVERY = 60
STOP_FILE = 'STOP'            # ignore every message
STOP_LIVE_FILE = 'STOP_LIVE'  # force dry
DEFAULT_MODULE = 'ih_discord_ai_bot'
OPS_NEEDED = ['post']
MAX_ASK = 4000                # the AI layer refuses longer text
MAX_REPLY = 1800              # the transport cuts at 1900
SEEN_MAX = 500
MAX_PARALLEL = 2
ID_OK = re.compile(r'^\d{1,32}$')
CAP_RE = re.compile(r'^[a-z0-9_]{1,40}$')
KEY_NAME_RE = bus_client.NAME_RE

MSG_SHADOW = 'The AI layer is in shadow mode, so no agent answered. Try again later.'
MSG_DOWN = 'The AI layer is not reachable right now. Try again later.'
MSG_AUTH = 'The AI layer refused my token, so I cannot ask it.'
MSG_NONE = 'The AI layer gave no answer. Try again later.'
MSG_LONG = 'That message is too long for me to send on. Please shorten it.'
MSG_BUSY = 'I am already working on other questions. Try again in a moment.'


def _text(v):
    return str(v or '').strip()


def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def load_options(path=None):
    try:
        with open(path or OPTIONS) as fh:
            o = json.load(fh)
    except (OSError, ValueError):
        o = {}
    if not isinstance(o, dict):
        o = {}
    name = _text(o.get('module_name')) or DEFAULT_MODULE
    keys = []
    for k in (o.get('channel_keys') if isinstance(o.get('channel_keys'), list) else []):
        k = _text(k) if isinstance(k, str) else ''
        if bus_client.KEY_RE.match(k) and k not in keys:
            keys.append(k)
    users = []
    for u in (o.get('allowed_user_ids') if isinstance(o.get('allowed_user_ids'), list) else []):
        u = _text(u) if isinstance(u, (str, int)) and not isinstance(u, bool) else ''
        if ID_OK.match(u) and u not in users:
            users.append(u)
    cap = _text(o.get('capability'))
    return {'module': name if KEY_NAME_RE.match(name) else DEFAULT_MODULE,
            'signing_key': _text(o.get('signing_key')), 'shadow': o.get('shadow') is not False,   # anything but an explicit false = shadow
            'channel_keys': keys, 'trigger_prefix': _text(o.get('trigger_prefix'))[:20], 'allowed_user_ids': users,
            'ai_layer_url': _text(o.get('ai_layer_url')).rstrip('/'), 'api_token': _text(o.get('api_token')),
            'capability': cap if CAP_RE.match(cap) else '',
            'rate_per_min': _int(o.get('rate_per_min'), 6, 1, 60), 'max_age_s': _int(o.get('max_age_s'), 300, 10, 3600),
            'ai_timeout_s': _int(o.get('ai_timeout_s'), 60, 5, 120),
            'mqtt_host': _text(o.get('mqtt_host')) or 'core-mosquitto', 'mqtt_port': _int(o.get('mqtt_port'), 1883, 1, 65535),
            'mqtt_user': _text(o.get('mqtt_user')) or DEFAULT_MODULE, 'mqtt_password': str(o.get('mqtt_password') or ''),
            'ingest_url': _text(o.get('ingest_url')).rstrip('/'), 'ingest_token': _text(o.get('ingest_token'))}


class Ring(logging.Handler):
    """Keeps the last lines in memory and counts what the module WOULD have done."""
    def __init__(self):
        super().__init__()
        self.lines = collections.deque(maxlen=100)
        self.would = 0
        self.would_lines = collections.deque(maxlen=30)

    def emit(self, rec):
        t = rec.getMessage()
        self.lines.append(time.strftime('%H:%M:%S ') + t[:300])
        if t.startswith('[DRY]'):
            self.would += 1
            self.would_lines.append(time.strftime('%H:%M:%S ') + t[:300])


def ask_ai(opts, text, rid, opener=None, timeout=None):
    """POST the text to the AI layer. -> (kind, text): ('answer', answer) | ('error', short plain message). Never raises, never logs."""
    if not opts['ai_layer_url']:
        return 'error', MSG_DOWN
    body = {'text': text, 'request_id': rid}
    if opts['capability']:
        body['capability'] = opts['capability']
    req = urllib.request.Request(opts['ai_layer_url'] + '/request', data=json.dumps(body).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + opts['api_token'], 'Content-Type': 'application/json'})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout or opts['ai_timeout_s']) as x:
            raw = x.read(200000)
        r = json.loads(raw)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return 'error', MSG_AUTH
        if e.code == 429:
            return 'error', 'The AI layer is busy (rate limit). Try again in a minute.'
        if e.code == 503:
            return 'error', 'The AI layer is stopped (kill switch).'
        return 'error', MSG_DOWN
    except Exception:
        return 'error', MSG_DOWN
    if not isinstance(r, dict):
        return 'error', MSG_NONE
    ans = r.get('answer')
    if r.get('mode') == 'dry' or (isinstance(ans, str) and ans.startswith('[dry]')):
        return 'error', MSG_SHADOW
    if not r.get('ok') or not isinstance(ans, str) or not ans.strip():
        # the layer's own failure text is short and plain ('No agent is available for ...'); cap it
        msg = ans.strip()[:200] if isinstance(ans, str) and ans.strip() else MSG_NONE
        return 'error', msg
    return 'answer', ans.strip()


class Module:
    def __init__(self, opts, bus=None, ring=None, data=None, clock=time.time, threaded=True, opener=None):
        self.opts, self.started, self.ring, self.data, self.clock = opts, clock(), ring or Ring(), data or DATA, clock
        self.lg = logging.getLogger('ihaibot')
        self.threaded, self.opener = threaded, opener
        self.lock = threading.RLock()
        self.error, self.client, self.bus, self.bus_reason = None, None, bus, ''
        self.in_count = self.answered = self.errors_posted = self.ignored = self.rate_limited = self.out_failed = self.busy = 0
        self.last_in = None
        self.ignored_why = collections.Counter()
        self.seen = collections.OrderedDict()   # message ids already taken (one answer per message), persisted
        self.own = collections.OrderedDict()    # ids of this module's own posts
        self.times = collections.deque()        # accepted-question times, for the rate limit
        self.sem = threading.BoundedSemaphore(MAX_PARALLEL)
        self.load_seen()

    # ---- mode ----
    def stopped(self):
        return os.path.exists(os.path.join(self.data, STOP_FILE))

    def dry_now(self):
        return bool(self.opts['shadow']) or os.path.exists(os.path.join(self.data, STOP_LIVE_FILE))

    def apply_mode(self):
        d = self.dry_now()
        if self.client:
            self.client.dry = d
        return d

    # ---- one answer per message: remembered on disk ----
    def seen_path(self):
        return os.path.join(self.data, 'seen.json')

    def load_seen(self):
        try:
            with open(self.seen_path()) as fh:
                for m in json.load(fh):
                    self.seen[str(m)] = True
        except (OSError, ValueError, TypeError):
            pass

    def remember(self, mid):
        self.seen[mid] = True
        while len(self.seen) > SEEN_MAX:
            self.seen.popitem(last=False)
        try:
            tmp = self.seen_path() + '.tmp'
            with open(tmp, 'w') as fh:
                json.dump(list(self.seen), fh)
            os.replace(tmp, self.seen_path())
        except OSError:
            self.lg.info('seen list not saved')

    # ---- build / start ----
    def build(self):
        if self.bus is None:
            if not self.opts['mqtt_password']:
                self.bus_reason = 'no mqtt password'
                return False
            self.bus = bus_client.PahoBus(self.opts['mqtt_host'], self.opts['mqtt_port'], self.opts['mqtt_user'], self.opts['mqtt_password'],
                                          self.opts['module'] + '_mod', log=lambda m: self.lg.info('bus: ' + m))
        self.client = bus_client.ModuleClient(self.opts['module'], self.opts['signing_key'], self.bus, version=VERSION, log=lambda m: self.lg.info(m),
                                              clock=self.clock, threaded=self.threaded, ops_needed=OPS_NEEDED, dry=True)
        pre = self.opts['trigger_prefix']
        for ck in self.opts['channel_keys']:
            if pre:
                self.client.on_message(ck, self.on_in, prefix=pre)   # specific claim: coexists with another module's catch-all
            else:
                self.client.on_message(ck, self.on_in, any=True)
        self.apply_mode()
        return True

    def start(self):
        if not self.build():
            return False
        ok = self.client.start()
        if not ok:
            self.bus_reason = 'no usable signing key' if not self.client.active else 'bus did not start'
        return bool(ok)

    # ---- events ----
    def _ignore(self, why):
        self.ignored += 1
        self.ignored_why[why] += 1

    def rate_ok(self):
        now = self.clock()
        while self.times and now - self.times[0] > 60:
            self.times.popleft()
        if len(self.times) >= self.opts['rate_per_min']:
            return False
        self.times.append(now)
        return True

    def on_in(self, d):
        """One verified `in` event (the ack was already sent). Decides, then asks the AI layer and posts ONE reply."""
        with self.lock:
            self.apply_mode()
            if d.get('type') != 'message':
                return
            self.in_count += 1
            self.last_in = self.clock()
            mid, ck, uid = _text(d.get('msg_id')), _text(d.get('channel_key')), _text(d.get('author_id'))
            if self.stopped():
                return self._ignore('stop_file')
            if not mid or ck not in self.opts['channel_keys']:
                return self._ignore('bad_event')
            if d.get('author_bot') or d.get('is_bot') or mid in self.own:
                return self._ignore('bot_or_own')
            if not d.get('allowed') or uid not in self.opts['allowed_user_ids']:
                return self._ignore('not_allowed')
            if mid in self.seen:
                return self._ignore('duplicate')
            age = d.get('age_s')
            if isinstance(age, (int, float)) and age > self.opts['max_age_s']:
                return self._ignore('stale')
            text = str(d.get('content') or '').strip()
            pre = self.opts['trigger_prefix']
            if pre and text.lower().startswith(pre.lower()):
                text = text[len(pre):].strip()
            if not text:
                return self._ignore('empty')
            self.remember(mid)   # ack-before-act: a crash after this loses the answer, it never doubles it
            if not self.rate_ok():
                self.rate_limited += 1
                return self._ignore('rate_limit')
            if len(text) > MAX_ASK:
                return self.reply(ck, mid, MSG_LONG, error=True)
            if not self.opts['api_token']:
                return self.reply(ck, mid, MSG_AUTH, error=True)
        if self.threaded:
            if not self.sem.acquire(blocking=False):
                with self.lock:
                    self.busy += 1
                    self.reply(ck, mid, MSG_BUSY, error=True)
                return
            threading.Thread(target=self._work, args=(ck, mid, text, True), daemon=True).start()
        else:
            self._work(ck, mid, text, False)

    def _work(self, ck, mid, text, release):
        try:
            kind, out = ask_ai(self.opts, text, 'dc' + mid, opener=self.opener)
            with self.lock:
                self.apply_mode()
                self.reply(ck, mid, out, error=(kind != 'answer'))
        except Exception as e:
            self.lg.info('work failed (%s)', type(e).__name__)
        finally:
            if release:
                self.sem.release()

    def reply(self, ck, mid, text, error=False):
        """Post ONE reply to message `mid`. Text is never logged."""
        text = text if len(text) <= MAX_REPLY else text[:MAX_REPLY - 3] + '...'
        if self.dry_now():
            self.lg.info('[DRY] would post %s reply (%d chars) to a message on %s', 'an error' if error else 'a', len(text), ck)
        res = self.client.post(ck, text, reply_to=mid, tag='ai')
        if res is None or not res.get('ok'):
            self.out_failed += 1
        else:
            did = res.get('discord_id')
            if did:
                self.own[str(did)] = True
                while len(self.own) > SEEN_MAX:
                    self.own.popitem(last=False)
        if error:
            self.errors_posted += 1
        else:
            self.answered += 1

    def tick(self):
        with self.lock:
            self.apply_mode()

    def healthy(self):
        return self.error is None

    def status(self):
        now = self.clock()
        hb = self.client.last_hello if self.client and self.client.last_hello else None
        return {'name': self.opts['module'], 'version': VERSION, 'shadow': bool(self.opts['shadow']), 'dry_now': self.dry_now(),
                'kill_switch': self.stopped(), 'stop_live': os.path.exists(os.path.join(self.data, STOP_LIVE_FILE)),
                'uptime_s': int(now - self.started), 'error': self.error, 'healthy': self.healthy(),
                'channel_keys': list(self.opts['channel_keys']), 'trigger_prefix_set': bool(self.opts['trigger_prefix']),
                'allowed_users': len(self.opts['allowed_user_ids']), 'ai_token_set': bool(self.opts['api_token']),
                'bus': {'connected': bool(self.bus and getattr(self.bus, 'connected', False)), 'active': bool(self.client and self.client.active),
                        'reason': self.bus_reason, 'last_heartbeat_s': None if hb is None else int(now - hb)},
                'counts': {'in': self.in_count, 'answered': self.answered, 'errors_posted': self.errors_posted, 'ignored': self.ignored,
                           'ignored_by': dict(self.ignored_why), 'rate_limited': self.rate_limited, 'busy': self.busy, 'out_failed': self.out_failed,
                           'rejected': sum((self.client.rejected if self.client else {}).values())},
                'last_in_s': None if self.last_in is None else int(now - self.last_in),
                'would_total': self.ring.would, 'last_would': list(self.ring.would_lines)[-10:]}


def push(mod, manifest, now=None, opener=None, log=print):
    """POST this module's manifest and status to the orchestrator. Never raises. The token is never printed."""
    o = mod.opts
    if not o['ingest_token'] or not o['ingest_url']:
        return False
    now = time.time() if now is None else now
    st = mod.status()
    body = json.dumps({'source': o['module'], 'manifests': [manifest],
                       'status': [{'name': o['module'], 'probed_at': now,
                                   'checks': [{'type': 'http', 'ok': st['healthy'] and st['bus']['active'],
                                               'detail': '%s, bus %s, %d answered, %d would-do' % ('shadow' if st['shadow'] else 'live', 'up' if st['bus']['connected'] else 'down',
                                                                                                  st['counts']['answered'], st['would_total'])}]}]}).encode()
    try:
        r = urllib.request.Request(o['ingest_url'] + '/ingest', data=body, method='POST',
                                   headers={'Authorization': 'Bearer ' + o['ingest_token'], 'Content-Type': 'application/json'})
        with (opener or urllib.request.urlopen)(r, timeout=5) as x:
            return x.status == 200
    except Exception as e:
        log('ih push: failed (%s)' % type(e).__name__)
        return False


def make_handler(mod):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            p = self.path.split('?')[0]
            if p == '/healthz':
                code, body, ct = (200 if mod.healthy() else 503), (b'ok' if mod.healthy() else b'not ok'), 'text/plain'
            elif p == '/status':
                code, body, ct = 200, json.dumps(mod.status()).encode(), 'application/json'
            else:
                self.send_error(404); return
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass
    return H


def main():
    ring = Ring()
    lg = logging.getLogger('ihaibot'); lg.setLevel(logging.INFO); lg.addHandler(ring)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    opts = load_options()
    mod = Module(opts, ring=ring)
    started = False
    try:
        started = mod.start()
    except Exception as e:
        mod.error = 'start failed (%s)' % type(e).__name__
    lg.info('ih_discord_ai_bot %s starting: %s, %d channel key(s), %d allowed user(s), AI token %s, %s', VERSION,
            'SHADOW (nothing is posted)' if opts['shadow'] else 'LIVE-CAPABLE (the transport decides)', len(opts['channel_keys']),
            len(opts['allowed_user_ids']), 'set' if opts['api_token'] else 'MISSING', 'bus started' if started else 'bus not started: %s' % (mod.bus_reason or mod.error))
    manifest = json.load(open(os.path.join(HERE, 'manifest.json')))

    def pusher():
        while True:
            push(mod, manifest, log=lg.info); time.sleep(PUSH_EVERY)

    def ticker():
        while True:
            try: mod.tick()
            except Exception as e: lg.info('tick failed (%s)', type(e).__name__)
            time.sleep(5)
    threading.Thread(target=pusher, daemon=True).start()
    threading.Thread(target=ticker, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(mod)).serve_forever()


if __name__ == '__main__':
    main()
