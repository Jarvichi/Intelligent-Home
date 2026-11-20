#!/usr/bin/env python3
"""ih_themed_events: themed events (Halloween, NYE, ...) kept as data, with a checklist, a review and a generator that writes
the standard Home Assistant helpers, scripts and automations as text for review. See DOCS.md. It never edits Home Assistant config.

API on IHE_PORT (bearer api_token; an empty token refuses everything except /healthz):
  GET /healthz (open)   GET /status   GET /events   GET /events/<slug>   GET /events/<slug>/generate   GET /events/<slug>/reviews
  POST /events {record}          POST /events/<slug> {record}          POST /events/<slug>/delete
  POST /events/<slug>/items {text, days_before?}    POST /events/<slug>/items/<id> {done}    POST /events/<slug>/items/<id>/delete
  POST /events/<slug>/review {occurrence, worked, change}
  POST /events/<slug>/mode {on}   (ingress page only, and only when the option control is "live": turns input_boolean.<slug>_mode on or off)
The ingress page (IHE_UI_PORT, Home Assistant sign-in, admin only) uses the same routes under /api/ and always acts as the owner.
Unknown fields are refused. Event text is never logged. stdlib only."""
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import generator as G
import model as M
import seed as SEED
from store import Store

VERSION = '0.1.0'
DATA = os.environ.get('IHE_DATA', '/data')
PORT = int(os.environ.get('IHE_PORT', '8819'))
UI_PORT = int(os.environ.get('IHE_UI_PORT', '8825'))
INGRESS_PEER = '172.30.32.2'
MAX_BODY = 65536
HTTP = {'bad': 400, 'missing': 404, 'conflict': 409, 'forbidden': 403}
UI_ROOT = Path(HERE) / 'ui'
ASSETS = {'/': 'index.html', '/index.html': 'index.html', '/ui.css': 'ui.css', '/ui.js': 'ui.js', '/ih-ui.css': 'ih-ui.css', '/ih-ui.js': 'ih-ui.js',
          '/ih-patterns.css': 'ih-patterns.css'}
EVENT = r'/events/([a-z][a-z0-9_]{1,19})'
ROUTES = [('GET', r'/events', 'list'), ('POST', r'/events', 'create'), ('GET', EVENT, 'get'), ('POST', EVENT, 'update'), ('POST', EVENT + '/delete', 'delete'),
          ('GET', EVENT + '/generate', 'generate'), ('GET', EVENT + '/reviews', 'reviews'), ('POST', EVENT + '/items', 'add_item'),
          ('POST', EVENT + r'/items/(\d{1,9})', 'set_item'), ('POST', EVENT + r'/items/(\d{1,9})/delete', 'delete_item'),
          ('POST', EVENT + '/review', 'review'), ('POST', EVENT + '/mode', 'mode')]
ROUTES = [(m, re.compile('^' + p + '$'), name) for m, p, name in ROUTES]


def load_options(path=None):
    try:
        with open(path or os.path.join(DATA, 'options.json')) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}
    o = o if isinstance(o, dict) else {}
    return {'api_token': o['api_token'].strip() if isinstance(o.get('api_token'), str) else '', 'control': 'live' if o.get('control') == 'live' else 'off'}


def _only(body, allowed, required=()):
    if not isinstance(body, dict):
        raise M.RuleError('bad', 'send a JSON object')
    extra = set(body) - set(allowed)
    if extra:
        raise M.RuleError('bad', 'unknown field: ' + ', '.join(sorted(extra)))
    missing = [k for k in required if k not in body]
    if missing:
        raise M.RuleError('bad', 'missing: ' + ', '.join(missing))
    return body


def mode_state(ha, slug):
    """Live state of input_boolean.<slug>_mode: 'on', 'off', or None (no such helper yet, or Home Assistant not reachable)."""
    if ha is None:
        return None
    s = ha.state('input_boolean.%s_mode' % slug)
    return s if s in ('on', 'off') else None


def dispatch(store, ha, opts, method, path, body, actor, ui):
    """Run one request. Returns (status, JSON-able). Raises M.RuleError for refused requests."""
    for m, rx, name in ROUTES:
        hit = rx.match(path) if m == method else None
        if not hit:
            continue
        a = hit.groups()
        if name == 'list':
            events = store.list()
            for e in events:
                e['mode_state'] = mode_state(ha, e['slug'])
            return 200, {'events': events}
        if name == 'create':
            b = dict(_only(body, M.EVENT_FIELDS, ('name',)))
            b.setdefault('slug', _slug_of(b))
            return 201, store.create(b, actor)
        if name == 'get':
            ev = store.get(a[0])
            ev['mode_state'] = mode_state(ha, a[0])
            return 200, ev
        if name == 'update':
            return 200, store.update(a[0], body, actor)
        if name == 'delete':
            return 200, store.delete(a[0], actor)
        if name == 'generate':
            ev = store.record(a[0])
            return 200, {'slug': a[0], 'files': G.generate(ev), 'warnings': G.warnings(ev), 'ids': G.ids(ev)}
        if name == 'reviews':
            return 200, {'reviews': store.reviews(a[0])}
        if name == 'add_item':
            return 201, store.add_item(a[0], _only(body, ('text', 'days_before'), ('text',)), actor)
        if name == 'set_item':
            return 200, store.set_item(a[0], int(a[1]), _only(body, ('done',), ('done',))['done'], actor)
        if name == 'delete_item':
            return 200, store.delete_item(a[0], int(a[1]), actor)
        if name == 'review':
            b = _only(body, ('occurrence', 'worked', 'change'), ('occurrence',))
            return 200, store.save_review(a[0], b['occurrence'], b.get('worked', ''), b.get('change', ''), actor)
        if name == 'mode':
            if not ui:
                raise M.RuleError('forbidden', 'the mode is switched on the page only')
            if opts['control'] != 'live':
                raise M.RuleError('forbidden', 'switching is off: set the option control to "live"')
            on = _only(body, ('on',), ('on',))['on']
            if not isinstance(on, bool):
                raise M.RuleError('bad', 'on must be true or false')
            store.record(a[0])
            if mode_state(ha, a[0]) is None:
                raise M.RuleError('conflict', 'input_boolean.%s_mode does not exist yet' % a[0])
            ha.call_service('input_boolean', 'turn_on' if on else 'turn_off', {'entity_id': 'input_boolean.%s_mode' % a[0]})
            store._log(store.db.con, actor, a[0], 'mode_on' if on else 'mode_off')
            return 200, {'slug': a[0], 'mode_state': 'on' if on else 'off'}
    raise M.RuleError('missing', 'no such route')


