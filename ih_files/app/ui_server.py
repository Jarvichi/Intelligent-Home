"""Read-only IH page server for the Files add-on (Supervisor ingress only).

Threaded on purpose: the ingress gateway keeps idle connections open and a single-threaded server froze the page.
Page text and requests are never logged.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import files_info  # noqa: E402

INGRESS_PEER = '172.30.32.2'
UI_ROOT = Path(HERE) / 'ui'
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'),
          '/ui.css': ('ui.css', 'text/css; charset=utf-8'), '/ui.js': ('ui.js', 'application/javascript; charset=utf-8'),
          '/ui.json': ('ui.json', 'application/json; charset=utf-8'),
          '/ih-ui.css': ('ih-ui.css', 'text/css; charset=utf-8'), '/ih-ui.js': ('ih-ui.js', 'application/javascript; charset=utf-8'),
          '/ih-patterns.css': ('ih-patterns.css', 'text/css; charset=utf-8')}


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True   # one thread per connection: idle gateway connections must not block other requests

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        # no traceback text: it could carry request data


def make_handler(allowed_peers, status_fn):
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        timeout = 60

        def log_message(self, *a):
            pass

        def _send(self, code, body, typ='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', typ)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.client_address[0] not in allowed_peers:
                return self._send(403, b'forbidden', 'text/plain')
            path = self.path.split('?', 1)[0]
            # The gateway may prefix the ingress path; only the last part matters.
            if path.endswith('/api/status') or path == 'api/status':
                try:
                    data = status_fn()
                    data['checked_at'] = int(time.time())
                    return self._send(200, data)
                except Exception:
                    return self._send(500, {'error': 'internal error'})
            key = '/' + path.rsplit('/', 1)[-1]
            if key not in ASSETS:
                return self._send(404, b'not found', 'text/plain')
            name, typ = ASSETS[key]
            try:
                body = (UI_ROOT / name).read_bytes()
            except OSError:
                return self._send(404, b'not found', 'text/plain')
            self._send(200, body, typ)

        def do_POST(self):
            self._send(405, b'read only', 'text/plain')
        do_PUT = do_DELETE = do_PATCH = do_POST

    return H


def make_server(port, allowed_peers=(INGRESS_PEER,), status_fn=None, host='0.0.0.0'):
    return QuietServer((host, port), make_handler(set(allowed_peers), status_fn or files_info.build_status))


if __name__ == '__main__':
    port = int(os.environ.get('IHF_UI_PORT', '8099'))
    make_server(port).serve_forever()
