#!/usr/bin/env python3
"""ih_assets: things you own, a low and high resale value each, and a value-over-time history. Own database (/data/ih.db via ihlib.db).

API on IHA_PORT (bearer api_token; an empty token refuses everything except /healthz):
  GET /healthz (open)   GET /status   GET /summary   GET /items[?sold=1]   GET /items/<id>/prices   GET /history
  POST /items {record}   PATCH /items/<id> {fields}   DELETE /items/<id>   POST /items/<id>/sell {date?}
  POST /barcode {code, add?, category?, platform?}   (only the barcode number leaves the house)
The ingress page (IHA_UI_PORT, Home Assistant sign-in, admin only) uses the same routes under /api/.
mode "live" publishes sensor.assets_low and sensor.assets_high; "dry" (default) publishes nothing. Item names are never logged. Standard library only."""
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine as E
from ihlib import db as ihdb

DATA = os.environ.get('IHA_DATA', '/data')
PORT = int(os.environ.get('IHA_PORT', '8850'))
UI_PORT = int(os.environ.get('IHA_UI_PORT', '8851'))
INGRESS_PEER = '172.30.32.2'   # Supervisor ingress gateway
MAX_BODY = 64 * 1024
UI_ROOT = Path(HERE) / 'ui'
ASSETS = {'/': 'index.html', '/index.html': 'index.html', '/ui.css': 'ui.css', '/ui.js': 'ui.js', '/ih-ui.css': 'ih-ui.css', '/ih-ui.js': 'ih-ui.js',
          '/ih-patterns.css': 'ih-patterns.css'}
TYPES = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8'}


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True   # one thread per connection: the ingress gateway keeps idle connections open

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def load_options(path=None):
    try:
        o = json.load(open(path or os.path.join(DATA, 'options.json')))
    except (OSError, ValueError):
        o = {}
    mode = o.get('mode') if o.get('mode') in ('dry', 'live') else 'dry'
    try:
        mins = max(5, min(1440, int(o.get('interval_min', 60))))
    except (TypeError, ValueError):
        mins = 60
    return {'mode': mode, 'api_token': str(o.get('api_token') or ''), 'interval_min': mins}


def dispatch(a, opts, method, path, body, query='', get=None):
    """-> (status, object). Raises E.BadInput for a 400."""
    if method == 'GET' and path == '/summary':
        return 200, {'version': E.VERSION, 'mode': opts['mode'], 'totals': a.totals(), 'categories': list(E.CATEGORIES)}
    if method == 'GET' and path == '/items':
        return 200, {'items': a.items(include_sold='sold=1' in query)}
    if method == 'GET' and path == '/history':
        return 200, {'history': a.history()}
    if method == 'POST' and path == '/items':
        return 201, a.add(body())
    if method == 'POST' and path == '/barcode':
        b = body()
        r = E.barcode_lookup(b.get('code'), get)
        if b.get('add') and r['name']:
            r['item'] = a.add({'name': r['name'], 'category': b.get('category') or 'other', 'platform': b.get('platform'),
                               'source': 'barcode ' + r['code'], 'barcode': r['code']})
        return 200, r
    m = re.fullmatch(r'/items/(\d+)(?:/(sell|prices))?', path)
    if m:
        iid = int(m.group(1))
        if method == 'GET' and m.group(2) == 'prices':
            r = {'prices': a.prices(iid)} if a.get(iid) else None
        elif method == 'POST' and m.group(2) == 'sell':
            r = a.sell(iid, (body() or {}).get('date'))
        elif method == 'PATCH' and not m.group(2):
            r = a.update(iid, body())
        elif method == 'DELETE' and not m.group(2):
            r = {'deleted': True} if a.delete(iid) else None
        else:
            return 405, {'error': 'method not allowed'}
        return (200, r) if r else (404, {'error': 'no such item'})
    return 404, {'error': 'not found'}


