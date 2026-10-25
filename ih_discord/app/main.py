#!/usr/bin/env python3
"""ih_discord 0.5.0: Discord shopping + house commands as an add-on module. GENERIC: every deployment detail (channels, entities, people,
aisles, shop names) comes from the add-on options (load_options -> settings.py), none from code. DRY-RUN by default: reads Discord and Home Assistant,
logs what it WOULD post or change. LIVE per feature (shopping, house only) when dry_run=false AND the feature is in live_features AND
old_service_disabled=true AND /data/STOP_LIVE does not exist. The optional plugin braininbox (house-specific, not in the public repo) is always dry.
stdlib only. core.py, settings.py and features/ are COPIES (see sync_from_service.sh).
Serves /healthz and /status (no secrets). Pushes its manifest and status to the Intelligent Home add-on (POST /ingest)."""
import json, os, sys, threading, time, traceback, logging, collections, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core
import settings as ST
import schedule_client
try:   # optional house-specific plugin (private notes server): absent in the generic/public build
    from brain_client import BrainClient
except ImportError:
    BrainClient = None

VERSION = '0.5.0'
SOURCE = 'ih_discord'
LIVE_CAPABLE = ('shopping', 'house')   # plugins are never part of the cut-over
STOP_FILE = 'STOP_LIVE'   # kill switch: create /data/STOP_LIVE and every feature goes dry within one tick
HAVE_BRAIN = BrainClient is not None and os.path.exists(os.path.join(HERE, 'features', 'braininbox.py'))
FEATURES = ['shopping', 'house'] + (['braininbox'] if HAVE_BRAIN else [])   # braininbox writes only through BrainClient (webhook), never a folder
DEFAULT_INGEST_URL = 'http://172.30.32.1:8790'   # the Supervisor gateway seen from an add-on; the orchestrator add-on listens there
OPTIONS = os.environ.get('IHD_OPTIONS', '/data/options.json')
DATA = os.environ.get('IHD_DATA', '/data')
PORT = int(os.environ.get('IHD_PORT', '8791'))
PUSH_EVERY = 30
SELFCHECK_JOB = 'selfcheck'


class Ring(logging.Handler):
    """Keeps the last lines in memory and counts what the bot WOULD have done."""
    def __init__(self):
        super().__init__()
        self.lines = collections.deque(maxlen=100)
        self.would = 0
        self.would_lines = collections.deque(maxlen=30)

    def emit(self, rec):
        t = rec.getMessage()
        self.lines.append(time.strftime('%H:%M:%S ') + t[:300])
        if t.startswith('[DRY]') or 'would' in t:
            self.would += 1
            self.would_lines.append(time.strftime('%H:%M:%S ') + t[:300])


class Out:   # feature modules use print(): send it to the logger
    def __init__(self, lg): self.lg = lg
    def write(self, t):
        if t.strip(): self.lg.info(t.rstrip())
    def flush(self): pass


def _text(v):
    return str(v or '').strip()


def settings_from_options(o):
    """The deployment details (settings.py) from the add-on options. Empty option = neutral default = feature not offered."""
    ch = o.get('channels') or {}
    he = {k: _text(v) for k, v in (o.get('house_entities') or {}).items() if _text(v)}
    dev = {k: _text(v) for k, v in (o.get('status_device') or {}).items() if _text(v)}
    over = {
        'channel_house': _text(ch.get('house')), 'channel_shopping': _text(ch.get('shopping')) or 'shopping',
        'channel_brain_inbox': _text(ch.get('brain_inbox')) or 'brain-inbox',
        'shopping_list_entity': _text(o.get('shopping_list_entity')) or 'todo.shopping_list',
        'meal_plan_script': _text(o.get('meal_plan_script')), 'shop_names': _text(o.get('shop_names')),
        'command_event': _text(o.get('command_event')) or 'ih_discord_command',
        'presence': {_text(p.get('name')).lower(): _text(p.get('entity')) for p in (o.get('presence') or []) if _text(p.get('name')) and _text(p.get('entity'))},
        'house_entities': he, 'status_device': dev,
        'aliases': {_text(a.get('command')): _text(a.get('words')) for a in (o.get('aliases') or []) if _text(a.get('command')) and _text(a.get('words'))},
    }
    aisles = [[_text(a.get('name')), _text(a.get('words'))] for a in (o.get('aisles') or []) if _text(a.get('name')) and _text(a.get('words'))]
    if aisles:
        over['aisles'] = aisles
        order = [_text(x) for x in (o.get('aisle_order') or []) if _text(x)]
        over['aisle_order'] = (order or [a[0] for a in aisles]) + ([] if 'Other' in order else ['Other'])
    return ST.merge(over)


