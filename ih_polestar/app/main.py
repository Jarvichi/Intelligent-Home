"""ih_polestar: read-only Polestar 2 readings. Two small HTTP servers: the token API (port 8813) and the ingress UI (port 8811, Home Assistant only)."""
import hmac, json, logging, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ihlib import db as ihdb
from ihlib.ha import HA
import engine as E

DATA = os.environ.get('IHPS_DATA', '/data')
PORT = int(os.environ.get('IHPS_PORT', '8813'))
UI_PORT = int(os.environ.get('IHPS_UI_PORT', '8811'))
UI = Path(__file__).with_name('ui')
INGRESS_PEER = '172.30.32.2'
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'),
          '/ui.css': ('ui.css', 'text/css; charset=utf-8'), '/ui.js': ('ui.js', 'text/javascript; charset=utf-8'),
          '/ih-ui.css': ('ih-ui.css', 'text/css; charset=utf-8'), '/ih-ui.js': ('ih-ui.js', 'text/javascript; charset=utf-8')}


def load_options():
    try:
        with open(os.path.join(DATA, 'options.json')) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class _Base(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype='application/json'):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def api_payload(eng, path):
    """Shared by both servers: returns (code, body) for /status and /readings, or None."""
    p = path.split('?', 1)[0]
    if p in ('/status', '/api/status'):
        return 200, eng.status()
    if p in ('/readings', '/api/readings'):
        return 200, {'readings': eng.readings(), 'latest': eng.last.get('reading')}
    return None


def make_token_handler(eng, token):
    class H(_Base):
        def do_GET(self):
            if self.path == '/healthz':
                return self._send(200, b'ok', 'text/plain')
            t = token()
            sent = (self.headers.get('Authorization') or '')[7:]
            if not t or not hmac.compare_digest(sent, t):
                return self._send(401, {'error': 'unauthorized'})
            r = api_payload(eng, self.path)
            self._send(*(r or (404, {'error': 'not found'})))
    return H


def make_ui_handler(eng):
    class H(_Base):
        def do_GET(self):
            if self.client_address[0] != INGRESS_PEER:
                return self.send_error(403)
            p = self.path.split('?', 1)[0]
            if p.startswith('/api/'):
                r = api_payload(eng, p)
                return self._send(*(r or (404, {'error': 'not found'})))
            a = ASSETS.get(p)
            if not a:
                return self.send_error(404)
            try:
                self._send(200, (UI / a[0]).read_bytes(), a[1])
            except OSError:
                self.send_error(404)
    return H


def main():
    lg = logging.getLogger('ihps')
    lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__)
    sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
    lg.addHandler(sh)
    opts = load_options()
    d = ihdb.open(os.path.join(DATA, 'ih.db'), E.MIGRATIONS, snapshot_dir=os.path.join(DATA, 'backup'))
    eng = E.Engine(d, HA(), opts, log=lg.info)
    mode = opts.get('mode', 'dry')
    publishing = mode in ('live', 'fixture')
    lg.info('ih_polestar %s starting: mode %s (read-only; no commands; %s)', E.VERSION, mode, 'publishes polestar_* sensors' if publishing else 'DRY: publishes nothing')

    def ticker():
        last_snap = 0
        while True:
            try:
                rd = eng.run_once()
                if rd is not None and publishing:
                    eng.publish(rd)
                lg.info('run: %s', eng.last['error'] or 'ok')
                if time.time() - last_snap > 86400:
                    ihdb.snapshot(os.path.join(DATA, 'ih.db'), os.path.join(DATA, 'backup'))
                    last_snap = time.time()
            except Exception as e:
                lg.info('run failed (%s)', type(e).__name__)
            time.sleep(max(5, int(opts.get('interval_min', 10))) * 60)
    threading.Thread(target=ticker, daemon=True).start()
    threading.Thread(target=ThreadingHTTPServer(('0.0.0.0', UI_PORT), make_ui_handler(eng)).serve_forever, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_token_handler(eng, lambda: opts.get('api_token', ''))).serve_forever()


if __name__ == '__main__':
    main()
