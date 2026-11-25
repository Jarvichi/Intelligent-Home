"""Template ingress UI server for an IH add-on (copy to <addon>/app/ui_server.py).

Threaded, so a slow request never blocks /healthz. Serves the shared status page
(index.html, ui.js, ui.css, ih-ui.*) from ./ui (the add-on) or, when that folder is
missing, from the canonical ih_ui files one level up (used by the tests).
Only the ingress peer may read the page; /healthz is open for the Supervisor watchdog.
"""
import json, os, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
UI = HERE / 'ui' if (HERE / 'ui').is_dir() else HERE
CANON = HERE.parent if UI == HERE else None      # canonical ih_ui folder, template run in place
PEER = os.environ.get('IH_UI_PEER', '172.30.32.2')   # Supervisor ingress gateway ('' = allow all, tests)
# page URL -> (file in the add-on ui folder, file in canonical ih_ui)
ASSETS = {
    '/': ('index.html', 'status-page.html'), '/index.html': ('index.html', 'status-page.html'),
    '/ui.js': ('ui.js', 'status-page.js'), '/ui.css': ('ui.css', 'status-page.css'),
    '/ih-ui.js': ('ih-ui.js', 'components.js'), '/ih-ui.css': ('ih-ui.css', 'tokens.css'),
    '/ih-patterns.css': ('ih-patterns.css', 'patterns.css'),
}
TYPES = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8'}


def load_info():
    for base in (UI, HERE):
        if (base / 'ui.json').is_file():
            return json.loads((base / 'ui.json').read_text())
    return {}


INFO = load_info()


def check_health():
    """Probe the add-on's own health endpoint (ui.json health_port/health_path). No port = healthy."""
    port = INFO.get('health_port')
    if not port:
        return True, 'No health endpoint configured'
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d%s' % (port, INFO.get('health_path', '/healthz')), timeout=2) as r:
            return 200 <= r.status < 400, 'Health endpoint responded'
    except Exception as e:
        return False, type(e).__name__


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, typ):
        self.send_response(code)
        self.send_header('Content-Type', typ)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/healthz':
            return self.reply(200, b'ok', 'text/plain; charset=utf-8')
        if PEER and self.client_address[0] != PEER:
            return self.send_error(403)
        if path == '/api/status':
            ok, detail = check_health()
            return self.reply(200, json.dumps({'healthy': ok, 'detail': detail, 'checked_at': time.time()}).encode(), 'application/json; charset=utf-8')
        if path == '/ui.json':
            keys = ('title', 'purpose', 'description', 'navigation', 'access')
            return self.reply(200, json.dumps({k: INFO.get(k) for k in keys}).encode(), 'application/json; charset=utf-8')
        names = ASSETS.get(path)
        if not names:
            return self.send_error(404)
        for base, name in ((UI, names[0]), (CANON, names[1])):
            if base and (base / name).is_file():
                return self.reply(200, (base / name).read_bytes(), TYPES[Path(name).suffix])
        self.send_error(404)

    def log_message(self, *args):
        pass


def make_server(port):
    return ThreadingHTTPServer(('0.0.0.0', port), Handler)


if __name__ == '__main__':
    make_server(int(os.environ.get('IH_UI_PORT', '8811'))).serve_forever()
