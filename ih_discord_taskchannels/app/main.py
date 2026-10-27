#!/usr/bin/env python3
"""ih_discord_taskchannels 0.1.1: one private Discord channel per task, as a signed bus MODULE of the ih_discord transport (S6 of the split).
GENERIC: every detail (category keys, template name, retention days, channel keys) comes from the add-on options, none from code.
It never holds a Discord token or a channel number. It receives `in` events for ONE channel key (default `tasks`, a catch-all claim) and creates, moves,
re-topics and deletes channels through the transport's ADMIN ops (create_channel, move_channel, set_topic, delete_channel, list_channels). The transport
checks the grants (name pattern, categories, template, quotas, kill switches); this module only asks.
Commands (typed by an allowed user in the commands channel key):
  new <name> | <goal> [| <opening message>]   create the channel in the "work in progress" category; the goal becomes the channel topic
  done <name> [| <summary>]                   move it to the "done" category
  reopen <name>                               move it back
  list                                        tracked task channels
  help
A daily cleanup deletes channels that have been done for more than `cleanup_days` (default 30). It only asks to delete channels THIS module created
(the transport refuses anything else). The goal text is never stored here; names and dates are (/data/state.json).
SHADOW (option, default true): every op is sent with dry=true and logged '[DRY] would ...'; nothing is created and no state is recorded.
/data/STOP_LIVE forces the same at once. The transport can also stop admin ops alone with /data/STOP_ADMIN (or _<module>).
No Home Assistant access is needed (no homeassistant_api). stdlib only (paho-mqtt for the real bus). Serves /healthz and /status (no secrets)."""
import json, os, re, sys, threading, time, logging, collections, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bus_client

VERSION = '0.1.1'
DATA = os.environ.get('IHT_DATA', '/data')
OPTIONS = os.path.join(DATA, 'options.json')
PORT = int(os.environ.get('IHT_PORT', '8797'))
DEFAULT_INGEST_URL = 'http://172.30.32.1:8790'
PUSH_EVERY = 60
STOP_FILE = 'STOP_LIVE'
DEFAULT_MODULE = 'ih_discord_taskchannels'
OPS_NEEDED = ['post', 'create_channel', 'move_channel', 'set_topic', 'delete_channel', 'list_channels']
CLEANUP_EVERY_S = 24 * 3600
FIRST_CLEANUP_AFTER_S = 120
SYNC_AFTER_S = 20
NAME_PREFIX = 'task-'
SLUG_MAX = 30
HELP = ("**Task channels**\n`new <name> | <goal>` creates a private channel for the task (the goal becomes its topic)\n"
        "`done <name> | <summary>` moves it to Done\n`reopen <name>` moves it back\n`list` shows the task channels")


def _text(v):
    return str(v or '').strip()


def slug(t, maxlen=SLUG_MAX):
    return re.sub(r'[^a-z0-9]+', '-', str(t).lower()).strip('-')[:maxlen].strip('-')


def pretty(t):
    """One sentence per line, a blank line before a question or option list (so a channel topic is easy to read)."""
    t = re.sub(r'(?<=[.!?:])\s+(?=[A-Z(0-9])', '\n', str(t).strip())
    return re.sub(r'\n(?=(Reply|Question|Open|Options?)\b)', '\n\n', t)


def _key(v, default):
    v = _text(v)
    return v if bus_client.KEY_RE.match(v) else default


