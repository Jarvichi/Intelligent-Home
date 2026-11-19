"""ih_speaker: small HTTP tool that speaks a message on a HomePod. Callers send the text; this add-on never writes it.
POST /speak (bearer api_token) | GET /status (bearer) | GET /healthz (open)."""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ihlib import log as loglib   # noqa: E402
from ihlib.ha import HA           # noqa: E402
import speaker as sp              # noqa: E402

VERSION = '0.2.0'
MAX_BODY = 8192
DATA = os.environ.get('IHS_DATA', '/data')


def load_options(path=None):
    try:
        with open(path or os.path.join(DATA, 'options.json')) as f:
            o = json.load(f)
        return o if isinstance(o, dict) else {}
    except (OSError, ValueError):
        return {}


def authed(cfg, header):
    tok = cfg['api_token']
    if not tok or not header or not header.startswith('Bearer '):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), tok.encode())


def make_handler(spk):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ct='application/json'):
            b = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
            self.send_response(code)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(b)

        def _route(self, method):
            path = urlparse(self.path).path.rstrip('/')
            if method == 'GET' and path == '/healthz':
                return self._send(200, 'ok', 'text/plain')
            if not authed(spk.cfg, self.headers.get('Authorization')):
                return self._send(401, {'ok': False, 'why': 'bearer token required'})
            if os.path.exists(os.path.join(DATA, 'STOP')):
                return self._send(503, {'ok': False, 'why': 'STOP file present'})
            if method == 'GET' and path == '/status':
                c = spk.cfg
                return self._send(200, {'ok': True, 'version': VERSION, 'mode': c['mode'], 'speakers': sorted(c['speakers']),
                                        'personas': sorted(c['permissions']), 'discord_users': sorted(c['discord_users']), 'engines': c['engines'],
                                        'waiting': {n: t.waiting() for n, t in spk.turns.items()}, 'recent': list(spk.history)[-10:]})
            if method == 'POST' and path == '/speak':
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
                return self._send(200, spk.speak(body))
            return self._send(404, {'ok': False})

        def do_GET(self):
            self._route('GET')

        def do_POST(self):
            self._route('POST')
    return H


def main():
    opts = load_options()
    log = loglib.Logger.from_options(opts, prefix='ih_speaker')
    spk = sp.Speaker(HA(), opts, log=log)
    port = int(os.environ.get('IHS_PORT', '8831'))
    log('start', version=VERSION, mode=spk.cfg['mode'], speakers=len(spk.cfg['speakers']),
        token='set' if spk.cfg['api_token'] else 'NOT set (all requests refused)')
    ThreadingHTTPServer(('0.0.0.0', port), make_handler(spk)).serve_forever()


if __name__ == '__main__':
    main()
