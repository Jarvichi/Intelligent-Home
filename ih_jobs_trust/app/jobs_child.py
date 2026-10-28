"""jobs_child: the runtime every child job add-on ("Intelligent Home - Jobs - <Job>") shares. stdlib only. SHARED SOURCE: sync_from_service.sh copies it
into each child add-on's app/ folder. Never edit the copies.

A child is an independent process that runs ONE job (code from the parent's jobs/ package, also copied in). It
  - serves /healthz, /status (no secrets) and a signed POST /run (the parent triggers the job with it),
  - registers with the parent every REGISTER_EVERY s (POST <parent_url>/register, signed with register_key, see jobs_link.py),
  - runs the job once START_DELAY_S after start (REST sensors vanish at an HA restart),
  - optionally runs it every own_timer_min minutes (option, 0 = off) so it still works when the parent is stopped.
DRY by default: live only when mode=live AND old_loops_disabled=true AND /data/STOP_LIVE does not exist. If the parent is down the child just keeps working."""
import json, os, sys, threading, time, traceback, logging, collections, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jobs_link
from jobs import common
from ihlib import log as ihlog

DEFAULT_PARENT = 'http://172.30.32.1:8792'    # the parent add-on on the Supervisor gateway
STOP_FILE = 'STOP_LIVE'
START_DELAY_S = 90
REGISTER_EVERY = 30
MAX_RUN_BODY = 4096


def _snip(s, n=200):
    """First n chars as ONE line: backslash, quote and newlines escaped. Only used when option log_payloads is true."""
    s = str(s or '')[:n]
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n')


def _tag(v, default='-'):
    """A short safe token for a log line (letters, digits and . _ : - only)."""
    t = ''.join(c if (c.isalnum() and c.isascii()) or c in '._:-' else '_' for c in str(v if v is not None else default))[:64]
    return t or default


class Ring(logging.Handler):
    """Last log lines in memory; counts '[DRY] would set' lines."""
    def __init__(self):
        super().__init__()
        self.lines = collections.deque(maxlen=60)
        self.would = 0
        self.would_lines = collections.deque(maxlen=20)

    def emit(self, rec):
        t = rec.getMessage()
        self.lines.append(time.strftime('%H:%M:%S ') + t[:300])
        if t.startswith('[DRY]'):
            self.would += 1
            self.would_lines.append(time.strftime('%H:%M:%S ') + t[:300])


def load_options(path, extra_str=(), extra_bool=()):
    """Common options plus the job's own string options (extra_str) and bool options (extra_bool, default True for host_info style flags)."""
    try:
        o = json.load(open(path))
    except (OSError, ValueError):
        o = {}
    out = {'mode': 'live' if str(o.get('mode') or 'dry').strip() == 'live' else 'dry',
           'old_loops_disabled': bool(o.get('old_loops_disabled', False)),
           'parent_url': str(o.get('parent_url') or DEFAULT_PARENT).strip().rstrip('/'),
           'callback_url': str(o.get('callback_url') or '').strip().rstrip('/'),
           'register_key': str(o.get('register_key') or '').strip(),
           'own_timer_min': max(0, int(o.get('own_timer_min') or 0)),
           'log_payloads': o.get('log_payloads') is True}
    for k, d in extra_str:
        out[k] = str(o.get(k) or d).strip()
    for k, d in extra_bool:
        out[k] = bool(o.get(k, d))
    return out


