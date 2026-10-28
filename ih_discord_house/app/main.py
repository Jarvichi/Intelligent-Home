#!/usr/bin/env python3
"""ih_discord_house 0.2.0: the HOUSE commands (status, lights, bins, who is home, ...) as a signed bus MODULE of the ih_discord transport (S2 of the split).
GENERIC: every house detail comes from the add-on options (settings.py), none from code. It never holds a Discord token or a channel number:
it receives `in` events for its channel KEY over the MQTT bus and answers with `out` post ops (bus_client.ModuleClient). It reads Home Assistant with
its own SUPERVISOR_TOKEN. The answer logic is the shared features/house.py in ROUTED mode (copied by sync_from_service.sh, never edited here).
SHADOW (option, default true): every outgoing op is sent with dry=true and logged as '[DRY] would post ...'; HA writes (command event, light off) are
only logged too. So it can run beside the in-process house feature and its answers can be compared. /data/STOP_LIVE forces the same at once.
stdlib only (paho-mqtt for the real bus). core.py, settings.py, features/ and bus_client.py are COPIES (see sync_from_service.sh).
Serves /healthz and /status (no secrets). Pushes its manifest and status to the orchestrator (POST /ingest)."""
import json, os, sys, threading, time, logging, collections, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core
import settings as ST
import bus_client
import importlib

VERSION = '0.2.2'
DATA = os.environ.get('IHH_DATA', '/data')
OPTIONS = os.path.join(DATA, 'options.json')
PORT = int(os.environ.get('IHH_PORT', '8796'))
DEFAULT_INGEST_URL = 'http://172.30.32.1:8790'
PUSH_EVERY = 60
STOP_FILE = 'STOP_LIVE'
DEFAULT_MODULE = 'ih_discord_house'


def _text(v):
    return str(v or '').strip()


