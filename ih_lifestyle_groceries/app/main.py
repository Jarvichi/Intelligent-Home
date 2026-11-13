"""ih_lifestyle_groceries: receipts ledger API (slice 2). Add-ons only (port not published). stdlib only.
GET  /healthz                      -> {ok, mode, receipts, lines}
GET  /stats/due | /stats/prices | /stats/weeks | /stats/week_diff?a=YYYY-MM-DD&b=YYYY-MM-DD   (read-only, always allowed)
POST /receipts   (Bearer api_token) store one whole receipt; in mode dry it only validates and answers what it WOULD store
POST /alias      (Bearer api_token) {"raw_name":..., "item_id":...}; dry = no write
No payment details are ever accepted or stored."""
import hmac, json, os, sys, threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger as L, stats as S

PORT = 8801
MAX_BODY = 512 * 1024


class App:
    def __init__(self, db, mode='dry', token='', today=date.today):
        self.db, self.mode, self.token, self.today = db, mode, token or '', today

    def authed(self, header):
        return bool(self.token) and hmac.compare_digest(str(header or '').encode(), ('Bearer ' + self.token).encode())

    def route(self, method, path, query, body, auth):
        """-> (status, object)"""
        d = self.db
        if method == 'GET':
            if path == '/healthz':
                c = L.q(d, 'SELECT (SELECT COUNT(*) FROM receipts) r, (SELECT COUNT(*) FROM receipt_lines) l')[0]
                return 200, {'ok': True, 'mode': self.mode, 'receipts': c['r'], 'lines': c['l']}
            if path == '/stats/due':
                return 200, {'due': S.due_items(d, self.today())}
            if path == '/stats/prices':
                return 200, {'changes': S.price_changes(d, self.today())}
            if path == '/stats/weeks':
                return 200, {'weeks': S.weeks(d, self.today())}
            if path == '/stats/week_diff':
                try:
                    return 200, S.week_diff(d, query.get('a', [''])[0], query.get('b', [''])[0])
                except ValueError as e:
                    return 400, {'ok': False, 'why': str(e)}
            return 404, {'ok': False}
        if method == 'POST' and path in ('/receipts', '/alias'):
            if not self.authed(auth):
                return 401, {'ok': False, 'why': 'token needed'}
            try:
                if path == '/alias':
                    if self.mode != 'live':
                        return 200, {'ok': True, 'dry': True, 'would_alias': L.norm_name(body.get('raw_name'))}
                    return 200, {'ok': True, 'alias': L.add_alias(d, body.get('raw_name'), body.get('item_id'))}
                if self.mode != 'live':
                    L.store_receipt(L.open_ledger(':memory:'), body)   # validate only, in a throwaway database
                    return 200, {'ok': True, 'dry': True, 'would_store': len(body.get('lines') or [])}
                st, rid, bal = L.store_receipt(d, body)
                return 200, {'ok': True, 'status': st, 'id': rid, 'balanced': bal}
            except L.LedgerError as e:
                return 400, {'ok': False, 'why': str(e)}
        return 404, {'ok': False}


def make_server(app, port=PORT, host='0.0.0.0'):
    class H(BaseHTTPRequestHandler):
        def _go(self, method):
            u = urlparse(self.path)
            body = {}
            if method == 'POST':
                n = int(self.headers.get('Content-Length') or 0)
                if n > MAX_BODY:
                    return self._send(413, {'ok': False, 'why': 'too large'})
                try:
                    body = json.loads(self.rfile.read(n) or b'{}')
                except ValueError:
                    return self._send(400, {'ok': False, 'why': 'not JSON'})
            self._send(*app.route(method, u.path, parse_qs(u.query), body, self.headers.get('Authorization')))
        def _send(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self): self._go('GET')
        def do_POST(self): self._go('POST')
        def log_message(self, *a): pass   # never log paths or bodies
    return ThreadingHTTPServer((host, port), H)


def main():
    data = os.environ.get('IHA_DATA', '/data')
    try:
        opts = json.load(open(os.path.join(data, 'options.json')))
    except OSError:
        opts = {}
    d = L.open_ledger(os.path.join(data, 'ih.db'), snapshot_dir=os.path.join(data, 'backup'))
    app = App(d, opts.get('mode', 'dry'), opts.get('api_token', ''))
    print('ih_lifestyle_groceries: mode %s, api on %d' % (app.mode, PORT), flush=True)
    make_server(app).serve_forever()


if __name__ == '__main__':
    main()
