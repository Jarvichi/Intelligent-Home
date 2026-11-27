#!/usr/bin/env python3
"""ih_usage_trace: a record of Claude usage (session % and week %) with labelled events (who or what used it). See DOCS.md.

API on IHU_PORT (bearer api_token; an empty token refuses everything except /healthz):
  GET  /healthz (open)   GET /status
  POST /events {source, label, start?, end?, ref?}   start defaults to now. Same source+ref with an end closes the open one. No message text is ever accepted or stored.
  GET  /events?since=&until=&source=   GET /countdown (hours until the week pace is under target, with an absolute target time)   GET /series?since=&until=   (times: ISO 8601 or epoch seconds; series also returns the unattributed jumps)
mode shadow: the series is recorded, POST /events logs 'would store' and stores nothing. mode live: events are stored.
The ingress page (IHU_UI_PORT, Home Assistant sign-in, admin only) serves the same GET routes under /api/. Threaded servers. stdlib only."""
import hmac
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store as S

VERSION = '0.2.1'
DATA = os.environ.get('IHU_DATA', '/data')
PORT = int(os.environ.get('IHU_PORT', '8848'))
UI_PORT = int(os.environ.get('IHU_UI_PORT', '8849'))
INGRESS_PEER = '172.30.32.2'
MAX_BODY = 8192
SAMPLE_SECONDS = 300
DEFAULTS = {'session_entity': 'sensor.claude_usage_session_usage', 'week_entity': 'sensor.claude_usage_week_usage',
            'pace_entity': 'sensor.claude_usage_week_usage_pace'}
UI_ROOT = Path(HERE) / 'ui'
ASSETS = {'/': 'index.html', '/index.html': 'index.html', '/ui.css': 'ui.css', '/ui.js': 'ui.js', '/ih-ui.css': 'ih-ui.css', '/ih-ui.js': 'ih-ui.js',
          '/ih-patterns.css': 'ih-patterns.css', '/ui.json': 'ui.json'}
EVENT_FIELDS = {'source', 'label', 'start', 'end', 'ref'}
log = logging.getLogger('ihusage')
# ihlib keeps one connection per thread and never closes the ones of ended threads, so ALL database work runs on this one long-lived thread.
DBTHREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix='db')


def on_db(fn, *a):
    return DBTHREAD.submit(fn, *a).result(timeout=30)


class HttpError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def load_options(path=None):
    try:
        with open(path or os.path.join(DATA, 'options.json')) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}
    o = o if isinstance(o, dict) else {}
    text = lambda k: o[k].strip() if isinstance(o.get(k), str) and o[k].strip() else DEFAULTS.get(k, '')

    def num(k, d, lo, hi, cast=float):
        try:
            return max(lo, min(hi, cast(o.get(k, d))))
        except (TypeError, ValueError):
            return d
    return {'api_token': text('api_token'), 'mode': 'live' if o.get('mode') == 'live' else 'shadow',
            'session_entity': text('session_entity'), 'week_entity': text('week_entity'), 'pace_entity': text('pace_entity'),
            'retention_days': num('retention_days', 60, 1, 365, int), 'jump_threshold': num('jump_threshold', 2.0, 0.1, 100.0)}


def series_reply(store, opts, q):
    one = lambda k: (q.get(k) or [''])[0]
    now = time.time()
    since = S.parse_time(one('since'), 0)
    until = S.parse_time(one('until'), now + 1) if one('until') else None
    samples = store.series(since, until)
    # events overlapping the window, plus a margin so a step at the window edge is judged against its events
    events = store.events(max(0, since - S.MAX_STEP_S), until)
    return {'series': samples, 'jumps': S.find_jumps(samples, events, opts['jump_threshold'], now), 'jump_threshold': opts['jump_threshold']}


def dispatch(store, opts, method, path, query, body):
    q = parse_qs(query)
    one = lambda k: (q.get(k) or [''])[0]
    try:
        if method == 'GET':
            if path == '/status':
                return {'version': VERSION, 'mode': opts['mode'], 'counts': store.counts(), 'jump_threshold': opts['jump_threshold'],
                        'retention_days': opts['retention_days'], 'schema': store.db.schema_version(), 'api_token_set': bool(opts['api_token'])}
            if path == '/countdown':
                return S.countdown(store.latest(), time.time())
            if path == '/series':
                return series_reply(store, opts, q)
            if path == '/events':
                return {'events': store.events(S.parse_time(one('since'), 0), S.parse_time(one('until')) if one('until') else None, one('source').lower() or None),
                        'mode': opts['mode']}
            raise HttpError(404, 'not found')
        if method == 'POST' and path == '/events':
            if not isinstance(body, dict):
                raise HttpError(400, 'body must be a JSON object')
            extra = sorted(set(body) - EVENT_FIELDS)
            if extra:
                raise HttpError(400, 'unknown field: %s' % ', '.join(str(x)[:30] for x in extra[:3]))
            if opts['mode'] != 'live':
                S.clean_text(body.get('source'), 'source', True), S.clean_text(body.get('label'), 'label', True)   # validate like live
                log.info('shadow: would store event source=%s label=%s', S.clean_text(body.get('source'), 'source'), S.clean_text(body.get('label'), 'label'))
                return {'stored': False, 'shadow': True}
            return {'stored': True, 'event': store.add_event(body.get('source'), body.get('label'), body.get('start'), body.get('end'), body.get('ref'))}
        raise HttpError(404, 'not found')
    except S.BadInput as e:
        raise HttpError(400, str(e))