def settings_from_options(o):
    """The deployment details (settings.py) from the add-on options. Same option names as ih_discord, so values copy over."""
    he = {k: _text(v) for k, v in (o.get('house_entities') or {}).items() if _text(v)}
    dev = {k: _text(v) for k, v in (o.get('status_device') or {}).items() if _text(v)}
    over = {
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
    name = _text(o.get('module_name')) or DEFAULT_MODULE
    ck = _text(o.get('channel_key')) or 'house'
    return {'module': name if bus_client.NAME_RE.match(name) else DEFAULT_MODULE,
            'channel_key': ck if bus_client.KEY_RE.match(ck) else 'house',
            'signing_key': _text(o.get('signing_key')), 'log_payloads': o.get('log_payloads') is True, 'shadow': o.get('shadow') is not False,   # anything but an explicit false = shadow
            'cfg': settings_from_options(o),
            'mqtt_host': _text(o.get('mqtt_host')) or 'core-mosquitto', 'mqtt_port': int(o.get('mqtt_port') or 1883),
            'mqtt_user': _text(o.get('mqtt_user')) or DEFAULT_MODULE, 'mqtt_password': str(o.get('mqtt_password') or ''),
            'ingest_url': (_text(o.get('ingest_url')) or DEFAULT_INGEST_URL).rstrip('/'), 'ingest_token': _text(o.get('ingest_token'))}


class Ring(logging.Handler):
    """Keeps the last lines in memory and counts what the module WOULD have done."""
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


class Out:   # feature modules use print(): send it to the logger
    def __init__(self, lg): self.lg = lg
    def write(self, t):
        if t.strip(): self.lg.info(t.rstrip())
    def flush(self): pass


class BusDiscord:
    """Stands in for the Discord object the house feature expects. post() becomes an `out` op on the bus. In dry mode (shadow or STOP_LIVE)
    the op carries dry=true and the text is logged as '[DRY] would post ...' (the Ring counts those lines)."""
    def __init__(self, mod):
        self.mod = mod
        self.dry = True
        self.reply_to = None

    def me(self):
        return {'id': '__bus__'}

    def post(self, chan, text, **kw):
        m = self.mod
        if m.dry_now():
            m.lg.info('[DRY] would post (reply to %s): %s', self.reply_to, text.replace('\n', ' | ')[:1500])
        res = m.client.post(m.opts['channel_key'], text, reply_to=self.reply_to, tag='house')
        m.out_ops += 1
        if res is None:
            m.out_failed += 1
        return 200, res

    def req(self, *a, **k):   # nothing in routed house mode reads Discord; refuse quietly
        return 405, None


class Module:
    def __init__(self, opts, bus=None, ha=None, ring=None, data=None, clock=time.time, threaded=True):
        self.opts, self.started, self.ring, self.data, self.clock = opts, clock(), ring or Ring(), data or DATA, clock
        self.lg = logging.getLogger('ihhouse')
        self.in_count = self.out_ops = self.out_failed = 0
        self.last_in = None
        self.error, self.client, self.feature, self.bus, self.bus_reason = None, None, None, bus, ''
        self.cur_allowed = set()
        self.cfg = ST.merge(dict(opts.get('cfg') or {}, channel_house=opts['channel_key']))
        try:
            self.ha = ha or core.HA(dry=True)
        except Exception as e:
            self.ha, self.error = None, 'no Home Assistant access: ' + str(e)[:80]
        self.threaded = threaded

    def dry_now(self):
        return bool(self.opts['shadow']) or os.path.exists(os.path.join(self.data, STOP_FILE))

    def apply_mode(self):
        d = self.dry_now()
        self.discord.dry = self.hactx.dry = self.ha.dry = d
        if self.client:
            self.client.dry = d
        return d

    def build(self):
        if self.ha is None:
            return False
        self.discord = BusDiscord(self)
        self.hactx = core.Ctx(self.discord, self.ha, True, self.cfg)
        self.hactx.allowed = lambda: set(self.cur_allowed)
        self.feature = importlib.import_module('features.house').Feature(self.hactx, os.path.join(self.data, 'root'))
        self.feature.routed = True
        self.feature.chan = self.opts['channel_key']
        if self.bus is None:
            if not self.opts['mqtt_password']:
                self.bus_reason = 'no mqtt password'
                return False
            self.bus = bus_client.PahoBus(self.opts['mqtt_host'], self.opts['mqtt_port'], self.opts['mqtt_user'], self.opts['mqtt_password'],
                                          self.opts['module'] + '_mod', log=lambda m: self.lg.info('bus: ' + m))
        self.client = bus_client.ModuleClient(self.opts['module'], self.opts['signing_key'], self.bus, version=VERSION, log=lambda m: self.lg.info(m),
                                              clock=self.clock, threaded=self.threaded, log_payloads=self.opts.get('log_payloads', False), ops_needed=['post'], dry=True)
        self.client.on_message(self.opts['channel_key'], self.on_in, any=True, public=['whoami'])
        self.apply_mode()
        return True

    def on_in(self, d):
        """One verified `in` event (the ack was already sent). Only messages are handled; reactions are not used by the house feature."""
        if d.get('type') != 'message':
            return
        self.in_count += 1
        self.last_in = self.clock()
        self.apply_mode()
        self.tz_once()
        uid = str(d.get('author_id') or '')
        self.cur_allowed = {uid} if d.get('allowed') else set()
        self.discord.reply_to = d.get('msg_id')
        raw = {'id': d.get('msg_id'), 'content': d.get('content') or '', 'author': {'id': uid, 'username': d.get('author_name') or '', 'bot': False}}
        self.feature.handle_message(raw)

    def tz_once(self):
        if not getattr(self.feature, 'tz_done', True):
            try:
                importlib.import_module('features.house').set_tz(self.ha.config().get('time_zone'))
            except Exception:
                pass
            self.feature.tz_done = True

    def start(self):
        if not self.build():
            return False
        ok = self.client.start()
        if not ok:
            self.bus_reason = 'no usable signing key' if not self.client.active else 'bus did not start'
        return bool(ok)

    def healthy(self):
        return self.error is None

    def status(self):
        now = self.clock()
        hb = self.client.last_hello if self.client and self.client.last_hello else None
        return {'name': self.opts['module'], 'version': VERSION, 'shadow': bool(self.opts['shadow']), 'dry_now': self.dry_now(),
                'kill_switch': os.path.exists(os.path.join(self.data, STOP_FILE)), 'uptime_s': int(now - self.started),
                'error': self.error, 'healthy': self.healthy(), 'channel_key': self.opts['channel_key'],
                'bus': {'connected': bool(self.bus and getattr(self.bus, 'connected', False)), 'active': bool(self.client and self.client.active),
                        'reason': self.bus_reason, 'last_heartbeat_s': None if hb is None else int(now - hb)},
                'counts': {'in': self.in_count, 'out': self.out_ops, 'out_failed': self.out_failed,
                           'rejected': sum((self.client.rejected if self.client else {}).values()), 'rejected_by': dict(self.client.rejected) if self.client else {}},
                'last_in_s': None if self.last_in is None else int(now - self.last_in),
                'would_post_total': self.ring.would, 'last_would': list(self.ring.would_lines)[-10:]}


def push(mod, manifest, now=None, opener=None, log=print):
    """POST this module's manifest and status to the orchestrator. Never raises. The token is never printed."""
    o = mod.opts
    if not o['ingest_token']:
        return False
    now = time.time() if now is None else now
    st = mod.status()
    body = json.dumps({'source': o['module'], 'manifests': [manifest],
                       'status': [{'name': o['module'], 'probed_at': now,
                                   'checks': [{'type': 'http', 'ok': st['healthy'] and st['bus']['active'],
                                               'detail': '%s, bus %s, %d in, %d would-post' % ('shadow' if st['shadow'] else 'live', 'up' if st['bus']['connected'] else 'down',
                                                                                              st['counts']['in'], st['would_post_total'])}]}]}).encode()
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
    lg = logging.getLogger('ihhouse'); lg.setLevel(logging.INFO); lg.addHandler(ring)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    core._LOGGER = lg
    sys.stdout = sys.stderr = Out(lg)
    opts = load_options()
    mod = Module(opts, ring=ring)
    started = False
    try:
        started = mod.start()
    except Exception as e:
        mod.error = 'start failed (%s)' % type(e).__name__
    lg.info('ih_discord_house %s starting: %s, %s', VERSION, 'SHADOW (nothing is posted)' if opts['shadow'] else 'LIVE-CAPABLE (the transport decides)',
            'bus started' if started else 'bus not started: %s' % (mod.bus_reason or mod.error))
    manifest = json.load(open(os.path.join(HERE, 'manifest.json')))

    def pusher():
        while True:
            push(mod, manifest, log=lg.info); time.sleep(PUSH_EVERY)
    threading.Thread(target=pusher, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(mod)).serve_forever()


if __name__ == '__main__':
    main()
