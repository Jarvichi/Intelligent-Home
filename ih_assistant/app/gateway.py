"""Discord Gateway listener for one persona bot (0.15.0): a push signal, not a message reader.

The listener keeps one websocket to the Discord Gateway open and calls on_wake('message' | 'reaction') when the persona's
person writes or reacts in the private chat. The caller then fetches the message over REST exactly as before, so the
polling code stays the only place that reads message text. Nothing in this file keeps, returns or logs message content,
user ids or the token.

Three layers, each testable without a network:
  1. websocket frames (RFC 6455, client side, stdlib only): encode_frame, FrameParser, WsConn (over an injectable socket)
  2. Session: the Gateway protocol as a pure state machine (identify / resume / heartbeat / close codes) -> actions
  3. GatewayClient: the loop that connects, runs the Session and reconnects with backoff
"""
import base64, hashlib, json, os, random, socket, ssl, struct, threading, time, urllib.parse

# ---- Discord Gateway constants ----
GATEWAY_VERSION = 10
GATEWAY_HOST = 'gateway.discord.gg'
GATEWAY_QUERY = '/?v=%d&encoding=json' % GATEWAY_VERSION
GATEWAY_URL = 'wss://' + GATEWAY_HOST + GATEWAY_QUERY
GATEWAY_HOST_SUFFIX = '.discord.gg'      # a resume address outside this domain is ignored
GATEWAY_PORT = 443
INTENT_DIRECT_MESSAGES = 1 << 12
INTENT_DIRECT_MESSAGE_REACTIONS = 1 << 13
INTENTS = INTENT_DIRECT_MESSAGES | INTENT_DIRECT_MESSAGE_REACTIONS   # private chats only; neither is a privileged intent
OP_DISPATCH, OP_HEARTBEAT, OP_IDENTIFY, OP_RESUME, OP_RECONNECT = 0, 1, 2, 6, 7
OP_INVALID_SESSION, OP_HELLO, OP_HEARTBEAT_ACK = 9, 10, 11
MESSAGE_EVENTS = ('MESSAGE_CREATE',)
REACTION_EVENTS = ('MESSAGE_REACTION_ADD', 'MESSAGE_REACTION_REMOVE')
WAKE_MESSAGE, WAKE_REACTION = 'message', 'reaction'
# Close codes after which Discord will never accept this token / these settings again: stop, polling carries on.
CLOSE_FATAL = (4004, 4010, 4011, 4012, 4013, 4014)
# Close codes that end the session: connect again with a fresh identify, not a resume.
CLOSE_NEW_SESSION = (1000, 1001, 4007, 4009)
CLOSE_NORMAL = 1000
CLOSE_KEEP_SESSION = 4000                # our own close when we mean to resume (1000 / 1001 would end the session)
IDENTIFY_PROPERTIES = {'os': 'linux', 'browser': 'ih_assistant', 'device': 'ih_assistant'}

# ---- timing ----
CONNECT_TIMEOUT_S = 15
RECV_POLL_S = 1.0                        # longest wait in one read: bounds heartbeat lateness and the stop delay
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 300.0
BACKOFF_JITTER_MIN = 0.5                 # delay is multiplied by a random factor from this up to 1
INVALID_SESSION_WAIT_S = 5.0             # Discord asks for 1 to 5 s before a new identify
STOP_JOIN_S = 3.0

# ---- websocket constants ----
WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
WS_TEXT, WS_BINARY, WS_CONT, WS_CLOSE, WS_PING, WS_PONG = 0x1, 0x2, 0x0, 0x8, 0x9, 0xA
WS_FIN, WS_RSV, WS_OPCODE, WS_MASKED, WS_LEN = 0x80, 0x70, 0x0F, 0x80, 0x7F
WS_LEN_16, WS_LEN_64 = 126, 127
WS_MAX_SHORT, WS_MAX_16 = 125, 0xFFFF
WS_CONTROL_FROM = 0x8
WS_MASK_BYTES = 4
WS_NONCE_BYTES = 16
WS_MAX_MESSAGE = 4 * 1024 * 1024         # a larger message is refused (the events asked for are a few kB)
WS_READ_BYTES = 65536
WS_HANDSHAKE_MAX = 16 * 1024
WS_SWITCHING = b' 101 '
STATE_OFF, STATE_CONNECTING, STATE_READY, STATE_FAILED = 'off', 'connecting', 'ready', 'failed'


