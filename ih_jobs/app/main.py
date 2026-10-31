#!/usr/bin/env python3
"""ih_jobs 0.4.0: PARENT "Intelligent Home - Jobs". GENERIC (house details come from the add-on options only). Hourly Home Assistant jobs as an add-on module: backupstore (backup storage sensors) and metoffice (forecast sensors).
DRY by default: the fetch logic runs for real (read-only) but HA states are only logged as '[DRY] would set <entity> = <state>'.
PLUGINS (0.4.0): app/plugins/*.py are discovered by pluginhost.py; option plugins [{name, live, settings (JSON text)}] turns them on; each has its own dry/live, state in /data/<name>/ and a line in /status.
A job is LIVE only when mode=live AND the job is in live_jobs AND old_loops_disabled=true AND /data/STOP_LIVE does not exist.
Runs: when the scheduler sends a signed message (MQTT, interval 3600 s), and once 90 s after start (REST sensors vanish at an HA restart).
stdlib only (+ paho-mqtt, websockets in the image). schedule_client.py and manifest.json are COPIES (see sync_from_service.sh).
Serves /healthz and /status (no secrets). Pushes its manifest and status to the Intelligent Home add-on (POST /ingest).
CHILDREN (0.3.0): each job can also run as its own add-on ("Intelligent Home - Jobs - Backup" / "- Met Office"). A child posts a signed registration to
POST /register (jobs_link.py, shared key option register_key). GET /children lists them; POST /trigger (signed) runs a child's job now. When the scheduler
fires a job and a child for it is registered and fresh, the parent forwards the run to the child instead of running its own copy. Option
run_builtin_jobs (default true = same behaviour as 0.2.x) can be turned off once the children are live."""
import hmac, re, json, os, sys, threading, time, traceback, logging, collections, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import schedule_client
import jobs_link
import pluginhost
from jobs import common, backupstore, metoffice

VERSION = '0.14.1'
DEFAULT_INGEST_URL = 'http://172.30.32.1:8790'   # the Supervisor gateway seen from an add-on; the orchestrator add-on listens there
SOURCE = 'ih_jobs'
JOBS = ('backupstore', 'metoffice')
RUNNERS = {'backupstore': backupstore.run, 'metoffice': metoffice.run}
STOP_FILE = 'STOP_LIVE'   # kill switch: create /data/STOP_LIVE and every job goes dry on its next run
OPTIONS = os.environ.get('IHJ_OPTIONS', '/data/options.json')
DATA = os.environ.get('IHJ_DATA', '/data')
PORT = int(os.environ.get('IHJ_PORT', '8792'))
PUSH_EVERY = 30
START_DELAY_S = 90
WINDOW_S = 600
CHILD_FRESH_S = 120     # a child that registered within this many seconds counts as up
MAX_CHILDREN = 20
FNAME_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]*\.(json|jsonl)$')
EVENT_MAX = 16384     # POST /plugin/<name>/event body limit
IMPORT_MAX = 1048576   # POST /plugin/<name>/import body limit
FORWARD_TIMEOUT_S = 120


def _snip(s, n=200):
    """First n chars as ONE line: backslash, quote and newlines escaped. Only used when option log_payloads is true."""
    s = str(s or '')[:n]
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n')


def _tag(v, default='-'):
    """A short safe token for a log line (letters, digits and . _ : - only)."""
    t = ''.join(c if (c.isalnum() and c.isascii()) or c in '._:-' else '_' for c in str(v if v is not None else default))[:64]
    return t or default


class Ring(logging.Handler):
    """Keeps the last lines in memory and counts what the jobs WOULD have set."""
    def __init__(self):
        super().__init__()
        self.lines = collections.deque(maxlen=100)
        self.would = 0
        self.would_lines = collections.deque(maxlen=30)

    def emit(self, rec):
        t = rec.getMessage()
        self.lines.append(time.strftime('%H:%M:%S ') + t[:300])
        if t.startswith('[DRY]'):
            self.would += 1
            self.would_lines.append(time.strftime('%H:%M:%S ') + t[:300])