def load_options(path=None):
    try:
        o = json.load(open(path or OPTIONS))
    except (OSError, ValueError):
        o = {}
    cfg = settings_from_options(o)
    return {'token': str(o.get('discord_token') or '').strip(), 'dry_run': bool(o.get('dry_run', True)),
            'bot_name': str(o.get('bot_name') or 'Discord module'), 'cfg': cfg,
            'house': cfg['channel_house'], 'shopping': cfg['channel_shopping'],
            'allowed': {str(x).strip() for x in (o.get('allowed_user_ids') or []) if str(x).strip()},
            'ingest_url': str(o.get('ingest_url') or DEFAULT_INGEST_URL).rstrip('/'),
            'ingest_token': str(o.get('ingest_token') or '').strip(),
            'brain_webhook_id': str(o.get('brain_webhook_id') or '').strip(),
            'brain_url': str(o.get('brain_url') or 'http://homeassistant:8123').rstrip('/'),
            'mqtt_host': str(o.get('mqtt_host') or 'core-mosquitto').strip(), 'mqtt_port': int(o.get('mqtt_port') or 1883),
            'mqtt_user': str(o.get('mqtt_user') or 'ih_discord').strip(), 'mqtt_password': str(o.get('mqtt_password') or ''),
            'live_features': [str(x).strip() for x in (o.get('live_features') or []) if str(x).strip() in LIVE_CAPABLE],
            'old_service_disabled': bool(o.get('old_service_disabled', False)),
            'enable_scheduler': bool(o.get('enable_scheduler', False)),
            'signing_key': str(o.get('signing_key') or '').strip()}


class MappedDiscord(core.Discord):
    """Same as the service's Discord, plus configured channel names (a name asked for -> the name set in options)."""
    def __init__(self, token, dry, names):
        super().__init__(token, dry)
        self.names = names

    def find_channel(self, name):
        want = str(self.names.get(name, name)).strip()
        if want.isdigit():     # a channel NUMBER is used as it is: names can change
            return want
        return super().find_channel(want)


