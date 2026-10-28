"""Intelligent Home - Cooking (ih_cooking): recipes, meal plan, meal-vs-calendar clashes, the recipes page (ingress) and the cook engine.
Two listeners: the bearer API on 8809 and the ingress page on 8810 (only the Supervisor may reach it)."""
import json, logging, os, re, sys, threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cooking, recipeadmin

PORT = int(os.environ.get('IHC_PORT', '8809'))
INGRESS_PORT = int(os.environ.get('IHC_INGRESS_PORT', '8810'))
SUPERVISOR_IPS = ('172.30.32.2',)
EAT_RE = re.compile(r'([01]\d|2[0-3]):[0-5]\d')


def ingress_api(st, method, p, body):
    """The recipes page API (same paths and shapes as the old host page service, minus the /api prefix handled by the caller). -> (code, obj)."""
    m = re.fullmatch(r'/recipes/([a-z0-9-]+)', p)
    if method == 'GET' and p == '/recipes':
        return 200, recipeadmin.listing(st.data)
    if method == 'GET' and m:
        r = st.recipe(m[1])
        if not r: return 404, {'error': 'not found'}
        r.pop('slug', None)
        return 200, r
    if method == 'PUT' and m:
        ok, e = recipeadmin.save_recipe(st.data, m[1], body)
        if not ok: return (404 if e == ['no such recipe'] else 422), {'errors': e}
        st.publish()
        return 200, {'saved': m[1]}
    if method == 'POST' and p == '/cook':
        return start_cook(st, body.get('name'), str(body.get('eat', '')))
    if method == 'GET' and p == '/cook':
        return 200, engine(st).status()
    return 404, {'error': 'not found'}


def engine(st):
    return st.engine or st.make_engine()


def start_cook(st, name, eat):
    r = st.recipe(name or '')
    if not r or not EAT_RE.fullmatch(eat or ''): return 422, {'errors': ['need a recipe and eat time HH:MM']}
    r.pop('slug', None)
    res = engine(st).start_cook(r, name, eat)
    if not res.get('ok'): return 409, {'errors': [res.get('why')]}
    return 200, {'dry_run': not res['live'], 'would_run': 'cook %s at %s (%s)' % (name, eat, res['why']), 'result': 'started %s for %s' % (name, eat), 'steps': res['steps']}


