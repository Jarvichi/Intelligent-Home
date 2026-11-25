"""Read-only ingress page for Room Insights. Answers only the Supervisor ingress gateway.
The data endpoint returns the engine's last result and nothing else (no token, no options)."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).with_name('ui')
PEER = '172.30.32.2'
ASSETS = {'/': 'index.html', '/index.html': 'index.html', '/ui.css': 'ui.css', '/ui.js': 'ui.js',
          '/ih-ui.css': 'ih-ui.css', '/ih-ui.js': 'ih-ui.js', '/ih-patterns.css': 'ih-patterns.css'}
TYPES = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8'}
# Only these keys of the engine result are ever sent. Options are never included.
ROOM_KEYS = ('room', 'current', 'target', 'min_per_deg', 'runs', 'minutes_to_target', 'frozen', 'frozen_why')


def public_data(eng, version):
    """The page's data, built from the engine object. Whitelisted fields only."""
    opts = eng.opts or {}
    last = eng.last or {}
    return {
        'version': version,
        'mode': 'live' if opts.get('mode') == 'live' else 'dry',
        'history_days': opts.get('history_days', 14),
        'frozen_hours': opts.get('frozen_hours', 6),
        'ran': bool(last),
        'at': last.get('at'),
        'mould_pct': last.get('mould_pct', opts.get('mould_pct', 70)),
        'mould_hours': last.get('mould_hours', opts.get('mould_hours', 2)),
        'rooms': [{k: r.get(k) for k in ROOM_KEYS} for r in (last.get('rooms') or {}).values()],
        'frozen': [{'room': f.get('room'), 'why': f.get('why'), 'since': f.get('since')} for f in (last.get('frozen') or [])],
        'mould': [{'sensor': m.get('room'), 'level': m.get('level'), 'minutes_above': m.get('minutes_above'), 'now': m.get('now')}
                  for m in (last.get('mould') or {}).values()],
    }


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # idle gateway connections reset/close often; nothing to log


def make_handler(eng, version):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                self._get()
            except (ConnectionResetError, BrokenPipeError):
                pass

        def _get(self):
            if self.client_address[0] != PEER:
                return self.send_error(403)
            path = self.path.split('?', 1)[0]
            if path == '/api/insights':
                body = json.dumps(public_data(eng, version)).encode()
                return self.reply(200, body, 'application/json; charset=utf-8')
            if path == '/ui.json':
                try:
                    return self.reply(200, (ROOT / 'ui.json').read_bytes(), 'application/json; charset=utf-8')
                except OSError:
                    return self.send_error(404)
            name = ASSETS.get(path)
            if not name:
                return self.send_error(404)
            try:
                body = (ROOT / name).read_bytes()
            except OSError:
                return self.send_error(404)
            self.reply(200, body, TYPES[os.path.splitext(name)[1]])

        def reply(self, code, body, typ):
            self.send_response(code)
            self.send_header('Content-Type', typ)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Handler


def make_server(eng, version, port, host='0.0.0.0'):
    return QuietServer((host, port), make_handler(eng, version))