def _slug_of(body):
    name = (body or {}).get('name', '') if isinstance(body, dict) else ''
    return re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_')[:20]


def make_handler(store, ha, opts_of, ui):
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
                if self.client_address[0] != INGRESS_PEER:
                    self.send_error(403)
                    return False
                return True
            tok = opts_of()['api_token']
            given = self.headers.get('Authorization', '')
            if tok and given.startswith('Bearer ') and hmac.compare_digest(given[7:].encode(), tok.encode()):
                return True
            self._send(401, {'error': 'token required'})
            return False

        def _body(self):
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                raise M.RuleError('bad', 'bad length')
            if n > MAX_BODY:
                raise M.RuleError('bad', 'body too large')
            if n == 0:
                return {}
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                raise M.RuleError('bad', 'send JSON')
            try:
                return json.loads(self.rfile.read(n))
            except ValueError:
                raise M.RuleError('bad', 'body is not JSON')

        def _run(self, method, path, body_fn):
            try:
                code, out = dispatch(store, ha, opts_of(), method, path, body_fn(), 'owner' if ui else 'worker', ui)
                self._send(code, out)
            except M.RuleError as e:
                self._send(HTTP.get(e.code, 400), {'error': str(e)})
            except Exception as e:
                logging.getLogger('ihevents').info('request failed (%s)', type(e).__name__)
                self._send(500, {'error': 'internal error'})

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == '/healthz':
                return self._send(200, b'ok', 'text/plain')
            if ui and u.path in ASSETS:
                try:
                    body = (UI_ROOT / ASSETS[u.path]).read_bytes()
                except OSError:
                    return self.send_error(404)
                name = ASSETS[u.path]
                return self._send(200, body, 'text/html; charset=utf-8' if name.endswith('.html') else 'text/css; charset=utf-8' if name.endswith('.css') else 'text/javascript; charset=utf-8')
            if not self._allowed():
                return
            path = u.path[4:] if ui and u.path.startswith('/api/') else u.path
            if path == '/status':
                return self._send(200, {'version': VERSION, 'counts': store.counts(), 'control': opts_of()['control'], 'api_token_set': bool(opts_of()['api_token']),
                                        'schema': store.db.schema_version()})
            if ui and not u.path.startswith('/api/'):
                return self.send_error(404)
            self._run('GET', path, lambda: None)

        def do_POST(self):
            if not self._allowed():
                return
            u = urlparse(self.path)
            if ui and not u.path.startswith('/api/'):
                return self.send_error(404)
            self._run('POST', u.path[4:] if ui else u.path, self._body)
    return H


def serve(make_server, make_store, name, log):
    """One single-threaded server on its own long-lived thread with its own store handle (= its own database connection)."""
    def loop():
        try:
            make_server(make_store()).serve_forever()
        except Exception as e:
            log('%s server stopped (%s)' % (name, type(e).__name__))
            os._exit(1)     # the Supervisor watchdog restarts the add-on
    t = threading.Thread(target=loop, name=name, daemon=True)
    t.start()
    return t


def main():
    lg = logging.getLogger('ihevents')
    lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__)
    sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
    lg.addHandler(sh)
    opts = load_options()
    first = Store(DATA)
    seeded = SEED.load(first)
    try:
        from ihlib.ha import HA
        ha = HA()
    except Exception:
        ha = None
    lg.info('ih_themed_events %s starting: API %s, switching %s, events %d%s', VERSION, 'on' if opts['api_token'] else 'OFF (no api_token)', opts['control'],
            first.counts()['events'], ' (Halloween and NYE records added)' if seeded else '')
    serve(lambda s: HTTPServer(('0.0.0.0', PORT), make_handler(s, ha, lambda: opts, False)), lambda: Store(DATA), 'api', lg.info)
    serve(lambda s: HTTPServer(('0.0.0.0', UI_PORT), make_handler(s, ha, lambda: opts, True)), lambda: Store(DATA), 'ui', lg.info)
    from ihlib import db as ihdb
    day = None
    while True:
        try:
            today = time.strftime('%Y-%m-%d')
            if today != day:
                day = today
                ihdb.snapshot(first.path, os.path.join(DATA, 'backup'))
        except Exception as e:
            lg.info('snapshot failed (%s)', type(e).__name__)
        time.sleep(3600)


if __name__ == '__main__':
    main()
