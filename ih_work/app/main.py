#!/usr/bin/env python3
"""ih_work 0.1.0: the work queue, the pace gate and the task view. See DOCS.md.
API (bearer api_token; an empty token refuses everything except /healthz):
  POST /enqueue {kind, ref, title, instruction, approved?, by?}   GET /list?status=a,b   GET /item/<id>
  POST /claim {consumer, lease_s?}   POST /heartbeat {id, consumer, lease_s?}   POST /complete {id, consumer, summary?}   POST /fail {id, consumer, error?}
  POST /cancel {id}   POST /retry {id}   GET /status   GET /healthz (open)
Unknown fields are refused. Nothing secret is logged or returned. stdlib only."""
import hmac, json, logging, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate as G
from service import Service, VERSION
from store import STATUSES, StoreError

DATA = os.environ.get('IHW_DATA', '/data')
PORT = int(os.environ.get('IHW_PORT', '8802'))
MAX_BODY = 16384
HTTP = {'bad': 400, 'forbidden': 403, 'missing': 404, 'conflict': 409}
FIELDS = {   # POST path -> (allowed fields, required fields)
    '/enqueue': ({'kind', 'ref', 'title', 'instruction', 'approved', 'by'}, {'kind', 'ref', 'title', 'instruction'}),
    '/claim': ({'consumer', 'lease_s'}, {'consumer'}),
    '/heartbeat': ({'id', 'consumer', 'lease_s'}, {'id', 'consumer'}),
    '/complete': ({'id', 'consumer', 'summary'}, {'id', 'consumer'}),
    '/fail': ({'id', 'consumer', 'error'}, {'id', 'consumer'}),
    '/cancel': ({'id'}, {'id'}),
    '/retry': ({'id'}, {'id'}),
}
DEFAULT_PREFIXES = ['IH:', 'Check:', 'Review:']


def _num(o, key, default, lo, hi):
    try:
        return max(lo, min(hi, float(o.get(key, default))))
    except (TypeError, ValueError):
        return default


def load_options(path=None, log=print):
    try:
        with open(path or os.path.join(DATA, 'options.json')) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}
    if not isinstance(o, dict):
        o = {}
    text = lambda k, d='': o[k].strip() if isinstance(o.get(k), str) else d
    bands = G.Bands(_num(o, 'day_start', -5.0, -100, 100), _num(o, 'day_stop', -4.0, -100, 100), _num(o, 'night_start', -1.0, -100, 100),
                    _num(o, 'night_stop', 0.0, -100, 100), text('night_from', '23:00'), text('night_to', '06:00'))
    try:
        tz = ZoneInfo(text('timezone', 'Europe/London') or 'Europe/London')
    except Exception:
        log('timezone not found: using UTC')
        from datetime import timezone
        tz = timezone.utc
    prefixes = [p.strip() for p in o.get('feed_prefixes', DEFAULT_PREFIXES) if isinstance(p, str) and p.strip()] if isinstance(o.get('feed_prefixes'), list) else DEFAULT_PREFIXES
    return dict(mode='live' if o.get('mode') == 'live' else 'dry', api_token=text('api_token'), lease_s=int(_num(o, 'lease_minutes', 20, 1, 120) * 60),
                stop=bool(o.get('stop')), bands=bands, tz=tz, feed_backlog=bool(o.get('feed_backlog')), feed_prefixes=prefixes,
                todo_entity=text('todo_entity', 'todo.claude_to_do') or 'todo.claude_to_do', week_entity=text('week_usage_entity'),
                session_entity=text('session_usage_entity'), reset_entity=text('weekly_reset_entity'))


