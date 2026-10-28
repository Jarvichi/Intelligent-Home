#!/usr/bin/env python3
"""ih_discord 0.8.0: the Discord TRANSPORT of Intelligent Home. A pure Discord link: it holds the one bot token, reads each channel once (router.py, journal.py,
persisted cursor) and lets separate feature add-ons ("modules", e.g. ih_discord_house, ih_discord_shopping) receive messages and post/react/edit/delete through a
signed MQTT bus (busside.py, bus_client.py). It has NO commands of its own. GENERIC: every deployment detail comes from the add-on options.
0.7.0 (S4): the in-process shopping and house features and the brain-inbox plugin were REMOVED. Their old options (live_features, retired_features,
old_service_disabled, shopping_list_entity, meal_plan_script, shop_names, aisles, aisle_order, command_event, presence, house_entities, status_device, aliases,
brain_*) are still ACCEPTED so saved options validate, but are ignored. The router and the bus are always on.
0.8.0 (S6): channel ADMINISTRATION ops for modules (create_category, create_channel, move_channel, set_topic, delete_channel, list_channels), see admin.py.
All off by default; new OPTIONAL keys: `categories` ("key=id,key=id"), `permission_templates` ("name=user_id+user_id,..."), and per module row
can_create_channels, allowed_parent_categories, name_pattern, max_channels, max_creates_per_hour, permission_template, can_delete, category_ops.
Kill switches /data/STOP_ADMIN, /data/STOP_ADMIN_<module>; /data/ADMIN_LOCK_<module> after 3 limit hits in an hour.
A module writes to Discord only when dry_run is off AND it is in live_modules AND /data/STOP_LIVE (or STOP_LIVE_<module>) does not exist.
stdlib only (+ paho-mqtt for the bus). core.py and settings.py are COPIES (see sync_from_service.sh).
Serves /healthz and /status (no secrets). Pushes its manifest and status to the Intelligent Home add-on (POST /ingest)."""
import json, os, sys, threading, time, traceback, logging, collections, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core
import schedule_client
import bus_client
import router as R
import journal as J
import busside
import admin

VERSION = '0.8.3'
SOURCE = 'ih_discord'
STOP_FILE = 'STOP_LIVE'   # kill switch: create /data/STOP_LIVE and every module's Discord writes go dry at once
DEFAULT_INGEST_URL = 'http://172.30.32.1:8790'   # the Supervisor gateway seen from an add-on; the orchestrator add-on listens there
OPTIONS = os.environ.get('IHD_OPTIONS', '/data/options.json')
DATA = os.environ.get('IHD_DATA', '/data')
PORT = int(os.environ.get('IHD_PORT', '8791'))
PUSH_EVERY = 30
SELFCHECK_JOB = 'selfcheck'
CURSOR_TO_NOW = 'CURSOR_TO_NOW'   # create /data/CURSOR_TO_NOW: at the next start every cursor jumps to the newest message (cut-overs)
ROUTER_EVERY = 4
RESERVED_MODULES = ('shopping', 'house', 'braininbox', 'router', 'transport')   # names of the removed in-process claimants (and the transport itself): a bus module may not use them
# options of the removed in-process features: still accepted by config.yaml so saved options validate, ignored since 0.7.0
DEPRECATED_OPTIONS = ('live_features', 'retired_features', 'old_service_disabled', 'shopping_list_entity', 'meal_plan_script', 'shop_names', 'aisles',
                      'aisle_order', 'command_event', 'presence', 'house_entities', 'status_device', 'aliases', 'brain_webhook_id', 'brain_url')


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


class Out:   # core.py uses print(): send it to the logger
    def __init__(self, lg): self.lg = lg
    def write(self, t):
        if t.strip(): self.lg.info(t.rstrip())
    def flush(self): pass


def _text(v):
    return str(v or '').strip()