def make_handler(app, ui, peer=INGRESS_PEER):
    """app = {'a': Assets, 'opts': options dict, 'get': optional barcode fetcher (tests)}."""
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):
            pass

        def _send(self, code, obj, typ='application/json; charset=utf-8'):
            body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', typ)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self):
            if ui:
                if self.client_address[0] != peer:
                    self._send(403, {'error': 'forbidden'})
                    return False
                return True
            tok = app['opts']['api_token']
            given = self.headers.get('Authorization', '')
            if tok and given.startswith('Bearer ') and hmac.compare_digest(given[7:].encode(), tok.encode()):
                return True
            self._send(401, {'error': 'token required'})
            return False

        def _body(self):
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                raise E.BadInput('bad length')
            if n > MAX_BODY:
                raise E.BadInput('body too large')
            if n == 0:
                return {}
            try:
                d = json.loads(self.rfile.read(n))
            except ValueError:
                raise E.BadInput('body is not JSON')
            if not isinstance(d, dict):
                raise E.BadInput('body must be an object')
            return d

        def _run(self, method):
            u = urlparse(self.path)
            path = u.path
            if method == 'GET' and path == '/healthz':
                return self._send(200, b'ok', 'text/plain')
            if ui and method == 'GET' and path in ASSETS:
                if not self._allowed():
                    return
                try:
                    body = (UI_ROOT / ASSETS[path]).read_bytes()
                except OSError:
                    return self._send(404, {'error': 'not found'})
                return self._send(200, body, TYPES[os.path.splitext(ASSETS[path])[1]])
            if not self._allowed():
                return
            if ui:
                if not path.startswith('/api/'):
                    return self._send(404, {'error': 'not found'})
                path = path[4:]
            if method == 'GET' and path == '/status':
                a = app['a']
                return self._send(200, {'version': E.VERSION, 'mode': app['opts']['mode'], 'api_token_set': bool(app['opts']['api_token']),
                                        'schema': a.db.schema_version(), 'items': len(a.items()), 'last_tick': app.get('last_tick')})
            try:
                code, out = dispatch(app['a'], app['opts'], method, path.rstrip('/') or '/', self._body, u.query, app.get('get'))
                self._send(code, out)
            except E.BadInput as e:
                self._send(400, {'error': str(e)})
            except Exception as e:
                logging.getLogger('iha').info('request failed (%s)', type(e).__name__)
                self._send(500, {'error': 'internal error'})

        def do_GET(self): self._run('GET')
        def do_POST(self): self._run('POST')
        def do_PATCH(self): self._run('PATCH')
        def do_DELETE(self): self._run('DELETE')
    return H


def serve(server, name, log):
    def loop():
        try:
            server.serve_forever()
        except Exception as e:
            log('%s server stopped (%s)' % (name, type(e).__name__))
            os._exit(1)     # the Supervisor watchdog restarts the add-on
    t = threading.Thread(target=loop, name=name, daemon=True)
    t.start()
    return t


def main():
    lg = logging.getLogger('iha')
    lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__)
    sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
    lg.addHandler(sh)
    opts = load_options()
    d = ihdb.open(os.path.join(DATA, ihdb.DB_FILE_NAME), E.MIGRATIONS, snapshot_dir=os.path.join(DATA, 'backup'))
    a = E.Assets(d)
    # One-time import from files the owner copies into /data/import/ (refuses if the database already has data).
    inv = os.path.join(DATA, 'import', 'inventory.json')
    if os.path.exists(inv):
        try:
            lg.info('import: %s', a.import_legacy(inv, os.path.join(DATA, 'import', 'history.csv')))
        except (E.BadInput, ValueError, KeyError) as e:
            lg.info('import skipped (%s)', e)
    live = opts['mode'] == 'live'
    ha = None
    if live:
        from ihlib.ha import HA
        ha = HA()
    lg.info('ih_assets %s starting: API %s, %s', E.VERSION, 'on' if opts['api_token'] else 'OFF (no api_token)',
            'LIVE: publishes sensor.assets_low/high' if live else 'DRY: publishes nothing')
    app = {'a': a, 'opts': opts}
    serve(QuietServer(('0.0.0.0', PORT), make_handler(app, False)), 'api', lg.info)
    serve(QuietServer(('0.0.0.0', UI_PORT), make_handler(app, True)), 'ui', lg.info)
    last_snap = 0
    while True:
        try:
            t = a.record()
            if live:
                a.publish(ha)
            app['last_tick'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            lg.info('tick ok: low %s high %s items %s unpriced %s', t['low'], t['high'], t['count'], t['unpriced'])
            if time.time() - last_snap > 86400:
                ihdb.snapshot(os.path.join(DATA, ihdb.DB_FILE_NAME), os.path.join(DATA, 'backup'))
                last_snap = time.time()
        except Exception as e:
            lg.info('tick failed (%s)', type(e).__name__)
        time.sleep(opts['interval_min'] * 60)


if __name__ == '__main__':
    main()