def load_options(path=None):
    try:
        o = json.load(open(path or OPTIONS))
    except (OSError, ValueError):
        o = {}
    return {'mode': 'live' if str(o.get('mode') or 'dry').strip() == 'live' else 'dry',
            'live_jobs': [str(x).strip() for x in (o.get('live_jobs') or []) if str(x).strip() in JOBS],
            'old_loops_disabled': bool(o.get('old_loops_disabled', False)),
            'met_office_key': str(o.get('met_office_key') or '').strip(),
            'ingest_url': str(o.get('ingest_url') or DEFAULT_INGEST_URL).rstrip('/'),
            'drive_used_entity': str(o.get('drive_used_entity') or '').strip(), 'drive_total_entity': str(o.get('drive_total_entity') or '').strip(),
            'timezone': str(o.get('timezone') or 'Europe/London').strip(), 'zone_entity': str(o.get('zone_entity') or 'zone.home').strip(),
            'fallback_weather_entity': str(o.get('fallback_weather_entity') or 'weather.forecast_home').strip(),
            'ingest_token': str(o.get('ingest_token') or '').strip(),
            'mqtt_host': str(o.get('mqtt_host') or 'core-mosquitto').strip(), 'mqtt_port': int(o.get('mqtt_port') or 1883),
            'mqtt_user': str(o.get('mqtt_user') or 'ih_jobs').strip(), 'mqtt_password': str(o.get('mqtt_password') or ''),
            'enable_scheduler': bool(o.get('enable_scheduler', False)),
            'signing_key': str(o.get('signing_key') or '').strip(),
            'disk_used_entity': str(o.get('disk_used_entity') or '').strip(), 'disk_total_entity': str(o.get('disk_total_entity') or '').strip(),
            'host_info': bool(o.get('host_info', True)),
            'register_key': str(o.get('register_key') or '').strip(), 'run_builtin_jobs': bool(o.get('run_builtin_jobs', True)),
            'log_payloads': o.get('log_payloads') is True,
            'discord_token': str(o.get('discord_token') or '').strip(), 'files_token': str(o.get('files_token') or '').strip(),
            'bods_api_key': str(o.get('bods_api_key') or '').strip(), 'mail_password': str(o.get('mail_password') or ''), 'anthropic_key': str(o.get('anthropic_key') or '').strip(), 'plugins': pluginhost.parse_plugins_option(o.get('plugins'))}


