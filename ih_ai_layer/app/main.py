"""Intelligent Home - AI Layer (ih_ai_layer): HTTP front for ailayer.Layer. Not connected to Discord or any chat."""
import json, logging, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ailayer

PORT = 8801
MAX_BODY = 16384


def make_handler(layer):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct='application/json'):
            body = json.dumps(body).encode() if isinstance(body, (dict, list)) else body.encode()
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

        def _authed(self):
            if layer.auth_ok(self.headers.get('Authorization')):
                return True
            self._send(401, {'ok': False, 'error': 'unauthorized'})
            return False

        def do_GET(self):
            p = self.path.split('?')[0]
            if p == '/healthz':
                self._send(200, 'ok', 'text/plain')
            elif p in ('/status', '/agents'):
                if self._authed():
                    self._send(200, layer.status() if p == '/status' else {'agents': layer.agents_view()})
            else:
                self.send_error(404)

        def do_POST(self):
            p = self.path.split('?')[0]
            n = int(self.headers.get('Content-Length') or 0)
            if p not in ('/request', '/feedback', '/register'):
                self.send_error(404); return
            if n > MAX_BODY:
                self.send_error(413); return
            raw = self.rfile.read(n)
            if p == '/register':
                code, r = layer.register(raw, self.headers.get(ailayer.jobs_link.HEADER)); self._send(code, r); return
            if not self._authed():
                return
            if layer.stopped():
                self._send(503, {'ok': False, 'error': 'stopped (kill switch)'}); return
            if not layer.rate_ok():
                self._send(429, {'ok': False, 'error': 'rate limit'}); return
            try:
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise ValueError
            except ValueError:
                self._send(400, {'ok': False, 'error': 'bad json'}); return
            code, r = layer.request(obj) if p == '/request' else layer.feedback(obj)
            self._send(code, r)

        def log_message(self, *a):
            pass
    return H


def main():
    lg = logging.getLogger('ih_ai_layer'); lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    opts = ailayer.load_options(os.environ.get('IHA_OPTIONS', '/data/options.json'))
    layer = ailayer.Layer(opts, data=os.environ.get('IHA_DATA', '/data'), log=lg.info)
    lg.info('ih_ai_layer %s starting: %s; api %s; agent link %s; payload logging %s', ailayer.VERSION, 'SHADOW (agents not called)' if opts['shadow'] else 'LIVE',
            'on' if opts['api_token'] else 'OFF (no api_token: everything refused)', 'on' if opts['register_key'] else 'OFF (no register_key)', 'ON' if opts['log_payloads'] else 'off')
    for n, p in sorted(layer.providers.items()):
        lg.info('provider %s %s: %s; model %s; cap %d/day', n, p.VERSION, 'configured' if p.configured() else 'NOT configured (no key)', p.opts['model'] if p.HAS_MODELS else '-', p.opts['daily_cap'])
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(layer)).serve_forever()


if __name__ == '__main__':
    main()