class Module:
    def __init__(self, opts, discord=None, ha=None, ring=None, root=None, data=None):
        self.opts, self.started, self.ring = opts, time.time(), ring or Ring()
        self.data = data or DATA
        self.stop_reason = ''
        self.root = root or os.path.join(DATA, 'root')   # empty folder: no host files are visible, features fall back to defaults
        self.beats, self.errors, self.err_last, self.threads = {}, collections.Counter(), {}, []
        self.error = None
        self.discord, self.ha = discord, ha        # templates: each feature gets its own copy so its dry flag is its own
        self.fctx = {}
        self.mode_why = {}
        self.cfg = ST.merge(dict(opts.get('cfg') or {}, channel_house=opts.get('house', ''), channel_shopping=opts.get('shopping', 'shopping')))
        self.brain = BrainClient(opts.get('brain_url', 'http://homeassistant:8123'), opts.get('brain_webhook_id', ''), dry=True) if HAVE_BRAIN else None   # plugin; stays dry
        self.ctx = None
        self.feats = {}
        self.last_selfcheck = None
        self.sched = None   # ScheduleClient when the scheduler is on
        self.sched_reason = ''
        if self.discord is None and opts['token']:
            self.discord = MappedDiscord(opts['token'], True, {})
        if self.ha is None:
            try:
                self.ha = core.HA(dry=True)
            except Exception as e:
                self.error = 'no Home Assistant access: ' + str(e)[:80]
        if self.discord is None:
            self.error = 'waiting for discord_token'

    def live_mode(self, name):
        """(is_live, reason). Live only for shopping/house, with every gate open. Anything else is dry."""
        o = self.opts
        if name not in LIVE_CAPABLE:
            return False, 'always dry'
        if o.get('dry_run', True):
            return False, 'dry_run option is on'
        if name not in (o.get('live_features') or []):
            return False, 'not in live_features'
        if not o.get('old_service_disabled'):
            return False, 'old_service_disabled is false'
        if os.path.exists(os.path.join(self.data, STOP_FILE)):
            return False, 'kill switch file %s exists' % STOP_FILE
        return True, 'live'

    def apply_modes(self):
        """Set every feature's Discord/HA/ctx dry flag from live_mode(). Called at build and before each tick, so STOP_LIVE acts fast."""
        for n, (d, h, c) in self.fctx.items():
            live, why = self.live_mode(n)
            d.dry = h.dry = c.dry = not live
            self.mode_why[n] = why

    def mode(self, name):
        return 'live' if name in self.fctx and not self.fctx[name][2].dry else 'dry'

    def build(self):
        """Create the feature objects. Each has its own Discord/HA/ctx so one can be live while the others stay dry."""
        if self.discord is None or self.ha is None:
            return False
        import importlib, copy
        for n in FEATURES:
            d, h = copy.copy(self.discord), copy.copy(self.ha)
            d.dry = h.dry = True
            c = core.Ctx(d, h, True, self.cfg)
            c.allowed = lambda: set(self.opts['allowed'])
            self.fctx[n] = (d, h, c)
            if n not in LIVE_CAPABLE:
                self.mode_why[n] = 'always dry'
        self.ctx = self.fctx['shopping'][2]
        self.apply_modes()
        for n in FEATURES:
            f = importlib.import_module('features.' + n).Feature(self.fctx[n][2], self.root)   # root: an empty folder, so optional files are never found
            if n == 'braininbox':
                f.store = self.brain
            self.feats[n] = f
        return True

    def run_feature(self, name):
        f = self.feats[name]
        while True:
            try:
                self.apply_modes()
                f.tick(); self.beats[name] = time.time()
            except Exception:
                self.errors[name] += 1
                self.err_last[name] = traceback.format_exc(limit=3).strip().splitlines()[-1][:200]
                core.log(f'{name} error:', self.err_last[name])
                time.sleep(30)
            time.sleep(f.interval)

    def start(self):
        if not self.build():
            return False
        for n in self.feats:
            t = threading.Thread(target=self.run_feature, args=(n,), daemon=True, name=n); t.start(); self.threads.append(t)
        return True

    def healthy(self):
        """Process is up. With a token, every feature thread must be alive."""
        return self.error is None and all(t.is_alive() for t in self.threads)

    def selfcheck(self, now=None):
        """Scheduled job. Feature threads must be alive and ticking. A tick is late after 3 x its interval + 60 s.
        Before the first tick, allow the same time from start-up. Stores the result for /status and returns ok (bool)."""
        now = time.time() if now is None else now
        problems, ages = [], {}
        if not self.threads:
            problems.append('no feature threads' + (': ' + self.error if self.error else ''))
        for n in FEATURES:
            if n not in self.feats:
                continue
            alive = any(t.name == n and t.is_alive() for t in self.threads)
            limit = 3 * float(getattr(self.feats[n], 'interval', 60)) + 60
            age = now - self.beats[n] if n in self.beats else None
            ages[n] = None if age is None else int(age)
            if not alive:
                problems.append(n + ' thread dead')
            elif age is None and now - self.started > limit:
                problems.append(n + ' never ticked')
            elif age is not None and age > limit:
                problems.append('%s last tick %ds ago' % (n, age))
        ok = not problems
        self.last_selfcheck = {'at': int(now), 'ok': ok, 'problems': problems, 'last_tick_s': ages}
        return ok

    def start_scheduler(self, bus=None):
        """Subscribe to ih/schedule/ih_discord/selfcheck and answer on .../done. Off unless enable_scheduler and a password are set."""
        o = self.opts
        if not o.get('enable_scheduler'):
            return False
        if not o.get('signing_key'):
            self.sched_reason = 'no signing key'
            return False
        if bus is None:
            if not o.get('mqtt_password'):
                return False
            bus = schedule_client.PahoBus(o['mqtt_host'], o['mqtt_port'], o['mqtt_user'], o['mqtt_password'], SOURCE, log=lambda m: core.log('schedule:', m))
        self.sched = schedule_client.ScheduleClient(SOURCE, bus, log=lambda m: core.log(m), signing_key=o['signing_key'])
        self.sched.on(SELFCHECK_JOB, lambda payload: self.selfcheck(), window_s=120)  # window_s = manifest window
        return self.sched.start() is not False

    def status(self):
        now = time.time()
        return {'name': SOURCE, 'version': VERSION, 'bot_name': self.opts['bot_name'], 'mode': 'live' if any(self.mode(n) == 'live' for n in FEATURES) else 'dry-run',
                'dry_run_option': self.opts['dry_run'], 'live_supported': True,
                'live_features_option': list(self.opts.get('live_features') or []), 'old_service_disabled': bool(self.opts.get('old_service_disabled')),
                'kill_switch': os.path.exists(os.path.join(self.data, STOP_FILE)),
                'feature_mode': {n: {'mode': self.mode(n), 'why': getattr(self, 'mode_why', {}).get(n, '')} for n in FEATURES}, 'uptime_s': int(now - self.started),
                'token_present': bool(self.opts['token']), 'ingest_configured': bool(self.opts['ingest_token']), 'brain_webhook_set': bool(self.brain and self.brain.configured()),
                'error': self.error, 'healthy': self.healthy(),
                'features': {n: {'alive': any(t.name == n and t.is_alive() for t in self.threads),
                                 'last_tick_s': None if n not in self.beats else int(now - self.beats[n]),
                                 'errors': self.errors[n], 'last_error': self.err_last.get(n)} for n in FEATURES},
                'scheduler': {'enabled': bool(self.opts.get('enable_scheduler')), 'active': self.sched is not None,
                              'reason': self.sched_reason if self.sched is None and self.opts.get('enable_scheduler') else '',
                              'rejected': self.sched.rejected if self.sched else 0},
                'last_selfcheck': self.last_selfcheck,
                'would_post_total': self.ring.would, 'last_would': list(self.ring.would_lines)[-10:]}