class Child:
    def __init__(self, spec, opts, data='/data', log=None, ring=None, token=None, opener=None):
        """spec: dict(job, source, slug, title, version, port, runner)."""
        self.spec, self.opts, self.data = spec, opts, data
        self.ring = ring or Ring()
        raw = log or (lambda m: logging.getLogger(spec['source']).info(m))
        self.log = lambda m: raw(ihlog.scrub(m, ihlog.secret_values(self.opts)))   # secret option values never reach a log line
        self._token, self._opener = token, opener
        self.started = time.time()
        self.lock = threading.Lock()
        self.runs = self.errors = 0
        self.last = self.last_ok = self.last_mode = self.last_trigger = self.err_last = None
        self.registered_at = None
        self.register_error = ''
        self.rejected = 0

    # ---- live gate ----
    def live_mode(self):
        o = self.opts
        if o.get('mode') != 'live':
            return False, 'mode option is dry'
        if not o.get('old_loops_disabled'):
            return False, 'old_loops_disabled is false'
        if os.path.exists(os.path.join(self.data, STOP_FILE)):
            return False, 'kill switch file %s exists' % STOP_FILE
        return True, 'live'

    def mode(self):
        return 'live' if self.live_mode()[0] else 'dry'

    # ---- run ----
    def run_job(self, trigger='manual'):
        """One job run = ONE invocation: logs ONE line (run id, trigger, job, chars of job log out, ms, ok, why). Job log text only when log_payloads is on."""
        with self.lock:
            live, _ = self.live_mode()
            t0, seen = time.time(), []
            self.seq = getattr(self, 'seq', 0) + 1
            rid = self.seq

            def jlog(m):
                seen.append(str(m)); self.log(m)
            ctx = common.Ctx(dry=not live, log=jlog, token=self._token, opener=self._opener, data_dir=self.data, opts=self.opts)
            self.last_mode, self.last_trigger = ('live' if live else 'dry'), trigger
            ok, why = False, '-'
            try:
                ok = self.spec['runner'](ctx) is not False
                self.last, self.last_ok = time.time(), ok
                self.runs += 1
                if ok:
                    self.err_last = None
                else:
                    why = 'runner_returned_false'
                return ok
            except Exception as e:
                self.last, self.last_ok = time.time(), False
                self.errors += 1
                why = type(e).__name__
                self.err_last = traceback.format_exc(limit=3).strip().splitlines()[-1][:200]
                self.log('%s error: %s' % (self.spec['job'], self.err_last))
                return False
            finally:
                try:
                    f = ['invoke', 'id=run%d' % rid, 'src=' + _tag(trigger), 'what=' + _tag(self.spec['job']), 'in=0', 'out=%d' % sum(len(x) for x in seen),
                         'ms=%d' % int((time.time() - t0) * 1000), 'ok=%s' % bool(ok), 'why=' + _tag(why), 'mode=' + _tag(self.last_mode)]
                    if self.opts.get('log_payloads'):
                        f += ['req="%s"' % _snip('%s %s' % (trigger, self.spec['job'])), 'ans="%s"' % _snip(seen[-1] if seen else '')]
                    self.log(' '.join(f))
                except Exception:
                    pass

    def start_background(self, delay=START_DELAY_S, sleep=time.sleep):
        def go():
            sleep(delay)
            self.run_job('startup')
            m = self.opts.get('own_timer_min') or 0
            while m:
                sleep(m * 60)
                self.run_job('own_timer')
        t = threading.Thread(target=go, daemon=True, name='startup'); t.start()
        return t

    # ---- status / registration ----
    def healthy(self):
        return True

    def status(self):
        now = time.time()
        return {'name': self.spec['source'], 'job': self.spec['job'], 'version': self.spec['version'], 'mode': self.mode(),
                'why': self.live_mode()[1], 'kill_switch': os.path.exists(os.path.join(self.data, STOP_FILE)),
                'uptime_s': int(now - self.started), 'healthy': self.healthy(), 'runs': self.runs, 'errors': self.errors,
                'last_ok': self.last_ok, 'last_run_s': None if self.last is None else int(now - self.last),
                'last_mode': self.last_mode, 'last_trigger': self.last_trigger, 'last_error': self.err_last,
                'parent': {'link_enabled': bool(self.opts.get('register_key')), 'registered_s_ago': None if self.registered_at is None else int(now - self.registered_at),
                           'last_error': self.register_error},
                'refused_run_requests': self.rejected, 'own_timer_min': self.opts.get('own_timer_min', 0),
                'would_set_total': self.ring.would, 'last_would': list(self.ring.would_lines)[-5:]}

    def register(self, opener=None, now=None):
        """POST this child's identity and status to the parent. Never raises, never prints the key. -> bool."""
        o, s = self.opts, self.spec
        if not o.get('register_key'):
            self.register_error = 'no register_key'
            return False
        cb = o.get('callback_url') or 'http://172.30.32.1:%d' % s['port']
        try:
            body, headers = jobs_link.pack(o['register_key'], {'name': s['source'], 'slug': s['slug'], 'job': s['job'], 'version': s['version'],
                                                               'title': s['title'], 'port': s['port'], 'url': cb, 'mode': self.mode(),
                                                               'healthy': self.healthy(), 'status': self._brief()}, now=now)
            req = urllib.request.Request(o['parent_url'] + '/register', data=body, method='POST', headers=headers)
            with (opener or self._opener or urllib.request.urlopen)(req, timeout=5) as x:
                good = x.status == 200
            self.register_error = '' if good else 'parent refused'
            if good:
                self.registered_at = time.time() if now is None else now
            return good
        except Exception as e:
            self.register_error = type(e).__name__
            return False

    def _brief(self):
        st = self.status()
        return {k: st[k] for k in ('runs', 'errors', 'last_ok', 'last_run_s', 'last_trigger', 'last_error', 'would_set_total')}


def make_handler(child):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct='application/json'):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            p = self.path.split('?')[0]
            if p == '/healthz':
                self._send(200 if child.healthy() else 503, 'ok' if child.healthy() else 'not ok', 'text/plain')
            elif p == '/status':
                self._send(200, child.status())
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path.split('?')[0] != '/run':
                self.send_error(404); return
            n = int(self.headers.get('Content-Length') or 0)
            if n > MAX_RUN_BODY:
                self.send_error(413); return
            obj, why = jobs_link.unpack(child.opts.get('register_key', ''), self.rfile.read(n), self.headers.get(jobs_link.HEADER))
            if obj is None or obj.get('job') != child.spec['job']:
                child.rejected += 1
                self._send(401, {'ok': False, 'why': why if obj is None else 'wrong job'}); return
            ok = child.run_job('parent')
            self._send(200, {'ok': ok, 'job': child.spec['job'], 'mode': child.last_mode})

        def log_message(self, *a):
            pass
    return H


def main(spec, extra_str=(), extra_bool=()):
    """Entry point used by each child's app/main.py."""
    ring = Ring()
    lg = logging.getLogger(spec['source']); lg.setLevel(logging.INFO); lg.addHandler(ring)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    opts = load_options(os.environ.get('IHJ_OPTIONS', '/data/options.json'), extra_str, extra_bool)
    child = Child(spec, opts, data=os.environ.get('IHJ_DATA', '/data'), log=lg.info, ring=ring)
    child.start_background()
    lg.info('%s %s starting: %s; first run in %d s; parent link %s', spec['source'], spec['version'], child.mode(), START_DELAY_S,
            'on' if opts['register_key'] else 'OFF (no register_key)')
    if child.mode() == 'live':
        lg.info('LIVE: really setting HA states. Kill switch: create %s/%s', child.data, STOP_FILE)

    def registrar():
        while True:
            child.register(); time.sleep(REGISTER_EVERY)
    threading.Thread(target=registrar, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', spec['port']), make_handler(child)).serve_forever()