def make_handler(svc, token_of):
    def authed(h):
        tok = token_of()
        given = h.headers.get('Authorization', '')
        return bool(tok) and given.startswith('Bearer ') and hmac.compare_digest(given[7:].encode(), tok.encode())

    def body_of(h, path):
        try:
            n = int(h.headers.get('Content-Length') or 0)
        except ValueError:
            raise StoreError('bad', 'bad length')
        if n > MAX_BODY:
            raise StoreError('bad', 'request too large')
        try:
            d = json.loads(h.rfile.read(n) or b'{}')
        except ValueError:
            raise StoreError('bad', 'body must be JSON')
        allowed, required = FIELDS[path]
        if not isinstance(d, dict):
            raise StoreError('bad', 'body must be a JSON object')
        extra = sorted(set(d) - allowed)
        if extra:
            raise StoreError('bad', 'unknown field: %s' % ', '.join(str(x)[:30] for x in extra[:3]))
        missing = sorted(required - set(d))
        if missing:
            raise StoreError('bad', 'missing: %s' % ', '.join(missing))
        return d

    def lease_of(d):
        v = d.get('lease_s')
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or not 60 <= v <= 3600:
            raise StoreError('bad', 'lease_s must be a whole number from 60 to 3600')
        return v

    def post(path, d):
        s = svc.store
        if path == '/enqueue':
            return s.enqueue(d['kind'], d['ref'], d['title'], d['instruction'], d.get('approved', False), d.get('by', 'claude'))
        if path == '/claim':
            return svc.claim(d['consumer'], lease_of(d))
        if path == '/heartbeat':
            return s.heartbeat(str(d['id']), d['consumer'], lease_of(d) or svc.opts['lease_s'])
        if path == '/complete':
            return s.finish(str(d['id']), d['consumer'], True, summary=d.get('summary'))
        if path == '/fail':
            return s.finish(str(d['id']), d['consumer'], False, error=d.get('error'))
        if path == '/cancel':
            return s.cancel(str(d['id']))
        if path == '/retry':
            return s.retry(str(d['id']))

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct='application/json'):
            body = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def _run(self, fn):
            try:
                self._send(200, fn())
            except StoreError as e:
                self._send(HTTP[e.code], {'error': str(e)})
            except Exception as e:
                logging.getLogger('ihwork').info('request failed (%s)', type(e).__name__)
                self._send(500, {'error': 'internal error'})

        def do_GET(self):
            u = urlparse(self.path)
            p = u.path
            if p == '/healthz':
                return self._send(200, b'ok', 'text/plain')
            if not authed(self):
                return self._send(401, {'error': 'unauthorized'})
            if p == '/status':
                return self._run(svc.status)
            if p == '/list':
                def f():
                    raw = (parse_qs(u.query).get('status') or [''])[0]
                    want = [x for x in raw.split(',') if x]
                    if any(x not in STATUSES for x in want):
                        raise StoreError('bad', 'status must be from: ' + ', '.join(STATUSES))
                    return {'items': svc.store.list(want or None), 'corrupt_files': list(svc.store.corrupt)}
                return self._run(f)
            if p.startswith('/item/'):
                def g():
                    it = svc.store.get(p[6:][:40])
                    if not it:
                        raise StoreError('missing', 'no such item')
                    return it
                return self._run(g)
            self._send(404, {'error': 'not found'})

        def do_POST(self):
            p = urlparse(self.path).path
            if p not in FIELDS:
                return self._send(404, {'error': 'not found'})
            if not authed(self):
                return self._send(401, {'error': 'unauthorized'})
            self._run(lambda: post(p, body_of(self, p)))

        def log_message(self, *a):
            pass
    return H


def main():
    lg = logging.getLogger('ihwork')
    lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__)
    sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
    lg.addHandler(sh)
    opts = load_options(log=lg.info)
    from ihlib.ha import HA
    from pace import PaceReader
    ha = HA()
    svc = Service(DATA, opts, PaceReader(ha, opts['week_entity'], opts['session_entity'], opts['reset_entity']), ha, log=lg.info)
    lg.info('ih_work %s starting: %s, API %s, feed_backlog %s, bands day %g/%g night %g/%g (%s-%s)', VERSION,
            'LIVE' if opts['mode'] == 'live' else 'TEST MODE (nothing is handed out)', 'on' if opts['api_token'] else 'OFF (no api_token)',
            'on' if opts['feed_backlog'] else 'off', opts['bands'].day_start, opts['bands'].day_stop, opts['bands'].night_start,
            opts['bands'].night_stop, opts['bands'].night_from, opts['bands'].night_to)

    def ticker():
        while True:
            try:
                svc.tick()
            except Exception as e:
                lg.info('tick failed (%s)', type(e).__name__)
            time.sleep(30)
    threading.Thread(target=ticker, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(svc, lambda: opts['api_token'])).serve_forever()


if __name__ == '__main__':
    main()