def push(mod, manifest, now=None, opener=None, log=print):
    """POST this module's manifest and status to the Intelligent Home add-on. Never raises. The token is never printed."""
    o = mod.opts
    if not o['ingest_token']:
        return False
    now = time.time() if now is None else now
    st = mod.status()
    body = json.dumps({'source': SOURCE, 'manifests': [manifest],
                       'status': [{'name': SOURCE, 'probed_at': now,
                                   'checks': [{'type': 'http', 'ok': st['healthy'], 'detail': '%s, %d would-post' % (st['mode'], st['would_post_total'])}]}]}).encode()
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
                # a missing token is not a crash: the add-on is up, but reports 503 so it shows red until configured
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
    lg = logging.getLogger('ihdiscord'); lg.setLevel(logging.INFO); lg.addHandler(ring)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    core._LOGGER = lg
    sys.stdout = sys.stderr = Out(lg)
    opts = load_options()
    mod = Module(opts, ring=ring)
    started = mod.start()
    try:
        mod.start_scheduler()
    except Exception as e:
        lg.info('scheduler not started (%s)', type(e).__name__)
    lg.info('ih_discord %s starting: %s%s', VERSION, ', '.join('%s=%s' % (n, mod.mode(n)) for n in FEATURES) if started else 'not started',
            '' if started else ' | ' + str(mod.error))
    if any(mod.mode(n) == 'live' for n in FEATURES):
        lg.info('LIVE: really posting for %s. Kill switch: create %s/%s', [n for n in FEATURES if mod.mode(n) == 'live'], DATA, STOP_FILE)
    manifest = json.load(open(os.path.join(HERE, 'manifest.json')))

    def pusher():
        while True:
            push(mod, manifest, log=lg.info); time.sleep(PUSH_EVERY)
    threading.Thread(target=pusher, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(mod)).serve_forever()


if __name__ == '__main__':
    main()