def make_handler(st, ingress=False, ingress_ips=SUPERVISOR_IPS):
    page = os.path.join(HERE, 'index.html')

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct='application/json'):
            b = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode()
            self._code, self._out = code, b.decode('utf-8', 'replace')
            self.send_response(code); self.send_header('Content-Type', ct + ('; charset=utf-8' if ct.startswith('text/html') else '')); self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(b)

        def _body(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n > 1000000: raise ValueError('too big')
            return json.loads(self.rfile.read(n) or b'{}')

        def do_GET(self): self._run('GET')
        def do_POST(self): self._run('POST')
        def do_PUT(self): self._run('PUT')

        def _run(self, method):
            t0 = st.clock(); self._code, self._out, self._why = 0, '', '-'
            try:
                (self._ingress if ingress else self._bearer)(method)
            finally:
                if getattr(self, '_logit', False):
                    st.invocation_line(self.headers, self.path, self._code, self._out, t0, self._why)

        # ---- ingress listener: no token, but only from the Supervisor
        def _ingress(self, method):
            self._logit = False
            if self.client_address[0] not in ingress_ips:
                return self._send(403, {'error': 'ingress only'})
            p = re.sub(r'/+', '/', urlparse(self.path).path)
            if method == 'GET' and p in ('/', '/index.html'):
                return self._send(200, open(page, 'rb').read(), 'text/html')
            if not p.startswith('/api/'):
                return self._send(404, {'error': 'not found'})
            self._logit = True
            try:
                body = self._body() if method in ('PUT', 'POST') else {}
            except Exception as ex:
                self._why = 'bad_body'; return self._send(400, {'errors': [str(ex)]})
            try:
                code, obj = ingress_api(st, method, p[4:], body)
            except Exception as ex:
                self._why = type(ex).__name__; return self._send(500, {'error': 'failed'})
            self._why = 'rejected' if code >= 400 else '-'
            self._send(code, obj)

        # ---- bearer listener
        def _bearer(self, method):
            u = urlparse(self.path); p = u.path.rstrip('/') or '/'; q = parse_qs(u.query)
            self._logit = False
            if p == '/healthz' and method == 'GET':
                return self._send(200, 'ok', 'text/plain')
            self._logit = True
            if not st.authed(self.headers.get('Authorization')):
                self._why = 'unauthorized'
                return self._send(401, {'ok': False, 'why': 'bearer token required'})
            if st.stopped():
                self._why = 'stopped'
                return self._send(503, {'ok': False, 'why': 'STOP file present'})
            if not st.rate_ok():
                self._why = 'rate_limit'
                return self._send(429, {'ok': False, 'why': 'rate limit'})
            try:
                if method == 'GET':
                    return self._get(p, q)
                try:
                    body = self._body()
                except Exception as ex:
                    self._why = 'bad_body'; return self._send(400, {'ok': False, 'why': 'bad body'})
                return self._write(method, p, body)
            except ValueError:
                self._why = 'bad_parameter'
                return self._send(400, {'ok': False, 'why': 'bad parameter'})
            except Exception as ex:
                self._why = type(ex).__name__
                return self._send(500, {'ok': False, 'why': type(ex).__name__})

        def _get(self, p, q):
            if p == '/status':
                s = st.status(); s['cook'] = engine(st).status()
                return self._send(200, s)
            if p == '/recipes':
                return self._send(200, {'recipes': st.recipes()})
            if p.startswith('/recipes/'):
                r = st.recipe(p[len('/recipes/'):])
                self._why = 'no_such_recipe'
                return self._send(200, r) if r else self._send(404, {'ok': False, 'why': 'no such recipe'})
            if p == '/plan':
                plan, src = st.plan()
                return self._send(200, {'plan': plan, 'source': src})
            if p == '/clashes':
                days = max(1, min(31, int((q.get('days') or ['7'])[0])))
                today = date.fromisoformat((q.get('today') or [date.today().isoformat()])[0])
                return self._send(200, st.clashes(days, today))
            if p == '/cook':
                return self._send(200, engine(st).status())
            self._why = 'not_found'
            self._send(404, {'ok': False})

        def _write(self, method, p, body):
            e = None
            m = re.fullmatch(r'/recipes/([a-z0-9-]+)', p)
            if method in ('PUT', 'POST') and m:
                ok, errs = recipeadmin.save_recipe(st.data, m[1], body)
                self._why = 'invalid' if not ok else '-'
                if ok: st.publish()
                return self._send(200 if ok else 422, {'ok': ok, 'saved': m[1]} if ok else {'ok': False, 'errors': errs})
            if method == 'POST' and p == '/recipes':
                ok, r = recipeadmin.create_recipe(st.data, body)
                self._why = 'invalid' if not ok else '-'
                if ok: st.publish()
                return self._send(200 if ok else 422, {'ok': True, 'slug': r} if ok else {'ok': False, 'errors': r})
            if method == 'PUT' and p == '/plan':
                ok, errs = recipeadmin.write_plan(st.data, body)
                self._why = 'invalid' if not ok else '-'
                return self._send(200 if ok else 422, {'ok': ok} if ok else {'ok': False, 'errors': errs})
            if method == 'POST' and p == '/shop':
                return self._send(200, recipeadmin.shop(st.data, list(body.get('recipes') or []), list(body.get('groups') or [])))
            if method == 'POST' and p == '/publish':
                return self._send(200, st.publish())
            if method == 'POST' and p == '/import':
                return self._send(200, recipeadmin.do_import(st.data, body))
            en = engine(st)
            if method == 'POST' and p == '/cook':
                code, obj = start_cook(st, body.get('recipe') or body.get('name'), str(body.get('eat', '')))
                self._why = 'rejected' if code >= 400 else '-'
                return self._send(code, obj)
            if method == 'POST' and p == '/cook/stop':
                return self._send(200, en.stop_cook())
            row = int(body.get('row', 0))
            if method == 'POST' and p == '/cook/done': return self._send(200, en.done(row, body.get('did') is True))
            if method == 'POST' and p == '/cook/ack': return self._send(200, en.ack(row))
            if method == 'POST' and p == '/cook/tick': return self._send(200, en.tick_prep(row))
            if method == 'POST' and p == '/cook/delay': return self._send(200, en.delay(int(body.get('minutes', 5)), row))
            if method == 'POST' and p in ('/oven/start', '/oven/set', '/oven/stop'):
                act = p.split('/')[-1]
                spec = {'action': act}
                for k in ('temp', 'program', 'minutes'):
                    if k in body: spec[k] = body[k]
                if act != 'stop' and not isinstance(body.get('temp', 0 if act == 'start' else None), int):
                    self._why = 'bad_parameter'; return self._send(422, {'ok': False, 'errors': ['temp must be a whole number']})
                if isinstance(spec.get('temp'), int) and not 30 <= spec['temp'] <= 300:
                    self._why = 'bad_parameter'; return self._send(422, {'ok': False, 'errors': ['temp 30-300']})
                return self._send(200, {'ok': en.oven_action(spec), 'live': en.guard()[0]})
            self._why = 'not_found'
            self._send(404, {'ok': False})

        def log_message(self, *a):
            pass
    return H


def main():
    lg = logging.getLogger('ih_cooking'); lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    opts = cooking.load_options(os.environ.get('IHA_OPTIONS', '/data/options.json'))
    st = cooking.Store(os.environ.get('IHA_DATA', '/data'), os.path.join(HERE, 'seed_recipes'), opts, log=lg.info)
    en = st.make_engine()
    ok, why = en.guard(force=True)
    lg.info('ih_cooking %s starting: %d recipes; token %s; ha token %s; cook_mode=%s live=%s (%s); discord %s', cooking.VERSION, len(st.recipes()),
            'set' if opts['api_token'] else 'NOT set (all requests refused)', 'set' if opts['ha_token'] else 'not set', opts['cook']['mode'], ok, why,
            'token set' if opts['cook']['discord_token'] else 'token not set')
    ing = ThreadingHTTPServer(('0.0.0.0', INGRESS_PORT), make_handler(st, ingress=True))
    threading.Thread(target=ing.serve_forever, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(st)).serve_forever()


if __name__ == '__main__':
    main()
