"""Admin-only Home Assistant ingress for the Money application."""
import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import uuid
import store

INGRESS_PEER = '172.30.32.2'
UI_ROOT = Path(__file__).parent / 'ui'
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/ui.json': ('ui.json', 'application/json'),
          '/ih-ui.css': ('ih-ui.css', 'text/css; charset=utf-8'),
          '/ih-ui.js': ('ih-ui.js', 'text/javascript; charset=utf-8'),
          '/ui.css': ('ui.css', 'text/css; charset=utf-8'),
          '/ui.js': ('ui.js', 'text/javascript; charset=utf-8')}


def make_server(db, recipients, port=8824, peer=INGRESS_PEER, host='0.0.0.0'):
    class Handler(BaseHTTPRequestHandler):
        def output(self, status, data, content_type='application/json'):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            if self.client_address[0] != peer:
                self.output(403, {'error': 'forbidden'})
                return False
            return True

        def do_GET(self):
            if not self.allowed(): return
            path = self.path.split('?', 1)[0]
            if path == '/api/contracts':
                contracts = store.raw_records(db)
                return self.output(200, {'contracts': contracts,
                                         'projections': store.records(db, dt.date.today()),
                                         'series': {c['id']: store.projection_series(c) for c in contracts},
                                         'series_without_overpayments': {c['id']: store.projection_series(dict(c, loan_changes=[x for x in c.get('loan_changes', []) if x.get('type') not in ('overpayment', 'recurring_overpayment')])) for c in contracts if any(x.get('type') in ('overpayment', 'recurring_overpayment') for x in c.get('loan_changes', []))},
                                         'mortgage_terms': {c['id']: {'agreed': store.mortgage_term_series(c, False), 'plan': store.mortgage_term_series(c, True)} for c in contracts if c.get('mortgage_term_end')},
                                         'recipients': recipients})
            if path in ASSETS:
                name, content_type = ASSETS[path]
                try:
                    return self.output(200, (UI_ROOT / name).read_bytes(), content_type)
                except OSError:
                    return self.output(404, {'error': 'not found'})
            return self.output(404, {'error': 'not found'})

        def do_POST(self):
            if not self.allowed(): return
            if self.path.split('?', 1)[0] != '/api/contracts':
                return self.output(404, {'error': 'not found'})
            try:
                if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                    raise ValueError('JSON required')
                size = int(self.headers.get('Content-Length', '0'))
                if not 1 <= size <= 65536: raise ValueError('invalid body')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict): raise ValueError('invalid contract')
                cid = body.get('id')
                if cid:
                    if not isinstance(cid, str) or not db.execute('SELECT 1 FROM contracts WHERE id=?', (cid,)).fetchone():
                        return self.output(404, {'error': 'contract not found'})
                else:
                    body['id'] = uuid.uuid4().hex
                who = body.get('who', [])
                if not isinstance(who, list) or any(not isinstance(w, str) or w not in recipients for w in who):
                    raise ValueError('invalid recipient')
                store.save_contract(db, body)
                return self.output(200, {'ok': True, 'id': body['id']})
            except (ValueError, TypeError, KeyError, OverflowError):
                return self.output(400, {'error': 'Check the contract details and dates.'})
            except Exception:
                return self.output(500, {'error': 'Could not save the contract.'})

        def log_message(self, *args): pass
    return HTTPServer((host, port), Handler)
