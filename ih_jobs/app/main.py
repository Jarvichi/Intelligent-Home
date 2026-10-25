#!/usr/bin/env python3
"""ih_jobs 0.2.0: GENERIC (house details come from the add-on options only). Hourly Home Assistant jobs as an add-on module: backupstore (backup storage sensors) and metoffice (forecast sensors).
DRY by default: the fetch logic runs for real (read-only) but HA states are only logged as '[DRY] would set <entity> = <state>'.
A job is LIVE only when mode=live AND the job is in live_jobs AND old_loops_disabled=true AND /data/STOP_LIVE does not exist.
Runs: when the scheduler sends a signed message (MQTT, interval 3600 s), and once 90 s after start (REST sensors vanish at an HA restart).
stdlib only (+ paho-mqtt, websockets in the image). schedule_client.py and manifest.json are COPIES (see sync_from_service.sh).
Serves /healthz and /status (no secrets). Pushes its manifest and status to the Intelligent Home add-on (POST /ingest)."""
import json, os, sys, threading, time, traceback, logging, collections, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import schedule_client
from jobs import common, backupstore, metoffice

VERSION = '0.2.0'
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
            'signing_key': str(o.get('signing_key') or '').strip()}


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
            ctx = common.Ctx(dry=not live, log=self.log, token=self._token, opener=self._opener, data_dir=self.data, opts=self.opts)
            self.last_mode[name] = 'live' if live else 'dry'; self.last_trigger[name] = trigger
            try:
                ok = self.runners[name](ctx) is not False
                self.last_ok[name] = ok
                self.last[name] = time.time()
                self.runs[name] += 1
                if ok:
                    self.err_last.pop(name, None)
                return ok
            except Exception:
                self.last[name] = time.time(); self.last_ok[name] = False
                self.errors[name] += 1
                self.err_last[name] = traceback.format_exc(limit=3).strip().splitlines()[-1][:200]
                self.log('%s error: %s' % (name, self.err_last[name]))
                return False

    def start_run_all(self, delay=START_DELAY_S, sleep=time.sleep):
        """Run every job once after `delay` s (the sensors vanish at an HA restart)."""
        def go():
            sleep(delay)
            for n in JOBS:
                self.run_job(n, 'startup')
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
            self.sched.on(n, lambda payload, n=n: self.run_job(n, 'scheduler'), window_s=WINDOW_S)   # window_s = manifest window
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
                                   'checks': [{'type': 'http', 'ok': st['healthy'], 'detail': '%s, %d would-set' % (st['mode'], st['would_set_total'])}]}]}).encode()
    try:
        r = urllib.request.Request(o['ingest_url'] + '/ingest', data=body, method='POST',
                                   headers={'Authorization': 'Bearer ' + o['ingest_token'], 'Content-Type': 'application/json'})
        with (opener or urllib.request.urlopen)(r, timeout=5) as x:
            return x.status == 200
    except Exception as e:
        log('ih push: failed (%s)' % type(e).__name__)
        return False


def make_handler(mod):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            p = self.path.split('?')[0]
            if p == '/healthz':
                code, body, ct = (200 if mod.healthy() else 503), (b'ok' if mod.healthy() else b'not ok'), 'text/plain'
            elif p == '/status':
                code, body, ct = 200, json.dumps(mod.status()).encode(), 'application/json'
            else:
                self.send_error(404); return
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
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