def load_options(path=None):
    try:
        o = json.load(open(path or OPTIONS))
    except (OSError, ValueError):
        o = {}
    ch = o.get('channels') or {}
    return {'token': str(o.get('discord_token') or '').strip(), 'dry_run': bool(o.get('dry_run', True)),
            'bot_name': str(o.get('bot_name') or 'Discord module'),
            'house': _text(ch.get('house')), 'shopping': _text(ch.get('shopping')) or 'shopping',   # channel KEYS for modules: 'house' and 'shopping'
            'allowed': {str(x).strip() for x in (o.get('allowed_user_ids') or []) if str(x).strip()},
            'ingest_url': str(o.get('ingest_url') or DEFAULT_INGEST_URL).rstrip('/'),
            'ingest_token': str(o.get('ingest_token') or '').strip(),
            'mqtt_host': str(o.get('mqtt_host') or 'core-mosquitto').strip(), 'mqtt_port': int(o.get('mqtt_port') or 1883),
            'mqtt_user': str(o.get('mqtt_user') or 'ih_discord').strip(), 'mqtt_password': str(o.get('mqtt_password') or ''),
            'enable_scheduler': bool(o.get('enable_scheduler', False)),
            'signing_key': str(o.get('signing_key') or '').strip(),
            'offline_notice': bool(o.get('offline_notice', False)), 'log_payloads': o.get('log_payloads') is True,
            'max_age_s': max(60, int(o.get('max_age_s') or 600)),
            'module_grants': busside.parse_modules(o.get('modules'), RESERVED_MODULES),
            'extra_channels': {_text(c.get('key')): _text(c.get('id')) for c in (o.get('extra_channels') or [])
                               if bus_client.KEY_RE.match(_text(c.get('key'))) and _text(c.get('id')) and _text(c.get('key')) not in ('house', 'shopping')},
            'live_modules': [str(x).strip() for x in (o.get('live_modules') or []) if str(x).strip()],
            'categories': admin.parse_categories(o.get('categories')),                 # S6: category key -> category id (optional)
            'permission_templates': admin.parse_templates(o.get('permission_templates')),   # S6: named templates (optional)
            'deprecated_set': sorted(k for k in DEPRECATED_OPTIONS if o.get(k) not in (None, '', [], {}, False))}


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
    def __init__(self, opts, discord=None, ring=None, data=None):
        self.opts, self.started, self.ring = opts, time.time(), ring or Ring()
        self.data = data or DATA
        self.beats, self.errors, self.err_last, self.threads = {}, collections.Counter(), {}, []
        self.error = None
        self.discord = discord
        self.last_selfcheck = None
        self.sched = None   # ScheduleClient when the scheduler is on
        self.sched_reason = ''
        self.router = self.journal = self.cursors = self.bus_t = self.tdiscord = None   # set by build / start_bus
        self.bus_reason = ''
        if self.discord is None and opts['token']:
            self.discord = MappedDiscord(opts['token'], True, {})
        if self.discord is None:
            self.error = 'waiting for discord_token'

    def bus_live(self, name):
        """(is_live, reason) for a bus module's Discord writes. Needs dry_run off, the module in live_modules and no kill switch file."""
        o = self.opts
        if o.get('dry_run', True):
            return False, 'dry_run option is on'
        if name not in (o.get('live_modules') or []):
            return False, 'not in live_modules'
        for f in (STOP_FILE, STOP_FILE + '_' + name):
            if os.path.exists(os.path.join(self.data, f)):
                return False, 'kill switch file %s exists' % f
        return True, 'live'

    def notice_live(self):
        return not self.opts.get('dry_run', True) and not os.path.exists(os.path.join(self.data, STOP_FILE))

    def channels(self):
        chans = {'house': self.opts.get('house', ''), 'shopping': self.opts.get('shopping', '')}
        chans.update(self.opts.get('extra_channels') or {})
        return {k: v for k, v in chans.items() if v}

    def build(self):
        """The single poller + journal + cursor. Modules claim channel keys over the bus (start_bus)."""
        if self.discord is None:
            return False
        import copy
        self.tdiscord = copy.copy(self.discord)   # the transport's own Discord object: its writes are gated by bus_live / notice_live, not by a flag on it
        self.tdiscord.dry = False
        self.journal = J.Journal(os.path.join(self.data, 'journal.jsonl'))
        self.cursors = J.Cursors(os.path.join(self.data, 'cursor.json'))
        flag = os.path.join(self.data, CURSOR_TO_NOW)
        if os.path.exists(flag):
            self.cursors.reset()
            try: os.remove(flag)
            except OSError: pass
            core.log('router: cursor_to_now: every channel starts at its newest message')
        self.router = R.Router(self.channels(), lambda: set(self.opts['allowed']), self.journal, self.cursors, self.tdiscord,
                               max_age_s=self.opts.get('max_age_s', 600), log=core.log)
        return True

    def run_router(self):
        try:
            self.router.recover()
        except Exception as e:
            core.log('router: recovery failed (%s)' % type(e).__name__)
        while True:
            try:
                self.router.poll_once()
                if self.bus_t:
                    self.bus_t.tick()
                self.beats['router'] = time.time()
            except Exception:
                self.errors['router'] += 1
                self.err_last['router'] = traceback.format_exc(limit=3).strip().splitlines()[-1][:200]
                core.log('router error:', self.err_last['router'])
                time.sleep(30)
            time.sleep(ROUTER_EVERY)

    def start(self):
        if not self.build():
            return False
        t = threading.Thread(target=self.run_router, daemon=True, name='router'); t.start(); self.threads.append(t)
        return True

    def healthy(self):
        """Process is up. With a token, the router thread must be alive."""
        return self.error is None and all(t.is_alive() for t in self.threads)

    def selfcheck(self, now=None):
        """Scheduled job. The router thread must be alive and ticking. A tick is late after 3 x its interval + 60 s.
        Before the first tick, allow the same time from start-up. Stores the result for /status and returns ok (bool)."""
        now = time.time() if now is None else now
        problems, ages = [], {}
        if not self.threads:
            problems.append('no router thread' + (': ' + self.error if self.error else ''))
        else:
            age = now - self.beats['router'] if 'router' in self.beats else None
            ages['router'] = None if age is None else int(age)
            limit = 3 * ROUTER_EVERY + 60
            if not any(t.name == 'router' and t.is_alive() for t in self.threads):
                problems.append('router thread dead')
            elif (age is None and now - self.started > limit) or (age is not None and age > limit):
                problems.append('router last tick %s' % ('never' if age is None else '%ds ago' % age))
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

    def start_bus(self, bus=None):
        """Open the signed bus for the modules in `modules`. Always on since 0.7.0 (needs a module key and the mqtt password)."""
        o = self.opts
        if self.router is None:
            self.bus_reason = 'router did not start'
            return False
        grants = o.get('module_grants') or {}
        if not any(bus_client.good_key(g.key) for g in grants.values()):
            self.bus_reason = 'no module with a signing key'
            return False
        if bus is None:
            if not o.get('mqtt_password'):
                self.bus_reason = 'no mqtt password'
                return False
            bus = bus_client.PahoBus(o['mqtt_host'], o['mqtt_port'], o['mqtt_user'], o['mqtt_password'], SOURCE + '_bus', log=lambda m: core.log('bus:', m),
                                     will=('ih/discord/hb/transport', json.dumps({'v': 1, 'state': 'down'})))
        self.bus_t = busside.BusTransport(bus, self.router, self.tdiscord, grants, self.channels(), self.data, journal=self.journal, log=core.log,
                                          is_live=self.bus_live, offline_notice=bool(o.get('offline_notice')), notice_live=self.notice_live,
                                          max_age_s=o.get('max_age_s', 600), threaded=True,
                                          categories=o.get('categories'), templates=o.get('permission_templates'), log_payloads=bool(o.get('log_payloads')))
        ok = self.bus_t.start()
        if not ok:
            self.bus_reason = 'bus did not start'
            self.bus_t = None
        return ok

    def status(self):
        now = time.time()
        live = [n for n in (self.opts.get('live_modules') or []) if self.bus_live(n)[0]]
        return {'name': SOURCE, 'version': VERSION, 'bot_name': self.opts['bot_name'], 'mode': 'live' if live else 'dry-run',
                'dry_run_option': self.opts['dry_run'], 'live_supported': True, 'live_modules_active': live,
                'kill_switch': os.path.exists(os.path.join(self.data, STOP_FILE)), 'uptime_s': int(now - self.started),
                'token_present': bool(self.opts['token']), 'ingest_configured': bool(self.opts['ingest_token']),
                'deprecated_options_ignored': list(self.opts.get('deprecated_set') or []),
                'error': self.error, 'healthy': self.healthy(),
                'poller': {'alive': any(t.name == 'router' and t.is_alive() for t in self.threads),
                           'last_tick_s': None if 'router' not in self.beats else int(now - self.beats['router']),
                           'errors': self.errors['router'], 'last_error': self.err_last.get('router')},
                'scheduler': {'enabled': bool(self.opts.get('enable_scheduler')), 'active': self.sched is not None,
                              'reason': self.sched_reason if self.sched is None and self.opts.get('enable_scheduler') else '',
                              'rejected': self.sched.rejected if self.sched else 0},
                'router': dict({'enabled': True, 'active': self.router is not None},
                               **(self.router.status() if self.router else {})),
                'bus': dict({'enabled': True, 'active': self.bus_t is not None, 'offline_notice': bool(self.opts.get('offline_notice')),
                             'reason': self.bus_reason if self.bus_t is None else ''},
                            **(self.bus_t.status() if self.bus_t else {})),
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
    try:
        if started and mod.start_bus():
            lg.info('bus: open for %d module(s)', len(opts.get('module_grants') or {}))
        elif started:
            lg.info('bus: not open (%s)', mod.bus_reason)
    except Exception as e:
        lg.info('bus not started (%s)', type(e).__name__)
    if opts.get('deprecated_set'):
        lg.info('options ignored since 0.7.0 (handled by the module add-ons): %s', ', '.join(opts['deprecated_set']))
    lg.info('ih_discord %s starting: %s', VERSION, 'transport only' if started else 'not started | ' + str(mod.error))
    live = [n for n in (opts.get('live_modules') or []) if mod.bus_live(n)[0]]
    if live:
        lg.info('LIVE: modules %s may really post. Kill switch: create %s/%s', live, DATA, STOP_FILE)
    manifest = json.load(open(os.path.join(HERE, 'manifest.json')))

    def pusher():
        while True:
            push(mod, manifest, log=lg.info); time.sleep(PUSH_EVERY)
    threading.Thread(target=pusher, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(mod)).serve_forever()


if __name__ == '__main__':
    main()
