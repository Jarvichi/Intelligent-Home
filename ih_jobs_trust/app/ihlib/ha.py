"""Home Assistant access from an add-on: REST through the Supervisor proxy and a minimal stdlib websocket client.
Token = SUPERVISOR_TOKEN (or passed in). opener / connect are injectable for tests."""
import base64, json, os, select, socket, struct, time, urllib.parse, urllib.request
from datetime import datetime, timezone

REST_BASE = 'http://supervisor/core/api/'
WS_URL = 'ws://supervisor/core/websocket'


class HAError(RuntimeError):
    pass


def _iso(t):
    if isinstance(t, datetime):
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).isoformat()
    return str(t)


def _utc(t):
    """UTC 'Z' text of a datetime (naive = UTC)."""
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ---- minimal websocket client (text frames only) ----
class WS:
    def __init__(self, sock):
        self.s, self.buf = sock, b''

    def _read(self, n):
        while len(self.buf) < n:
            c = self.s.recv(65536)
            if not c:
                raise HAError('websocket closed')
            self.buf += c
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def wait(self, timeout):
        """True when a frame can be read now (data already buffered or arriving within `timeout` seconds). Used so recv() is never cut off half way by a timeout."""
        if self.buf:
            return True
        try:
            return bool(select.select([self.s], [], [], timeout)[0])
        except (OSError, ValueError):
            raise HAError('websocket closed')

    def send(self, obj):
        data = json.dumps(obj).encode()
        n, hdr = len(data), b'\x81'
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack('>Q', n)
        mask = os.urandom(4)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        msg = b''
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7f
            if n == 126:
                n = struct.unpack('>H', self._read(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._read(8))[0]
            payload = self._read(n)
            op = b1 & 0x0f
            if op == 8:
                raise HAError('websocket closed')
            if op == 9:   # ping -> pong (masked, empty)
                self.s.sendall(b'\x8a\x80' + os.urandom(4))
                continue
            msg += payload
            if b1 & 0x80:
                return json.loads(msg)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def _connect(url=WS_URL, timeout=20):
    u = urllib.parse.urlparse(url)
    s = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(('GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n'
               % (u.path or '/', u.netloc, key)).encode())
    head = b''
    while b'\r\n\r\n' not in head:
        c = s.recv(1)
        if not c:
            raise HAError('websocket handshake failed')
        head += c
    if b' 101 ' not in head.split(b'\r\n')[0]:
        raise HAError('websocket handshake refused')
    return WS(s)


class HA:
    def __init__(self, token=None, opener=None, connect=None, rest_base=REST_BASE, ws_url=WS_URL):
        self._token, self._opener, self._connect = token, opener, connect
        self.rest_base, self.ws_url = rest_base, ws_url

    @property
    def token(self):
        t = self._token if self._token is not None else os.environ.get('SUPERVISOR_TOKEN', '')
        if not t:
            raise HAError('no SUPERVISOR_TOKEN')
        return t

    def _rest(self, method, path, data=None, timeout=30):
        req = urllib.request.Request(self.rest_base + path.lstrip('/'), method=method, data=None if data is None else json.dumps(data).encode(),
                                     headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        with (self._opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def get(self, path, timeout=30):
        return self._rest('GET', path, None, timeout)

    def post(self, path, data=None, timeout=30):
        return self._rest('POST', path, {} if data is None else data, timeout)

    def state(self, entity):
        """State string of an entity, or None if unknown."""
        try:
            return (self.get('states/' + entity) or {}).get('state')
        except Exception:
            return None

    def call_service(self, domain, service, data=None):
        return self.post('services/%s/%s' % (domain, service), data or {})

    def history(self, entity, start, end=None):
        """List of {state, last_changed, attributes...} for one entity (REST, attributes included). start/end: datetime or ISO text."""
        q = {'filter_entity_id': entity, 'significant_changes_only': '0'}
        if end is not None:
            q['end_time'] = _iso(end)
        out = self.get('history/period/%s?%s' % (urllib.parse.quote(_iso(start)), urllib.parse.urlencode(q)), timeout=60)
        return (out or [[]])[0] if out else []

    def calendar_events(self, entity, start, end):
        """Events of a calendar entity between two datetimes (REST). Each is the raw HA dict (summary, start, end, location, uid ...)."""
        q = urllib.parse.urlencode({'start': _utc(start), 'end': _utc(end)})
        return self.get('calendars/%s?%s' % (entity, q), timeout=30) or []

    # ---- websocket ----
    def _open(self):
        ws = (self._connect or _connect)(self.ws_url)
        first = ws.recv()
        if first.get('type') != 'auth_required':
            raise HAError('unexpected websocket greeting')
        ws.send({'type': 'auth', 'access_token': self.token})
        if ws.recv().get('type') != 'auth_ok':
            raise HAError('websocket auth refused')
        return ws

    def ws_many(self, messages):
        """Send several {type: ...} commands over one connection. Returns list of results (in order). Raises HAError on a failed command."""
        ws, results = self._open(), []
        try:
            for i, m in enumerate(messages, 1):
                ws.send(dict(m, id=i))
                while True:
                    r = ws.recv()
                    if r.get('id') == i and r.get('type') == 'result':
                        break
                if not r.get('success'):
                    raise HAError('command %s failed: %s' % (m.get('type'), (r.get('error') or {}).get('code', '?')))
                results.append(r.get('result'))
        finally:
            ws.close()
        return results

    def ws(self, message):
        return self.ws_many([message])[0]

    def subscribe_events(self, event_type, on_event, max_seconds=600, on_idle=None, poll=1.0):
        """Listen for one HA event type over a websocket for at most `max_seconds`, then return (the caller starts the next pass).
        on_event(data_dict) is called for each event, on_idle() about every `poll` seconds when nothing arrived. Handlers run on this thread,
        so events that arrive meanwhile wait in the socket. Raises HAError if the subscription is refused or the connection drops."""
        ws = self._open()
        try:
            ws.send({'id': 1, 'type': 'subscribe_events', 'event_type': event_type})
            end = time.time() + max_seconds
            ready = False
            while time.time() < end:
                if ws.wait(poll):
                    m = ws.recv()
                    if m.get('id') == 1 and m.get('type') == 'result':
                        if not m.get('success'):
                            raise HAError('subscribe refused')
                        ready = True
                    elif m.get('type') == 'event' and ready:
                        d = (m.get('event') or {}).get('data')
                        on_event(d if isinstance(d, dict) else {})
                elif on_idle:
                    on_idle()
        finally:
            ws.close()