class WsError(Exception):
    """Protocol problem on the websocket. The text never holds payload data."""


class WsClosed(Exception):
    def __init__(self, code=None):
        Exception.__init__(self, 'closed')
        self.code = code


# ---------------------------------------------------------------- layer 1: websocket frames
def _mask(data, key):
    return bytes(b ^ key[i % WS_MASK_BYTES] for i, b in enumerate(data))


def encode_frame(opcode, payload=b'', mask_key=None):
    """One final frame. A client must mask (mask_key = 4 random bytes); None = unmasked (what a server sends)."""
    n = len(payload)
    head = bytes([WS_FIN | opcode])
    m = WS_MASKED if mask_key is not None else 0
    if n <= WS_MAX_SHORT:
        head += bytes([m | n])
    elif n <= WS_MAX_16:
        head += bytes([m | WS_LEN_16]) + struct.pack('!H', n)
    else:
        head += bytes([m | WS_LEN_64]) + struct.pack('!Q', n)
    if mask_key is None:
        return head + payload
    return head + mask_key + _mask(payload, mask_key)


class FrameParser:
    """feed(bytes) -> list of (opcode, payload): whole text / binary messages (fragments joined) and control frames."""
    def __init__(self, max_message=WS_MAX_MESSAGE):
        self.buf, self.max = b'', max_message
        self.parts, self.part_op, self.part_len = [], None, 0

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            f = self._frame()
            if f is None:
                return out
            fin, op, payload = f
            if op >= WS_CONTROL_FROM:
                if not fin or len(payload) > WS_MAX_SHORT:
                    raise WsError('bad control frame')
                out.append((op, payload))
                continue
            if op == WS_CONT:
                if self.part_op is None:
                    raise WsError('continuation without a start')
            else:
                if self.part_op is not None:
                    raise WsError('new message inside a fragmented one')
                self.part_op = op
            self.part_len += len(payload)
            if self.part_len > self.max:
                raise WsError('message too large')
            self.parts.append(payload)
            if fin:
                out.append((self.part_op, b''.join(self.parts)))
                self.parts, self.part_op, self.part_len = [], None, 0

    def _frame(self):
        b = self.buf
        if len(b) < 2:
            return None
        if b[0] & WS_RSV:
            raise WsError('reserved bits set')   # would mean compression, which is never asked for
        n, pos = b[1] & WS_LEN, 2
        if n == WS_LEN_16:
            if len(b) < pos + 2:
                return None
            n, pos = struct.unpack('!H', b[pos:pos + 2])[0], pos + 2
        elif n == WS_LEN_64:
            if len(b) < pos + 8:
                return None
            n, pos = struct.unpack('!Q', b[pos:pos + 8])[0], pos + 8
        if n > self.max:
            raise WsError('frame too large')
        key = None
        if b[1] & WS_MASKED:
            if len(b) < pos + WS_MASK_BYTES:
                return None
            key, pos = b[pos:pos + WS_MASK_BYTES], pos + WS_MASK_BYTES
        if len(b) < pos + n:
            return None
        payload, self.buf = b[pos:pos + n], b[pos + n:]
        return bool(b[0] & WS_FIN), b[0] & WS_OPCODE, _mask(payload, key) if key else payload


def accept_value(nonce):
    """The Sec-WebSocket-Accept value a server must answer for this Sec-WebSocket-Key."""
    return base64.b64encode(hashlib.sha1((nonce + WS_GUID).encode()).digest()).decode()


def handshake_request(host, path, nonce):
    return ('GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
            'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n' % (path, host, nonce)).encode()


