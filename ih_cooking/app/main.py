"""Intelligent Home - Cooking (ih_cooking): read-only recipes, meal plan and meal-vs-calendar clashes."""
import json, logging, os, sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cooking

PORT = int(os.environ.get('IHC_PORT', '8809'))


def make_handler(st):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct='application/json'):
            b = (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode()
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path); p = u.path.rstrip('/') or '/'; q = parse_qs(u.query)
            if p == '/healthz':
                return self._send(200, 'ok', 'text/plain')
            if not st.authed(self.headers.get('Authorization')):
                return self._send(401, {'ok': False, 'why': 'bearer token required'})
            if st.stopped():
                return self._send(503, {'ok': False, 'why': 'STOP file present'})
            if not st.rate_ok():
                return self._send(429, {'ok': False, 'why': 'rate limit'})
            try:
                if p == '/status':
                    return self._send(200, st.status())
                if p == '/recipes':
                    return self._send(200, {'recipes': st.recipes()})
                if p.startswith('/recipes/'):
                    r = st.recipe(p[len('/recipes/'):])
                    return self._send(200, r) if r else self._send(404, {'ok': False, 'why': 'no such recipe'})
                if p == '/plan':
                    plan, src = st.plan()
                    return self._send(200, {'plan': plan, 'source': src})
                if p == '/clashes':
                    days = max(1, min(31, int((q.get('days') or ['7'])[0])))
                    today = date.fromisoformat((q.get('today') or [date.today().isoformat()])[0])
                    return self._send(200, st.clashes(days, today))
            except ValueError:
                return self._send(400, {'ok': False, 'why': 'bad parameter'})
            except Exception as ex:
                return self._send(500, {'ok': False, 'why': type(ex).__name__})
            self._send(404, {'ok': False})

        def log_message(self, *a):
            pass
    return H


def main():
    lg = logging.getLogger('ih_cooking'); lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    opts = cooking.load_options(os.environ.get('IHA_OPTIONS', '/data/options.json'))
    st = cooking.Store(os.environ.get('IHA_DATA', '/data'), os.path.join(HERE, 'seed_recipes'), opts, log=lg.info)
    lg.info('ih_cooking %s starting: %d recipes; token %s; ha token %s; read-only', cooking.VERSION, len(st.recipes()),
            'set' if opts['api_token'] else 'NOT set (all requests refused)', 'set' if opts['ha_token'] else 'not set')
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(st)).serve_forever()


if __name__ == '__main__':
    main()
