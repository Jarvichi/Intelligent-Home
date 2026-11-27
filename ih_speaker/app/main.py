"""ih_speaker: small HTTP tool that speaks a message on a HomePod. Callers send the text; this add-on never writes it.
POST /speak (bearer api_token) | GET /status (bearer) | GET /healthz (open)."""
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ihlib import log as loglib   # noqa: E402
from ihlib.ha import HA           # noqa: E402
import cache as cachelib          # noqa: E402
import speaker as sp              # noqa: E402
import ui_server                  # noqa: E402
from ihlib import db as ihdb     # noqa: E402

VERSION = '0.4.3'
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
            if method == 'GET' and path.startswith('/clip/'):
                return self._clip(path)
            if not authed(spk.cfg, self.headers.get('Authorization')):
                return self._send(401, {'ok': False, 'why': 'bearer token required'})
            if os.path.exists(os.path.join(DATA, 'STOP')):
                return self._send(503, {'ok': False, 'why': 'STOP file present'})
            if method == 'GET' and path == '/status':
                c = spk.cfg
                return self._send(200, {'ok': True, 'version': VERSION, 'mode': c['mode'], 'speakers': sorted(c['speakers']),
                                        'personas': sorted(c['permissions']), 'discord_users': sorted(c['discord_users']), 'engines': c['engines'],
                                        'cache': spk.cache.stats() if spk.cache else None, 'waiting': {n: t.waiting() for n, t in spk.turns.items()}, 'recent': list(spk.history)[-10:]})
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

        def _clip(self, path):
            """A signed link a speaker can fetch (it sends no bearer): /clip/<id>.<ext>?exp=..&sig=.."""
            name = path.rsplit('/', 1)[1]
            q = parse_qs(urlparse(self.path).query)
            try:
                cid = int(name.split('.', 1)[0])
                exp, sig = int((q.get('exp') or ['0'])[0]), (q.get('sig') or [''])[0]
            except ValueError:
                return self._send(404, {'ok': False})
            tok = spk.cfg['api_token']
            if not tok or not spk.cache or exp < time.time() or not hmac.compare_digest(sig, sp.sign_clip(tok, cid, exp)):
                return self._send(404, {'ok': False})
            row = spk.cache.by_id(cid)
            if not row or name != '%d.%s' % (cid, row['ext']):
                return self._send(404, {'ok': False})
            try:
                data = spk.cache.read(row)
            except OSError:
                return self._send(404, {'ok': False})
            self.send_response(200)
            self.send_header('Content-Type', cachelib.MIME.get(row['ext'], 'audio/mpeg'))
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._route('GET')

        def do_POST(self):
            self._route('POST')
    return H


def main():
    opts = load_options()
    log = loglib.Logger.from_options(opts, prefix='ih_speaker')
    cfg = sp.parse_options(opts)
    database = ihdb.open_db(os.path.join(DATA, 'ih.db'), cachelib.MIGRATIONS)
    cache = cachelib.Cache(os.path.join(DATA, 'cache'), database, max_bytes=cfg['cache_mb'] * 1024 * 1024, days=cfg['cache_days'],
                             auto_pin_uses=cfg['auto_pin_uses'], auto_pin_days=cfg['auto_pin_days'])
    spk = sp.Speaker(HA(), opts, log=log, cache=cache)
    cache.expire()
    cache.evict()
    if cfg['seed_fillers'] and cfg['mode'] == 'live':
        threading.Thread(target=spk.seed_all, daemon=True).start()   # pinned filler phrases for each voice; plays nothing
    ui = ui_server.make_server(spk, int(os.environ.get('IHS_UI_PORT', '8832')))
    threading.Thread(target=ui.serve_forever, daemon=True).start()
    port = int(os.environ.get('IHS_PORT', '8831'))
    log('start', version=VERSION, mode=spk.cfg['mode'], speakers=len(spk.cfg['speakers']),
        token='set' if spk.cfg['api_token'] else 'NOT set (all requests refused)')
    ThreadingHTTPServer(('0.0.0.0', port), make_handler(spk)).serve_forever()


if __name__ == '__main__':
    main()