def check_handshake(head, nonce):
    """head = the response bytes up to the blank line. Raises WsError unless the server switched protocols correctly."""
    lines = head.decode('latin-1').split('\r\n')
    if WS_SWITCHING.decode() not in lines[0] + ' ':
        raise WsError('handshake refused')
    hdr = {}
    for ln in lines[1:]:
        k, sep, v = ln.partition(':')
        if sep:
            hdr[k.strip().lower()] = v.strip()
    if hdr.get('sec-websocket-accept') != accept_value(nonce):
        raise WsError('handshake accept mismatch')


def split_url(url):
    """wss url -> (host, port, path with query)."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != 'wss' or not u.hostname:
        raise WsError('not a wss address')
    return u.hostname, u.port or GATEWAY_PORT, (u.path or '/') + ('?' + u.query if u.query else '')


def tls_socket(host, port):
    raw = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S)
    try:
        return ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    except Exception:
        raw.close()
        raise


class WsConn:
    """A client websocket over a socket-like object (sendall / recv / settimeout / close). Used from one thread only."""
    def __init__(self, url, sock_factory=tls_socket, rand=os.urandom):
        host, port, path = split_url(url)
        self.rand, self.parser, self.open = rand, FrameParser(), False
        self.sock = sock_factory(host, port)
        try:
            nonce = base64.b64encode(rand(WS_NONCE_BYTES)).decode()
            self.sock.settimeout(CONNECT_TIMEOUT_S)
            self.sock.sendall(handshake_request(host, path, nonce))
            buf = b''
            while b'\r\n\r\n' not in buf:
                if len(buf) > WS_HANDSHAKE_MAX:
                    raise WsError('handshake too long')
                chunk = self.sock.recv(WS_READ_BYTES)
                if not chunk:
                    raise WsClosed()
                buf += chunk
            head, _, rest = buf.partition(b'\r\n\r\n')
            check_handshake(head, nonce)
            self.open = True
            self.pending = self.parser.feed(rest) if rest else []
        except Exception:
            self._shut()
            raise

    def _send(self, opcode, payload=b''):
        self.sock.sendall(encode_frame(opcode, payload, self.rand(WS_MASK_BYTES)))

    def send_text(self, text):
        self._send(WS_TEXT, text.encode())

    def recv(self, timeout):
        """Wait up to timeout for data -> list of text payloads (bytes), [] on a quiet read. Pings are answered here.
        Raises WsClosed(code) when the server closes, OSError / WsError on a broken connection."""
        frames, self.pending = self.pending, []
        if not frames:
            self.sock.settimeout(timeout)
            try:
                data = self.sock.recv(WS_READ_BYTES)
            except (socket.timeout, ssl.SSLWantReadError):
                return []
            if not data:
                raise WsClosed()
            frames = self.parser.feed(data)
        out = []
        for op, payload in frames:
            if op == WS_PING:
                self._send(WS_PONG, payload)
            elif op == WS_CLOSE:
                code = struct.unpack('!H', payload[:2])[0] if len(payload) >= 2 else None
                self.close(code or CLOSE_NORMAL)
                raise WsClosed(code)
            elif op == WS_TEXT:
                out.append(payload)
            # binary and pong frames are not used by the JSON gateway: ignored
        return out

    def close(self, code=CLOSE_NORMAL):
        if self.open:
            self.open = False
            try:
                self._send(WS_CLOSE, struct.pack('!H', code))
            except OSError:
                pass
        self._shut()

    def _shut(self):
        self.open = False
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- layer 2: the Gateway protocol as a state machine
A_SEND, A_WAKE, A_RECONNECT, A_READY = 'send', 'wake', 'reconnect', 'ready'


class Session:
    """Pure protocol state for one bot. Methods take the current time and return a list of actions:
    (A_SEND, payload dict) | (A_WAKE, kind) | (A_RECONNECT, wait_s) | (A_READY,). No I/O, no threads."""
    def __init__(self, token, user_id, intents=INTENTS, rng=random.random):
        self._token, self.user_id, self.intents, self.rng = token, str(user_id), intents, rng
        self.seq = self.session_id = self.resume_url = None
        self.hb_interval = self.next_hb = None
        self.acked, self.ready = True, False

    def url(self):
        """Address for the next connection: the resume address while a session exists, else the public one."""
        if self.session_id and self.resume_url:
            try:
                host = split_url(self.resume_url)[0]
            except (WsError, ValueError):
                host = ''
            if host.endswith(GATEWAY_HOST_SUFFIX):
                return 'wss://' + host + GATEWAY_QUERY
        return GATEWAY_URL

    def can_resume(self):
        return bool(self.session_id) and self.seq is not None

    def forget(self):
        self.seq = self.session_id = self.resume_url = None

    def on_open(self):
        """A new connection: no heartbeat until HELLO, not ready until READY / RESUMED."""
        self.hb_interval = self.next_hb = None
        self.acked, self.ready = True, False

    def heartbeat(self):
        return {'op': OP_HEARTBEAT, 'd': self.seq}

    def on_payload(self, p, now):
        if not isinstance(p, dict):
            return []
        op = p.get('op')
        if op == OP_HELLO:
            d = p.get('d') if isinstance(p.get('d'), dict) else {}
            try:
                self.hb_interval = float(d.get('heartbeat_interval')) / 1000.0
            except (TypeError, ValueError):
                return [(A_RECONNECT, 0.0)]
            if self.hb_interval <= 0:
                return [(A_RECONNECT, 0.0)]
            self.next_hb = now + self.hb_interval * self.rng()   # first beat after a random part of the interval
            self.acked = True
            if self.can_resume():
                return [(A_SEND, {'op': OP_RESUME, 'd': {'token': self._token, 'session_id': self.session_id, 'seq': self.seq}})]
            return [(A_SEND, {'op': OP_IDENTIFY, 'd': {'token': self._token, 'intents': self.intents, 'properties': dict(IDENTIFY_PROPERTIES)}})]
        if op == OP_HEARTBEAT_ACK:
            self.acked = True
            return []
        if op == OP_HEARTBEAT:
            return [(A_SEND, self.heartbeat())]
        if op == OP_RECONNECT:
            return [(A_RECONNECT, 0.0)]
        if op == OP_INVALID_SESSION:
            if p.get('d') is not True:
                self.forget()
            return [(A_RECONNECT, INVALID_SESSION_WAIT_S)]
        if op == OP_DISPATCH:
            if isinstance(p.get('s'), int):
                self.seq = p['s']
            return self._dispatch(p.get('t'), p.get('d') if isinstance(p.get('d'), dict) else {})
        return []

    def _dispatch(self, t, d):
        if t == 'READY':
            self.session_id, self.resume_url, self.ready = d.get('session_id'), d.get('resume_gateway_url'), True
            return [(A_READY,)]
        if t == 'RESUMED':
            self.ready = True
            return [(A_READY,)]
        if d.get('guild_id'):
            return []   # only private chats matter (the intents already say so)
        if t in MESSAGE_EVENTS and str((d.get('author') or {}).get('id')) == self.user_id:
            return [(A_WAKE, WAKE_MESSAGE)]
        if t in REACTION_EVENTS and str(d.get('user_id')) == self.user_id:
            return [(A_WAKE, WAKE_REACTION)]
        return []

    def on_tick(self, now):
        """Heartbeat when due. A beat that was never acknowledged means a dead ("zombie") connection: reconnect and resume."""
        if self.next_hb is None or now < self.next_hb:
            return []
        if not self.acked:
            return [(A_RECONNECT, 0.0)]
        self.acked, self.next_hb = False, now + self.hb_interval
        return [(A_SEND, self.heartbeat())]

    def on_close(self, code):
        """The connection ended with this close code (None = dropped). -> True to try again, False = give up for good."""
        self.ready = False
        if code in CLOSE_FATAL:
            self.forget()
            return False
        if code in CLOSE_NEW_SESSION:
            self.forget()
        return True


class Backoff:
    """Reconnect delays: base, 2x, 4x ... capped at max, each shortened by random jitter. reset() after a good connection."""
    def __init__(self, base=BACKOFF_BASE_S, cap=BACKOFF_MAX_S, rng=random.random):
        self.base, self.cap, self.rng, self.fails = base, cap, rng, 0

    def reset(self):
        self.fails = 0

    def next(self):
        raw = min(self.cap, self.base * (2 ** self.fails))
        if raw < self.cap:
            self.fails += 1
        return raw * (BACKOFF_JITTER_MIN + (1.0 - BACKOFF_JITTER_MIN) * self.rng())


# ---------------------------------------------------------------- layer 3: the loop
class GatewayClient:
    """Runs one Session over reconnecting websockets. connect(url) -> object with send_text / recv(timeout) / close(code).
    on_wake(kind) is called from this client's thread. Logs carry the label, states, close codes and error type names only."""
    def __init__(self, label, token, user_id, on_wake, log=print, connect=WsConn, clock=time.monotonic, rng=random.random, wait=None):
        self.label, self.on_wake, self.log, self.connect, self.clock = label, on_wake, log, connect, clock
        self.session = Session(token, user_id, rng=rng)
        self.backoff = Backoff(rng=rng)
        self._stop = threading.Event()
        self.wait = wait or self._stop.wait      # wait(seconds); returns early when stopped
        self.state, self.thread, self.connects = STATE_OFF, None, 0

    def healthy(self):
        """True only while a connection is identified / resumed. The caller may slow its polling only then."""
        return self.state == STATE_READY

    def start(self):
        self.thread = threading.Thread(target=self.run, name='gateway-' + self.label, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()

    def join(self, timeout=STOP_JOIN_S):
        if self.thread:
            self.thread.join(timeout)

    def run(self):
        while not self._stop.is_set():
            self.state = STATE_CONNECTING
            again, min_wait = self.run_connection()
            if self._stop.is_set():
                break
            if not again:
                self.state = STATE_FAILED
                self.log('persona %s: gateway refused for good; polling only until restart' % self.label)
                return
            self.state = STATE_CONNECTING
            delay = max(min_wait, self.backoff.next())
            self.log('persona %s: gateway reconnect in %.0f s' % (self.label, delay))
            self.wait(delay)
        self.state = STATE_OFF

    def run_connection(self):
        """One connection from open to close. -> (try again?, minimum wait before the next one)."""
        s = self.session
        try:
            conn = self.connect(s.url())
        except WsClosed:
            return True, 0.0
        except (OSError, WsError, ValueError) as e:
            self.log('persona %s: gateway connect failed (%s)' % (self.label, type(e).__name__))
            return True, 0.0
        self.connects += 1
        s.on_open()
        close_code, again, min_wait = CLOSE_KEEP_SESSION, True, 0.0
        try:
            while not self._stop.is_set():
                actions = []
                for raw in conn.recv(RECV_POLL_S):
                    actions += s.on_payload(json.loads(raw), self.clock())
                actions += s.on_tick(self.clock())
                for a in actions:
                    if a[0] == A_SEND:
                        conn.send_text(json.dumps(a[1]))
                    elif a[0] == A_WAKE:
                        self.on_wake(a[1])
                    elif a[0] == A_READY:
                        self.state = STATE_READY
                        self.backoff.reset()
                        self.log('persona %s: gateway ready' % self.label)
                        self.on_wake(WAKE_MESSAGE)     # catch up on anything sent while the socket was down
                        self.on_wake(WAKE_REACTION)
                    elif a[0] == A_RECONNECT:
                        return True, a[1]
            close_code = CLOSE_NORMAL                   # stopped on purpose: end the session cleanly
        except WsClosed as e:
            again = s.on_close(e.code)
            self.log('persona %s: gateway closed (code %s)' % (self.label, e.code if e.code is not None else 'none'))
        except (OSError, WsError, ValueError) as e:      # ValueError covers bad JSON / bad UTF-8
            self.log('persona %s: gateway error (%s)' % (self.label, type(e).__name__))
        finally:
            s.ready = False
            if self.state == STATE_READY:
                self.state = STATE_CONNECTING
            try:
                conn.close(close_code)
            except Exception:
                pass
        return again, min_wait
