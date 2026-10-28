"""Intelligent Home - Brain (ih_brain): serves the House Brain notes as an MCP server over HTTP (stdlib only)."""
import hmac, json, logging, os, sys, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brainlib, mcp

VERSION = '0.1.0'
MAX_BODY = 512 * 1024


def load_options(path):
    try:
        o = json.load(open(path))
    except Exception:
        o = {}
    return {'brain_dir': str(o.get('brain_dir') or '/share/brain'), 'api_token': str(o.get('api_token') or ''),
            'read_only': o.get('read_only', True) is not False, 'port': int(o.get('port') or 8820),
            'rate_per_min': int(o.get('rate_per_min') or 120)}


class State:
    def __init__(self, opts, data_dir='/data', log=print, clock=time.time):
        self.opts, self.log, self.clock = opts, log, clock
        self.brain = brainlib.Brain(opts['brain_dir'], data_dir, opts['read_only'])
        self.data_dir = data_dir
        self._hits, self._lock = [], threading.Lock()

    def authed(self, header):
        tok = self.opts['api_token']
        if not tok or not header or not header.startswith('Bearer '):
            return False
        return hmac.compare_digest(header[7:].strip().encode(), tok.encode())

    def stopped(self):
        return os.path.exists(os.path.join(self.data_dir, 'STOP'))

    def rate_ok(self):
        now = self.clock()
        with self._lock:
            self._hits = [t for t in self._hits if now - t < 60]
            if len(self._hits) >= self.opts['rate_per_min']:
                return False
            self._hits.append(now)
            return True

    def status(self):
        notes = self.brain._notes()
        return {'ok': True, 'version': VERSION, 'notes': len(notes), 'read_only': self.opts['read_only'],
                'last_index': self.brain.last_index, 'token_set': bool(self.opts['api_token'])}


def make_handler(st):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct='application/json'):
            b = (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode()
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(b)

        def _gate(self):
            """True if the request may go on (auth, kill switch, rate limit); else the answer is already sent."""
            if not st.authed(self.headers.get('Authorization')):
                self._send(401, {'ok': False, 'why': 'bearer token required'}); return False
            if st.stopped():
                self._send(503, {'ok': False, 'why': 'STOP file present'}); return False
            if not st.rate_ok():
                self._send(429, {'ok': False, 'why': 'rate limit'}); return False
            return True

        def do_GET(self):
            p = self.path.split('?')[0].rstrip('/') or '/'
            if p == '/healthz':
                return self._send(200, 'ok', 'text/plain')
            if not self._gate():
                return
            if p == '/status':
                return self._send(200, st.status())
            if p == '/mcp':
                self.send_response(405); self.send_header('Allow', 'POST'); self.send_header('Content-Length', '0'); self.end_headers(); return
            self._send(404, {'ok': False})

        def do_POST(self):
            p = self.path.split('?')[0].rstrip('/') or '/'
            if p not in ('/mcp', '/'):
                return self._send(404, {'ok': False})
            if not self._gate():
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413, {'ok': False, 'why': 'body too large'})
            try:
                msg = json.loads(self.rfile.read(n) or b'')
            except Exception:
                return self._send(400, {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}})
            resp = mcp.handle_rpc(st.brain, msg, VERSION)
            if resp is None:
                self.send_response(202); self.send_header('Content-Length', '0'); self.end_headers(); return
            self._send(200, resp)

        def log_message(self, *a):
            pass
    return H


def main():
    lg = logging.getLogger('ih_brain'); lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    opts = load_options(os.environ.get('IHA_OPTIONS', '/data/options.json'))
    st = State(opts, os.environ.get('IHA_DATA', '/data'), lg.info)
    lg.info('ih_brain %s starting: %d notes in %s; %s; token %s', VERSION, len(st.brain._notes()), opts['brain_dir'],
            'READ-ONLY' if opts['read_only'] else 'writes allowed', 'set' if opts['api_token'] else 'NOT set (all requests refused)')
    ThreadingHTTPServer(('0.0.0.0', opts['port']), make_handler(st)).serve_forever()


if __name__ == '__main__':
    main()