class Base(BaseHTTPRequestHandler):
    timeout = 15

    def _send(self, code, body, ct='application/json; charset=utf-8'):
        body = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise HttpError(400, 'bad length')
        if n > MAX_BODY:
            raise HttpError(400, 'request too large')
        try:
            return json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            raise HttpError(400, 'body must be JSON')

    def _run(self, fn):
        try:
            self._send(200, fn())
        except HttpError as e:
            self._send(e.code, {'error': e.msg})
        except Exception as e:
            log.info('request failed (%s)', type(e).__name__)
            self._send(500, {'error': 'internal error'})

    def log_message(self, *a):
        pass


def make_api_handler(store, opts):
    def authorised(h):
        given = h.headers.get('Authorization', '')
        tok = opts['api_token']
        return bool(tok) and given.startswith('Bearer ') and hmac.compare_digest(given[7:].encode(), tok.encode())

    class H(Base):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == '/healthz':
                return self._send(200, b'ok', 'text/plain')
            if not authorised(self):
                return self._send(401, {'error': 'unauthorized'})
            self._run(lambda: on_db(dispatch, store, opts, 'GET', u.path, u.query, None))

        def do_POST(self):
            u = urlparse(self.path)
            if not authorised(self):
                return self._send(401, {'error': 'unauthorized'})
            body = None
            try:
                body = self._body()
            except HttpError as e:
                return self._send(e.code, {'error': e.msg})
            self._run(lambda: on_db(dispatch, store, opts, 'POST', u.path, u.query, body))
    return H


def make_ui_handler(store, opts, peer=INGRESS_PEER):
    info = json.loads((UI_ROOT / 'ui.json').read_text())

    class H(Base):
        def do_GET(self):
            if self.client_address[0] != peer:
                return self.send_error(403)
            u = urlparse(self.path)
            if u.path.startswith('/api/'):
                return self._run(lambda: on_db(dispatch, store, opts, 'GET', u.path[4:], u.query, None))
            if u.path == '/ui.json':
                return self._send(200, {k: info.get(k) for k in ('title', 'purpose', 'description', 'navigation', 'access')})
            name = ASSETS.get(u.path)
            if not name:
                return self.send_error(404)
            try:
                body = (UI_ROOT / name).read_bytes()
            except OSError:
                return self.send_error(404)
            self._send(200, body, 'text/html; charset=utf-8' if name.endswith('.html') else 'text/css; charset=utf-8' if name.endswith('.css') else 'text/javascript; charset=utf-8')
    return H


def serve(make_server, name):
    def loop():
        try:
            make_server().serve_forever()
        except Exception as e:
            log.info('%s server stopped (%s)', name, type(e).__name__)
            os._exit(1)     # the Supervisor watchdog restarts the add-on
    t = threading.Thread(target=loop, name=name, daemon=True)
    t.start()
    return t


def sample_once(store, ha, opts):
    """Read the three sensors once and store them. -> True if stored. Never raises."""
    try:
        vals = [ha.state(opts[k]) for k in ('session_entity', 'week_entity', 'pace_entity')]
        return on_db(store.add_sample, *vals)
    except Exception as e:
        log.info('sample failed (%s)', type(e).__name__)
        return False


def main():
    log.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__)
    sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
    log.addHandler(sh)
    opts = load_options()
    from ihlib import db as ihdb
    from ihlib.ha import HA
    main_store = on_db(S.UsageStore, DATA)
    log.info('ih_usage_trace %s starting: mode %s, API %s, retention %s days, jump threshold %s',
             VERSION, opts['mode'], 'on' if opts['api_token'] else 'OFF (no api_token)', opts['retention_days'], opts['jump_threshold'])
    # ThreadingHTTPServer: the ingress gateway keeps idle connections open, so a single-threaded server would freeze the page.
    serve(lambda: ThreadingHTTPServer(('0.0.0.0', PORT), make_api_handler(main_store, opts)), 'api')
    serve(lambda: ThreadingHTTPServer(('0.0.0.0', UI_PORT), make_ui_handler(main_store, opts)), 'ui')
    ha, day = HA(), None
    while True:
        sample_once(main_store, ha, opts)
        try:
            today = time.strftime('%Y-%m-%d')
            if today != day:
                day = today
                on_db(main_store.purge, opts['retention_days'])
                on_db(ihdb.snapshot, main_store.path, os.path.join(DATA, 'backup'))
        except Exception as e:
            log.info('upkeep failed (%s)', type(e).__name__)
        time.sleep(SAMPLE_SECONDS)


if __name__ == '__main__':
    main()