class Module:
    def __init__(self, opts, runners=None, data=None, log=None, ring=None, token=None, opener=None):
        self.opts, self.started, self.ring = opts, time.time(), ring or Ring()
        self.data = data or DATA
        self.runners = runners or RUNNERS
        self.log = log or (lambda m: logging.getLogger('ihjobs').info(m))
        self._token, self._opener = token, opener
        self.locks = {n: threading.Lock() for n in JOBS}
        self.runs, self.errors, self.last, self.last_ok = collections.Counter(), collections.Counter(), {}, {}
        self.err_last, self.last_mode, self.last_trigger = {}, {}, {}
        self.sched, self.sched_reason = None, ''
        self.thread = None
        self.children = {}          # job -> dict(name, slug, version, url, mode, healthy, status, seen)
        self.children_lock = threading.Lock()
        self.child_rejected = 0
        self.forward_results = {}   # job -> (time, ok)
        self.plugins = None         # pluginhost.Host (set by main())

    def live_mode(self, name):
        """(is_live, reason). Live only with every gate open. Anything else is dry."""
        o = self.opts
        if o.get('mode') != 'live':
            return False, 'mode option is dry'
        if name not in (o.get('live_jobs') or []):
            return False, 'not in live_jobs'
        if not o.get('old_loops_disabled'):
            return False, 'old_loops_disabled is false'
        if os.path.exists(os.path.join(self.data, STOP_FILE)):
            return False, 'kill switch file %s exists' % STOP_FILE
        return True, 'live'

    def mode(self, name):
        return 'live' if self.live_mode(name)[0] else 'dry'

    def run_job(self, name, trigger='manual'):
        """Run one job now (serialised per job). Returns True/False. The mode is decided at the start of each run, so STOP_LIVE acts fast."""
        if name not in self.runners:
            return False
        with self.locks[name]:
            live, why = self.live_mode(name)
            t0, seen = time.time(), []
            self.inv_seq = getattr(self, 'inv_seq', 0) + 1
            rid = self.inv_seq

            def jlog(m):
                seen.append(str(m)); self.log(m)
            ctx = common.Ctx(dry=not live, log=jlog, token=self._token, opener=self._opener, data_dir=self.data, opts=self.opts)
            self.last_mode[name] = 'live' if live else 'dry'; self.last_trigger[name] = trigger
            ok, ewhy = False, '-'
            try:
                ok = self.runners[name](ctx) is not False
                self.last_ok[name] = ok
                self.last[name] = time.time()
                self.runs[name] += 1
                if ok:
                    self.err_last.pop(name, None)
                else:
                    ewhy = 'runner_returned_false'
                return ok
            except Exception as e:
                self.last[name] = time.time(); self.last_ok[name] = False
                self.errors[name] += 1
                ewhy = type(e).__name__
                self.err_last[name] = traceback.format_exc(limit=3).strip().splitlines()[-1][:200]
                self.log('%s error: %s' % (name, self.err_last[name]))
                return False
            finally:
                self._invoke_line('run%d' % rid, trigger, name, sum(len(x) for x in seen), t0, ok, ewhy, seen[-1] if seen else '')

    def _invoke_line(self, rid, trigger, what, out, t0, ok, why, ans=''):
        """ONE line per job run or forward (an invocation): id, trigger, job, chars out, ms, ok, why. Job log text only when log_payloads is on."""
        try:
            f = ['invoke', 'id=' + _tag(rid), 'src=' + _tag(trigger), 'what=' + _tag(what), 'in=0', 'out=%d' % out, 'ms=%d' % int((time.time() - t0) * 1000),
                 'ok=%s' % bool(ok), 'why=' + _tag(why)]
            if self.opts.get('log_payloads'):
                f += ['req="%s"' % _snip('%s %s' % (trigger, what)), 'ans="%s"' % _snip(ans)]
            self.log(' '.join(f))
        except Exception:
            pass

    # ---- children (jobs_link.py) ----
    def register_child(self, body, signature, now=None):
        """Handle a signed POST /register. -> (http code, reason). Stores nothing unless the signature, freshness, name and job are valid."""
        obj, why = jobs_link.unpack(self.opts.get('register_key', ''), body, signature, now=now)
        if obj is None:
            self.child_rejected += 1
            return 401, why
        job, name, url = str(obj.get('job') or ''), str(obj.get('name') or ''), str(obj.get('url') or '').rstrip('/')
        if job not in JOBS or not name.startswith(SOURCE + '_') or len(name) > 40 or not url.startswith('http://') or len(url) > 120:
            self.child_rejected += 1
            return 400, 'bad registration'
        with self.children_lock:
            if job not in self.children and len(self.children) >= MAX_CHILDREN:
                return 400, 'too many children'
            self.children[job] = {'name': name, 'slug': str(obj.get('slug') or '')[:60], 'version': str(obj.get('version') or '')[:20],
                                  'title': str(obj.get('title') or '')[:60], 'url': url, 'mode': 'live' if obj.get('mode') == 'live' else 'dry',
                                  'healthy': bool(obj.get('healthy')), 'status': obj.get('status') if isinstance(obj.get('status'), dict) else {},
                                  'seen': time.time() if now is None else now}
        return 200, 'ok'

    def child_fresh(self, job, now=None):
        c = self.children.get(job)
        return bool(c) and ((time.time() if now is None else now) - c['seen']) <= CHILD_FRESH_S

    def children_view(self, now=None):
        now = time.time() if now is None else now
        with self.children_lock:
            return {j: {'name': c['name'], 'title': c['title'], 'slug': c['slug'], 'version': c['version'], 'mode': c['mode'], 'healthy': c['healthy'],
                        'up': (now - c['seen']) <= CHILD_FRESH_S, 'seen_s_ago': int(now - c['seen']), 'status': c['status'],
                        'last_forward': None if j not in self.forward_results else {'s_ago': int(now - self.forward_results[j][0]), 'ok': self.forward_results[j][1]}}
                    for j, c in self.children.items()}

    def forward(self, job, opener=None):
        """Ask the registered child to run its job now (signed POST <child url>/run). -> True/False. Never raises."""
        c = self.children.get(job)
        key = self.opts.get('register_key', '')
        if not c or not key:
            return False
        ok, t0, why = False, time.time(), '-'
        try:
            body, headers = jobs_link.pack(key, {'job': job})
            req = urllib.request.Request(c['url'] + '/run', data=body, method='POST', headers=headers)
            with (opener or self._opener or urllib.request.urlopen)(req, timeout=FORWARD_TIMEOUT_S) as x:
                ok = x.status == 200 and json.load(x).get('ok') is True
        except Exception as e:
            why = type(e).__name__
            self.log('forward %s failed (%s)' % (job, type(e).__name__))
        self._invoke_line('fwd-' + job, 'scheduler', 'forward:' + job, 0, t0, ok, why if not ok and why != '-' else ('child_said_no' if not ok else '-'))
        self.forward_results[job] = (time.time(), ok)
        return ok

    def dispatch(self, name, trigger='manual'):
        """Run job `name`: through its registered child when one is up, else the built-in copy (when run_builtin_jobs). Start-up runs skip a live child (it runs itself)."""
        if name not in JOBS:
            return False
        if self.child_fresh(name):
            if trigger == 'startup':
                return True
            if self.forward(name):
                return True
            self.log('%s: child run failed, ' % name + ('using the built-in copy' if self.opts.get('run_builtin_jobs', True) else 'built-in jobs are off'))
        if not self.opts.get('run_builtin_jobs', True):
            return False
        return self.run_job(name, trigger)

    def start_run_all(self, delay=START_DELAY_S, sleep=time.sleep):
        """Run every job once after `delay` s (the sensors vanish at an HA restart)."""
        def go():
            sleep(delay)
            for n in JOBS:
                self.dispatch(n, 'startup')
        self.thread = threading.Thread(target=go, daemon=True, name='startup'); self.thread.start()
        return self.thread

    def start_scheduler(self, bus=None):
        """Subscribe to ih/schedule/ih_jobs/<job> and answer on .../done. Off unless enable_scheduler and a key are set."""
        o = self.opts
        if not o.get('enable_scheduler'):
            return False
        if not o.get('signing_key'):
            self.sched_reason = 'no signing key'
            return False
        if bus is None:
            if not o.get('mqtt_password'):
                self.sched_reason = 'no mqtt password'
                return False
            bus = schedule_client.PahoBus(o['mqtt_host'], o['mqtt_port'], o['mqtt_user'], o['mqtt_password'], SOURCE, log=lambda m: self.log('schedule: ' + m))
        self.sched = schedule_client.ScheduleClient(SOURCE, bus, log=self.log, signing_key=o['signing_key'])
        for n in JOBS:
            self.sched.on(n, lambda payload, n=n: self.dispatch(n, 'scheduler'), window_s=WINDOW_S)   # window_s = manifest window
        return self.sched.start() is not False

    def healthy(self):
        return True   # the process is up; job results show in /status

    def status(self):
        now = time.time()
        return {'name': SOURCE, 'version': VERSION,
                'mode': 'live' if any(self.mode(n) == 'live' for n in JOBS) else 'dry-run',
                'mode_option': self.opts['mode'], 'live_jobs_option': list(self.opts.get('live_jobs') or []),
                'old_loops_disabled': bool(self.opts.get('old_loops_disabled')),
                'kill_switch': os.path.exists(os.path.join(self.data, STOP_FILE)), 'uptime_s': int(now - self.started),
                'met_office_key_present': bool(self.opts['met_office_key']), 'ingest_configured': bool(self.opts['ingest_token']),
                'healthy': self.healthy(),
                'jobs': {n: {'mode': self.mode(n), 'why': self.live_mode(n)[1], 'runs': self.runs[n], 'errors': self.errors[n],
                             'last_ok': self.last_ok.get(n), 'last_run_s': None if n not in self.last else int(now - self.last[n]),
                             'last_mode': self.last_mode.get(n), 'last_trigger': self.last_trigger.get(n), 'last_error': self.err_last.get(n)} for n in JOBS},
                'scheduler': {'enabled': bool(self.opts.get('enable_scheduler')), 'active': self.sched is not None,
                              'reason': self.sched_reason if self.sched is None and self.opts.get('enable_scheduler') else '',
                              'rejected': self.sched.rejected if self.sched else 0},
                'run_builtin_jobs': bool(self.opts.get('run_builtin_jobs', True)), 'link_enabled': bool(self.opts.get('register_key')),
                'children': {j: {k: v for k, v in c.items() if k in ('name', 'title', 'version', 'mode', 'up', 'healthy', 'seen_s_ago')} for j, c in self.children_view().items()},
                'children_refused': self.child_rejected,
                'plugins': self.plugins.status() if self.plugins else {},
                'would_set_total': self.ring.would, 'last_would': list(self.ring.would_lines)[-10:]}


