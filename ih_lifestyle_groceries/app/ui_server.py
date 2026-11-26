"""Read-only ingress page for the Groceries add-on (port 8802). Answers ONLY the Supervisor ingress gateway.
GET only; nothing here writes to the database or touches the shopping list. Page text and queries are never logged."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger as L, stats as S

UI_PORT = 8802
PEER = '172.30.32.2'
ROOT = Path(__file__).with_name('ui')
IDLE_TIMEOUT_S = 60
RECEIPT_LIMIT = 20
LIBRARY_LIMIT = 50
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'),
          '/ui.json': ('ui.json', 'application/json'),
          '/ih-ui.css': ('ih-ui.css', 'text/css; charset=utf-8'), '/ih-patterns.css': ('ih-patterns.css', 'text/css; charset=utf-8'),
          '/ui.css': ('ui.css', 'text/css; charset=utf-8'),
          '/ih-ui.js': ('ih-ui.js', 'text/javascript; charset=utf-8'), '/ui.js': ('ui.js', 'text/javascript; charset=utf-8')}


def summary(app):
    d = app.db
    c = L.q(d, 'SELECT (SELECT COUNT(*) FROM receipts) r, (SELECT COUNT(*) FROM (SELECT item_id AS k FROM item_aliases UNION SELECT key FROM receipt_lines)) i')[0]
    return {'mode': app.mode, 'mode_label': 'test mode' if app.mode != 'live' else 'live', 'items': c['i'], 'receipts': c['r'],
            'price_changes': len(S.price_changes(d, app.today()))}


def receipts(app):
    rows = L.q(app.db, 'SELECT bought_on, shop, total_p FROM receipts ORDER BY bought_on DESC, id DESC LIMIT ?', (RECEIPT_LIMIT,))
    return {'receipts': rows}


def library(app, text):
    """Search aliases and product keys. Each hit: name, id, times bought (distinct days), last bought."""
    like = '%' + str(text or '').lower().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    rows = L.q(app.db, "SELECT l.key, l.raw_name, r.bought_on FROM receipt_lines l JOIN receipts r ON r.id=l.receipt_id "
                       "WHERE l.cancelled=0 AND (lower(l.raw_name) LIKE ? ESCAPE '\\' OR lower(l.key) LIKE ? ESCAPE '\\') ORDER BY r.bought_on, l.id", (like, like))
    items = {}
    for r in rows:
        i = items.setdefault(r['key'], {'id': r['key'], 'name': r['raw_name'], 'days': set(), 'last_bought': ''})
        i['days'].add(r['bought_on']); i['name'] = r['raw_name']; i['last_bought'] = max(i['last_bought'], r['bought_on'])
    for a in L.q(app.db, "SELECT alias, item_id FROM item_aliases WHERE lower(alias) LIKE ? ESCAPE '\\' OR lower(item_id) LIKE ? ESCAPE '\\'", (like, like)):
        items.setdefault(a['item_id'], {'id': a['item_id'], 'name': a['alias'], 'days': set(), 'last_bought': ''})
    out = [{'id': i['id'], 'name': i['name'], 'times_bought': len(i['days']), 'last_bought': i['last_bought']} for i in items.values()]
    out.sort(key=lambda x: (-x['times_bought'], x['name']))
    return {'items': out[:LIBRARY_LIMIT], 'total': len(out)}


def route(app, path, query):
    if path == '/api/summary':
        return summary(app)
    if path == '/api/receipts':
        return receipts(app)
    if path == '/api/prices':
        return {'changes': S.price_changes(app.db, app.today())}
    if path == '/api/due':
        return {'due': S.due_items(app.db, app.today())}
    if path == '/api/library':
        return library(app, query.get('q', [''])[0][:80])
    return None


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            return
        print('ui server error: %s' % type(sys.exc_info()[1]).__name__, flush=True)


def make_ui_server(app, port=UI_PORT, host='0.0.0.0', peer=PEER):
    class H(BaseHTTPRequestHandler):
        timeout = IDLE_TIMEOUT_S

        def _send(self, code, data, ctype='application/json'):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.client_address[0] != peer:
                return self._send(403, {'error': 'forbidden'})
            u = urlparse(self.path)
            if u.path in ASSETS:
                name, ctype = ASSETS[u.path]
                try:
                    return self._send(200, (ROOT / name).read_bytes(), ctype)
                except OSError:
                    return self._send(404, {'error': 'not found'})
            try:
                data = route(app, u.path, parse_qs(u.query))
            except Exception as e:
                print('ui data error: %s' % type(e).__name__, flush=True)
                return self._send(500, {'error': 'could not read the data'})
            return self._send(404, {'error': 'not found'}) if data is None else self._send(200, data)

        def _no_write(self):
            self._send(405 if self.client_address[0] == peer else 403, {'error': 'read-only'})
        do_POST = do_PUT = do_DELETE = do_PATCH = _no_write

        def log_message(self, *a):
            pass
    return QuietServer((host, port), H)