def load_options(path=None):
    try:
        o = json.load(open(path or OPTIONS))
    except (OSError, ValueError):
        o = {}
    name = _text(o.get('module_name')) or DEFAULT_MODULE
    try:
        days = max(1, min(365, int(o.get('cleanup_days') or 30)))
    except (TypeError, ValueError):
        days = 30
    return {'module': name if bus_client.NAME_RE.match(name) else DEFAULT_MODULE,
            'signing_key': _text(o.get('signing_key')), 'shadow': o.get('shadow') is not False,   # anything but an explicit false = shadow
            'command_channel_key': _key(o.get('command_channel_key'), 'tasks'),
            'wip_category': _key(o.get('wip_category'), 'wip'), 'done_category': _key(o.get('done_category'), 'done'),
            'template': _text(o.get('template')), 'cleanup_days': days,
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


class Module:
    def __init__(self, opts, bus=None, ring=None, data=None, clock=time.time, threaded=True):
        self.opts, self.started, self.ring, self.data, self.clock = opts, clock(), ring or Ring(), data or DATA, clock
        self.lg = logging.getLogger('ihtaskchannels')
        self.threaded = threaded
        self.lock = threading.RLock()
        self.error, self.client, self.bus, self.bus_reason = None, None, bus, ''
        self.in_count = self.out_ops = self.out_failed = self.created = self.finished = self.deleted = 0
        self.last_in = None
        self.tasks = {}                  # slug -> {name, key, id, state (wip|done), created, moved, summary}
        self.last_cleanup = 0.0
        self.synced = False
        self.slug_max = max(1, min(SLUG_MAX, 60 - len(opts['module']) - 1))
        self.load_state()

    # ---- mode ----
    def dry_now(self):
        return bool(self.opts['shadow']) or os.path.exists(os.path.join(self.data, STOP_FILE))

    def apply_mode(self):
        d = self.dry_now()
        if self.client:
            self.client.dry = d
        return d

    # ---- state (names and dates only; never the goal text) ----
    def state_path(self): return os.path.join(self.data, 'state.json')

    def save_state(self):
        try:
            tmp = self.state_path() + '.tmp'
            with open(tmp, 'w') as fh:
                json.dump({'tasks': self.tasks, 'last_cleanup': self.last_cleanup}, fh)
            os.replace(tmp, self.state_path())
        except OSError:
            self.lg.info('state not saved')

    def load_state(self):
        try:
            d = json.load(open(self.state_path()))
        except (OSError, ValueError):
            return
        t = d.get('tasks')
        self.tasks = {k: v for k, v in (t or {}).items() if isinstance(v, dict) and v.get('key')} if isinstance(t, dict) else {}
        try:
            self.last_cleanup = float(d.get('last_cleanup') or 0)
        except (TypeError, ValueError):
            self.last_cleanup = 0.0

    def log_deleted(self, slug_, t):
        try:
            with open(os.path.join(self.data, 'cleanup_log.jsonl'), 'a') as f:   # name and dates only
                f.write(json.dumps({'at': int(self.clock()), 'task': t.get('name', '')[:100], 'key': t.get('key'), 'created': t.get('created'), 'done': t.get('moved'),
                                    'summary': (t.get('summary') or '')[:200]}) + '\n')
        except OSError:
            pass

    # ---- bus ops (each logs '[DRY] would ...' when dry, then sends; the transport also forces dry for a shadow grant) ----
    def _do(self, desc, fn):
        if self.dry_now():
            self.lg.info('[DRY] would %s', desc[:600])
        res = fn()
        self.out_ops += 1
        if res is None or not res.get('ok'):
            self.out_failed += 1
        return res

    def bus_post(self, ck, text, reply_to=None, tag='tasks'):
        return self._do('post to %s: %s' % (ck, text.replace('\n', ' | ')), lambda: self.client.post(ck, text, reply_to=reply_to, tag=tag))

    def reply(self, text, mid=None):
        return self.bus_post(self.opts['command_channel_key'], text, reply_to=mid)

    # ---- build / start ----
    def build(self):
        if self.bus is None:
            if not self.opts['mqtt_password']:
                self.bus_reason = 'no mqtt password'
                return False
            self.bus = bus_client.PahoBus(self.opts['mqtt_host'], self.opts['mqtt_port'], self.opts['mqtt_user'], self.opts['mqtt_password'],
                                          self.opts['module'] + '_mod', log=lambda m: self.lg.info('bus: ' + m))
        self.client = bus_client.ModuleClient(self.opts['module'], self.opts['signing_key'], self.bus, version=VERSION, log=lambda m: self.lg.info(m),
                                              clock=self.clock, threaded=self.threaded, ops_needed=OPS_NEEDED, dry=True)
        self.client.on_message(self.opts['command_channel_key'], self.on_in, any=True)
        self.client.on_events('_admin', self.on_admin)
        self.apply_mode()
        return True

    def start(self):
        if not self.build():
            return False
        ok = self.client.start()
        if not ok:
            self.bus_reason = 'no usable signing key' if not self.client.active else 'bus did not start'
        return bool(ok)

    # ---- events ----
    def on_in(self, d):
        """One verified `in` event on the commands channel key (the ack was already sent)."""
        with self.lock:
            self.apply_mode()
            if d.get('type') != 'message':
                return
            self.in_count += 1
            self.last_in = self.clock()
            if not d.get('allowed'):
                return   # only users on the transport's allow-list can give commands
            text = (d.get('content') or '').strip()
            out = self.command(text, d.get('msg_id'))
            if out:
                self.reply(out, d.get('msg_id'))

    def on_admin(self, d):
        """The transport says a channel this module created is gone from Discord (deleted by hand)."""
        with self.lock:
            if d.get('type') != 'channel_gone':
                return
            for s, t in list(self.tasks.items()):
                if t.get('key') == d.get('channel_key'):
                    self.tasks.pop(s, None)
                    self.lg.info('task channel %s was deleted in Discord: forgotten', s)
            self.save_state()

    # ---- commands ----
    def command(self, text, mid=None):
        word, _, rest = text.partition(' ')
        w = word.lower().rstrip(':')
        if w == 'new':
            return self.cmd_new(rest.strip())
        if w == 'done':
            return self.cmd_done(rest.strip())
        if w == 'reopen':
            return self.cmd_reopen(rest.strip())
        if w == 'list':
            return self.cmd_list()
        if w == 'help':
            return HELP
        return None   # not a command: stay quiet

    def find(self, ref):
        m = re.fullmatch(r'<#(\d+)>|(\d{15,25})', ref.strip())
        if m:
            cid = m.group(1) or m.group(2)
            hits = [k for k, t in self.tasks.items() if str(t.get('id')) == cid]
            return hits[0] if len(hits) == 1 else None
        s = slug(ref, 60)
        if s in self.tasks:
            return s
        hits = [k for k in self.tasks if s and k.startswith(s)] or [k for k, t in self.tasks.items() if t.get('name', '').lower() == ref.strip().lower()]
        return hits[0] if len(hits) == 1 else None

    def cmd_new(self, rest):
        parts = [p.strip() for p in rest.split('|')]
        name = parts[0]
        goal = parts[1] if len(parts) > 1 else ''
        opening = parts[2] if len(parts) > 2 else ''
        sl = slug(name, self.slug_max)
        if not sl:
            return "Usage: `new <name> | <goal>`"
        if sl in self.tasks:
            return "There is already a task channel called `%s`." % sl
        o = self.opts
        res = self._do('create channel %s%s in %s' % (NAME_PREFIX, sl, o['wip_category']),
                       lambda: self.client.create_channel(NAME_PREFIX + sl, o['wip_category'], sl, topic=pretty(goal or name)[:1000], template=o['template'] or None))
        if res is None:
            return "The Discord link did not answer. Try again in a minute."
        if not res.get('ok'):
            return "Could not create the channel (%s%s)." % (res.get('code', 'error'), ': ' + res['detail'] if res.get('detail') else '')
        if res.get('code') == 'dry':
            return "(dry run) would create `%s%s`." % (NAME_PREFIX, sl)
        key, cid = res.get('channel_key') or '', res.get('discord_id')
        self.tasks[sl] = {'name': name[:100], 'key': key, 'id': cid, 'state': 'wip', 'created': int(self.clock()), 'moved': None, 'summary': ''}
        self.created += 1
        self.save_state()
        self.bus_post(key, '**%s**\n%s' % (name[:200], pretty(opening or goal or 'New task channel.')[:1500]), tag='task')
        return "Created <#%s> in Work in Progress." % cid

    def cmd_done(self, rest):
        ref, _, summary = rest.partition('|')
        s = self.find(ref.strip())
        if not s:
            return "No task channel matches `%s`." % ref.strip()[:60]
        t = self.tasks[s]
        if t['state'] == 'done':
            return "`%s` is already done." % s
        res = self._do('move %s to %s' % (t['key'], self.opts['done_category']), lambda: self.client.move_channel(t['key'], self.opts['done_category']))
        if res is None or not res.get('ok'):
            return "Could not move the channel (%s)." % ((res or {}).get('code', 'no answer'))
        if res.get('code') == 'dry':
            return "(dry run) would move `%s` to Done." % s
        t['state'], t['moved'], t['summary'] = 'done', int(self.clock()), summary.strip()[:300]
        self.finished += 1
        self.save_state()
        days = self.opts['cleanup_days']
        self.bus_post(t['key'], 'Done%s (moved to Done; deleted after %d days)' % (': ' + t['summary'] if t['summary'] else '', days), tag='task')
        return "Moved <#%s> to Done. It is deleted after %d days." % (t['id'], days)

    def cmd_reopen(self, rest):
        s = self.find(rest)
        if not s:
            return "No task channel matches `%s`." % rest[:60]
        t = self.tasks[s]
        if t['state'] != 'done':
            return "`%s` is not done." % s
        res = self._do('move %s to %s' % (t['key'], self.opts['wip_category']), lambda: self.client.move_channel(t['key'], self.opts['wip_category']))
        if res is None or not res.get('ok'):
            return "Could not move the channel (%s)." % ((res or {}).get('code', 'no answer'))
        if res.get('code') == 'dry':
            return "(dry run) would move `%s` back." % s
        t['state'], t['moved'] = 'wip', None
        self.save_state()
        return "Moved <#%s> back to Work in Progress." % t['id']

    def cmd_list(self):
        if not self.tasks:
            return "No task channels."
        now, days, lines = self.clock(), self.opts['cleanup_days'], []
        for s, t in sorted(self.tasks.items(), key=lambda kv: kv[1].get('created') or 0):
            if t['state'] == 'done':
                left = max(0, days - int((now - (t.get('moved') or now)) // 86400))
                lines.append("done: <#%s> %s (deleted in %d days)" % (t['id'], t['name'], left))
            else:
                lines.append("open: <#%s> %s" % (t['id'], t['name']))
        return '\n'.join(lines)[:1800]

    # ---- jobs ----
    def cleanup(self, now=None):
        """Delete channels that have been done for more than cleanup_days. Only asks for channels in our own state (the transport refuses the rest)."""
        now = self.clock() if now is None else now
        limit, gone = self.opts['cleanup_days'] * 86400, []
        for s, t in list(self.tasks.items()):
            if t.get('state') != 'done' or not t.get('moved') or now - t['moved'] <= limit:
                continue
            res = self._do('delete %s (done for more than %d days)' % (t['key'], self.opts['cleanup_days']), lambda t=t: self.client.delete_channel(t['key']))
            if res is None:
                continue   # no answer: try again at the next cleanup
            if res.get('code') == 'dry':
                continue
            if res.get('ok') or res.get('code') == 'not_owner':   # not_owner = the transport no longer knows it: nothing left to delete
                self.log_deleted(s, t)
                self.tasks.pop(s, None)
                self.deleted += 1
                gone.append(s)
        self.last_cleanup = now
        self.save_state()
        return gone

    def sync(self):
        """Once after start: forget tasks whose channel the transport no longer lists (deleted by hand while this module was off)."""
        res = self.client.list_channels() if self.client else None
        if not res or not res.get('ok') or res.get('code') != 'ok':
            return False
        living = {c['channel_key'] for c in res.get('channels') or [] if not c.get('dead')}
        for s, t in list(self.tasks.items()):
            if t.get('key') not in living:
                self.tasks.pop(s, None)
                self.lg.info('task channel %s is no longer listed by the transport: forgotten', s)
        self.save_state()
        return True

    def tick(self):
        """About every 5 s: one-off sync after start, then the daily cleanup."""
        with self.lock:
            if self.client is None or not self.client.started:
                return
            now = self.clock()
            self.apply_mode()
            if not self.synced and now - self.started >= SYNC_AFTER_S and not self.dry_now():
                self.synced = self.sync()
            if now - self.started >= FIRST_CLEANUP_AFTER_S and now - self.last_cleanup >= CLEANUP_EVERY_S and not self.dry_now():
                self.cleanup(now)

    def healthy(self):
        return self.error is None

    def status(self):
        now = self.clock()
        hb = self.client.last_hello if self.client and self.client.last_hello else None
        return {'name': self.opts['module'], 'version': VERSION, 'shadow': bool(self.opts['shadow']), 'dry_now': self.dry_now(),
                'kill_switch': os.path.exists(os.path.join(self.data, STOP_FILE)), 'uptime_s': int(now - self.started),
                'error': self.error, 'healthy': self.healthy(), 'command_channel_key': self.opts['command_channel_key'],
                'cleanup_days': self.opts['cleanup_days'],
                'bus': {'connected': bool(self.bus and getattr(self.bus, 'connected', False)), 'active': bool(self.client and self.client.active),
                        'reason': self.bus_reason, 'last_heartbeat_s': None if hb is None else int(now - hb)},
                'counts': {'in': self.in_count, 'out': self.out_ops, 'out_failed': self.out_failed, 'created': self.created, 'finished': self.finished,
                           'deleted': self.deleted, 'rejected': sum((self.client.rejected if self.client else {}).values())},
                'tasks': {'open': len([1 for t in self.tasks.values() if t.get('state') == 'wip']), 'done': len([1 for t in self.tasks.values() if t.get('state') == 'done'])},
                'last_cleanup_s': None if not self.last_cleanup else int(now - self.last_cleanup),
                'last_in_s': None if self.last_in is None else int(now - self.last_in),
                'would_total': self.ring.would, 'last_would': list(self.ring.would_lines)[-10:]}


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
                                               'detail': '%s, bus %s, %d open, %d would-do' % ('shadow' if st['shadow'] else 'live', 'up' if st['bus']['connected'] else 'down',
                                                                                              st['tasks']['open'], st['would_total'])}]}]}).encode()
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
    lg = logging.getLogger('ihtaskchannels'); lg.setLevel(logging.INFO); lg.addHandler(ring)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    opts = load_options()
    mod = Module(opts, ring=ring)
    started = False
    try:
        started = mod.start()
    except Exception as e:
        mod.error = 'start failed (%s)' % type(e).__name__
    lg.info('ih_discord_taskchannels %s starting: %s, %s', VERSION, 'SHADOW (nothing is created)' if opts['shadow'] else 'LIVE-CAPABLE (the transport decides)',
            'bus started' if started else 'bus not started: %s' % (mod.bus_reason or mod.error))
    manifest = json.load(open(os.path.join(HERE, 'manifest.json')))

    def pusher():
        while True:
            push(mod, manifest, log=lg.info); time.sleep(PUSH_EVERY)

    def ticker():
        while True:
            try: mod.tick()
            except Exception as e: lg.info('tick failed (%s)', type(e).__name__)
            time.sleep(5)
    threading.Thread(target=pusher, daemon=True).start()
    threading.Thread(target=ticker, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(mod)).serve_forever()


if __name__ == '__main__':
    main()