def push(mod, manifest, now=None, opener=None, log=print):
    """POST this module's manifest and status to the Intelligent Home add-on. Never raises. The token is never printed."""
    o = mod.opts
    if not o['ingest_token']:
        return False
    now = time.time() if now is None else now
    st = mod.status()
    body = json.dumps({'source': SOURCE, 'manifests': [manifest],
                       'status': [{'name': SOURCE, 'probed_at': now,
                                   'checks': [{'type': 'http', 'ok': st['healthy'], 'detail': '%s, %d would-set, %d children' % (st['mode'], st['would_set_total'], len(st['children']))}]}]}).encode()
    try:
        r = urllib.request.Request(o['ingest_url'] + '/ingest', data=body, method='POST',
                                   headers={'Authorization': 'Bearer ' + o['ingest_token'], 'Content-Type': 'application/json'})
        with (opener or urllib.request.urlopen)(r, timeout=5) as x:
            return x.status == 200
    except Exception as e:
        log('ih push: failed (%s)' % type(e).__name__)
        return False


def _bad_file(fname, text):
    """None if fname/text are acceptable plugin state, else a reason."""
    if not isinstance(fname, str) or not FNAME_RE.match(fname) or '..' in fname:
        return 'bad file name'
    if not isinstance(text, str):
        return 'file content must be text'
    try:
        if fname.endswith('.jsonl'):
            for ln in text.splitlines():
                if ln.strip():
                    json.loads(ln)
        else:
            json.loads(text)
    except ValueError:
        return 'not valid JSON: ' + fname
    return None


def make_handler(mod):
    class H(BaseHTTPRequestHandler):
        def _reply(self, code, body, ct='application/json'):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

        def _plugin_files(self, p):
            """Shared by GET/POST /plugin/<name>/(files|file/<f>|import). Returns (name, rest) after auth + plugin check, or None when already answered."""
            parts = p.split('/')   # ['', 'plugin', name, ...]
            tok = mod.opts.get('files_token') or ''
            if not tok:
                self._reply(403, {'ok': False, 'why': 'files_token not set'}); return None
            if not hmac.compare_digest(('Bearer ' + tok).encode(), (self.headers.get('Authorization') or '').encode()):
                self._reply(401, {'ok': False, 'why': 'unauthorized'}); return None
            names = mod.plugins.mods if mod.plugins else pluginhost.discover()
            if len(parts) < 4 or parts[2] not in names:
                self._reply(404, {'ok': False, 'why': 'unknown plugin'}); return None
            return parts[2], parts[3:]

        def _get_plugin(self, p):
            r = self._plugin_files(p)
            if r is None:
                return
            name, rest = r; d = os.path.join(DATA, name)
            if rest == ['files']:
                fl = {f: os.path.getsize(os.path.join(d, f)) for f in sorted(os.listdir(d)) if FNAME_RE.match(f) and os.path.isfile(os.path.join(d, f))} if os.path.isdir(d) else {}
                self._reply(200, {'files': fl})
            elif len(rest) == 2 and rest[0] == 'file':
                if not FNAME_RE.match(rest[1]) or '..' in rest[1]:
                    self._reply(400, {'ok': False, 'why': 'bad file name'}); return
                try:
                    with open(os.path.join(d, rest[1]), 'rb') as f:
                        self._reply(200, f.read(), 'application/octet-stream')
                except OSError:
                    self._reply(404, {'ok': False, 'why': 'no such file'})
            else:
                self._reply(404, {'ok': False, 'why': 'not found'})

        def _post_import(self, p, n):
            r = self._plugin_files(p)
            if r is None:
                return
            name, rest = r
            if rest == ['run']:   # 0.10.1: run a timer plugin now (e.g. 'check email' from a persona); service plugins have their own thread
                if not mod.plugins or name not in mod.plugins.cfg:
                    self._reply(409, {'ok': False, 'why': 'plugin not configured'}); return
                if mod.plugins.schedule(name).get('kind') == 'service':
                    self._reply(409, {'ok': False, 'why': 'service plugins run on their own'}); return
                if mod.plugins.schedule(name).get('kind') == 'event':
                    self._reply(409, {'ok': False, 'why': 'event plugins are triggered by POST /plugin/<name>/event'}); return
                ok = mod.plugins.run(name, trigger='api')
                self._reply(200, {'ok': ok, 'plugin': name, 'mode': mod.plugins.mode(name)[0]}); return
            if rest == ['event']:   # 0.12.0: hand one JSON event to an event plugin (same files_token auth)
                if n > EVENT_MAX:
                    self._reply(413, {'ok': False, 'why': 'body too large'}); return
                try:
                    obj = json.loads(self.rfile.read(n) or b'{}'); assert isinstance(obj, dict)
                except Exception:
                    self._reply(400, {'ok': False, 'why': 'body must be a JSON object'}); return
                if not mod.plugins or name not in mod.plugins.cfg:
                    self._reply(409, {'ok': False, 'why': 'plugin not configured'}); return
                ok, why = mod.plugins.event(name, obj)
                self._reply(200 if ok else 500, {'ok': ok, 'plugin': name, 'mode': mod.plugins.mode(name)[0], 'why': why}); return
            if rest != ['import']:
                self._reply(404, {'ok': False, 'why': 'not found'}); return
            if n > IMPORT_MAX:
                self._reply(413, {'ok': False, 'why': 'body too large'}); return
            try:
                obj = json.loads(self.rfile.read(n) or b'{}'); files = obj['files']; assert isinstance(files, dict)
            except Exception:
                self._reply(400, {'ok': False, 'why': 'body must be {"files": {name: text}, "overwrite": bool}'}); return
            for fn, tx in files.items():
                why = _bad_file(fn, tx)
                if why:
                    self._reply(400, {'ok': False, 'why': why}); return
            d = os.path.join(DATA, name); os.makedirs(d, exist_ok=True); written, skipped = [], []
            for fn, tx in files.items():
                path = os.path.join(d, fn)
                if os.path.exists(path) and obj.get('overwrite') is not True:
                    skipped.append(fn); continue
                tmp = path + '.tmp'
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w') as f:
                    f.write(tx)
                os.replace(tmp, path); written.append(fn)
            self._reply(200, {'ok': True, 'written': written, 'skipped': skipped})

        def do_GET(self):
            p = self.path.split('?')[0]
            if p == '/healthz':
                code, body, ct = (200 if mod.healthy() else 503), (b'ok' if mod.healthy() else b'not ok'), 'text/plain'
            elif p == '/status':
                code, body, ct = 200, json.dumps(mod.status()).encode(), 'application/json'
            elif p == '/children':
                code, body, ct = 200, json.dumps(mod.children_view()).encode(), 'application/json'
            elif p.startswith('/plugin/'):
                self._get_plugin(p); return
            else:
                self.send_error(404); return
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            p = self.path.split('?')[0]
            n = int(self.headers.get('Content-Length') or 0)
            if p.startswith('/plugin/'):
                self._post_import(p, n); return
            if p not in ('/register', '/trigger') or n > 16384:
                self.send_error(404 if p not in ('/register', '/trigger') else 413); return
            raw = self.rfile.read(n); sig = self.headers.get(jobs_link.HEADER)
            if p == '/register':
                code, why = mod.register_child(raw, sig)
                out = {'ok': code == 200, 'why': why}
            else:
                obj, why = jobs_link.unpack(mod.opts.get('register_key', ''), raw, sig)
                if obj is None or obj.get('job') not in JOBS:
                    code, out = (401 if obj is None else 400), {'ok': False, 'why': why if obj is None else 'unknown job'}
                else:
                    ok = mod.dispatch(obj['job'], 'trigger'); code, out = 200, {'ok': ok, 'job': obj['job']}
            body = json.dumps(out).encode()
            self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass
    return H


def main():
    ring = Ring()
    lg = logging.getLogger('ihjobs'); lg.setLevel(logging.INFO); lg.addHandler(ring)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    mod = Module(load_options(), ring=ring, log=lg.info)
    try:
        mod.start_scheduler()
    except Exception as e:
        lg.info('scheduler not started (%s)', type(e).__name__)
    mod.start_run_all()
    mod.plugins = pluginhost.Host(mod.opts, data=DATA, log=lg.info)
    mod.plugins.start()
    lg.info('ih_jobs %s starting: %s; first run in %d s', VERSION, ', '.join('%s=%s' % (n, mod.mode(n)) for n in JOBS), START_DELAY_S)
    if any(mod.mode(n) == 'live' for n in JOBS):
        lg.info('LIVE: really setting HA states for %s. Kill switch: create %s/%s', [n for n in JOBS if mod.mode(n) == 'live'], DATA, STOP_FILE)
    manifest = json.load(open(os.path.join(HERE, 'manifest.json')))

    def pusher():
        while True:
            push(mod, manifest, log=lg.info); time.sleep(PUSH_EVERY)
    threading.Thread(target=pusher, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(mod)).serve_forever()


if __name__ == '__main__':
    main()
