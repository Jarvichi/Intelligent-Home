#!/usr/bin/env python3
"""ih_discord_shopping 0.1.0: the SHOPPING list commands as a signed bus MODULE of the ih_discord transport (S3 of the split).
GENERIC: every detail (aisles, shop names, list entity, meal-plan script) comes from the add-on options (settings.py), none from code.
It never holds a Discord token or a channel number: it receives `in` events for channel KEYS over the MQTT bus and answers with `out` ops
(bus_client.ModuleClient). It reads and writes Home Assistant with its own SUPERVISOR_TOKEN.
Claims: the shopping channel key (catch-all: everything typed there is an item, `shop`, `got ...`, `help`) and, if option house_channel_key is set,
the SPECIFIC words `shop` / `got ...` in the house channel key (answered as plain text, like the house feature did).
Reactions (watch events): scope question (tag `scope`, 1 = full list, 2 = extras only, no answer in 180 s = full list), checklist (tag `checklist`,
a number ticks the item in HA and the line is edited to struck-through).
The text logic is the shared features/shopping.py (copied by sync_from_service.sh, never edited here); only the Discord part is done with bus ops.
SHADOW (option, default true): every op is sent with dry=true and logged '[DRY] would ...'; HA writes are logged only. /data/STOP_LIVE forces the same at once.
stdlib only (paho-mqtt for the real bus). Serves /healthz and /status (no secrets). Pushes its manifest and status to the orchestrator (POST /ingest)."""
import json, os, re, sys, threading, time, logging, collections, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import core
import settings as ST
import bus_client
from features import shopping as S

VERSION = '0.1.2'
DATA = os.environ.get('IHS_DATA', '/data')
OPTIONS = os.path.join(DATA, 'options.json')
PORT = int(os.environ.get('IHS_PORT', '8795'))
DEFAULT_INGEST_URL = 'http://172.30.32.1:8790'
PUSH_EVERY = 60
STOP_FILE = 'STOP_LIVE'
DEFAULT_MODULE = 'ih_discord_shopping'
SCOPE_TTL = S.ASK_WAIT          # 180 s
CHECKLIST_TTL = 3600            # the transport caps a watch at one hour: it is renewed while the list is young
CHECKLIST_KEEP_S = 12 * 3600
RENEW_AFTER_S = 3000
OPS_NEEDED = ['post', 'react', 'edit', 'delete', 'delete_tagged', 'watch']


def _text(v):
    return str(v or '').strip()


def settings_from_options(o):
    over = {'shopping_list_entity': _text(o.get('shopping_list_entity')) or 'todo.shopping_list',
            'meal_plan_script': _text(o.get('meal_plan_script')), 'shop_names': _text(o.get('shop_names')),
            'command_event': _text(o.get('command_event'))}
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
    ck = _text(o.get('channel_key')) or 'shopping'
    ck = ck if bus_client.KEY_RE.match(ck) else 'shopping'
    hk = _text(o.get('house_channel_key'))
    return {'module': name if bus_client.NAME_RE.match(name) else DEFAULT_MODULE,
            'channel_key': ck,
            'house_channel_key': hk if bus_client.KEY_RE.match(hk) and hk != ck else '',
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
    """Stands in for the Discord object the shared shopping feature expects. post() becomes a `post` op on the current channel key;
    the only req() it understands is DELETE of a message (the feature removes an unanswered scope question that way)."""
    def __init__(self, mod): self.mod = mod
    def me(self): return {'id': '__bus__'}
    def post(self, chan, text, **kw):
        return 200, self.mod.bus_post(self.mod.cur_ck, text, tag='reply')
    def req(self, method, path, body=None):
        m = re.fullmatch(r'/channels/[^/]+/messages/(\w+)', path)
        if method == 'DELETE' and m:
            self.mod.bus_delete(self.mod.opts['channel_key'], m.group(1))
            return 204, None
        return 405, None
    def find_channel(self, name): return self.mod.opts['channel_key']
    def get_bytes(self, url): return b''


class BusShopping(S.Feature):
    """The shared feature with its two Discord-heavy flows done as bus ops (the text rules are unchanged)."""
    def __init__(self, ctx, mod, root=''):
        super().__init__(ctx, root)
        self.mod = mod
        self.routed = True
        self.schan = mod.opts['channel_key']

    def ask_scope(self, user_id):
        m = self.mod
        text = (f"{S.ASK_MARK} **Which list?** <@{user_id}> tap one:\n1️⃣ Full list (meal plan + extras)\n2️⃣ Only the extras (not the meal plan)\n"
                "_No answer in 3 minutes: I post the full list._")
        res = m.bus_post(self.schan, text, tag='scope')
        mid = (res or {}).get('discord_id')
        if not (res and res.get('ok') and mid):
            return None
        for k in S.KEYS[:2]:
            m.bus_react(self.schan, mid, k)
        m.bus_watch(self.schan, mid, S.KEYS[:2], SCOPE_TTL, 'scope')
        return {'id': mid, 'user': str(user_id), 't': self.c.now()}

    def post_checklist(self, extras_only=False):
        m, ch = self.mod, self.schan
        m.bus_delete_tagged(ch, 'checklist')
        self.add_meal_plan()
        items = [lbl for lbl, _ in S.merge_batteries(self.items())]
        if extras_only:
            mn = S.meal_names(self.root, self.cfg['meal_items_file'])
            items = [i for i in items if not S.is_meal(i, mn)]
        if not items:
            return self.send("Nothing on the list apart from the meal plan." if extras_only else "The shopping list is empty.")
        order, rank = self.learned()
        for text in S.checklist_messages(items, order, rank, self.aisles):
            res = m.bus_post(ch, text, tag='checklist')
            mid = (res or {}).get('discord_id')
            if not (res and res.get('ok') and mid):
                continue
            n = text.count('\n')
            for k in S.KEYS[:n]:
                m.bus_react(ch, mid, k)
            m.bus_watch(ch, mid, S.KEYS[:n], CHECKLIST_TTL, 'checklist')
            m.lists[mid] = {'lines': text.split('\n'), 't': self.c.now(), 'renewed': self.c.now()}
        m.save_state()


class Module:
    def __init__(self, opts, bus=None, ha=None, ring=None, data=None, clock=time.time, threaded=True):
        self.opts, self.started, self.ring, self.data, self.clock = opts, clock(), ring or Ring(), data or DATA, clock
        self.lg = logging.getLogger('ihshopping')
        self.in_count = self.react_count = self.out_ops = self.out_failed = 0
        self.last_in = None
        self.error, self.client, self.feature, self.bus, self.bus_reason = None, None, None, bus, ''
        self.cur_allowed, self.cur_ck = set(), opts['channel_key']
        self.lists = {}          # checklist message id -> {lines, t, renewed}
        self.lock = threading.RLock()
        self.cfg = ST.merge(dict(opts.get('cfg') or {}, channel_shopping=opts['channel_key']))
        try:
            self.ha = ha or core.HA(dry=True)
        except Exception as e:
            self.ha, self.error = None, 'no Home Assistant access: ' + str(e)[:80]
        self.threaded = threaded

    # ---- mode ----
    def dry_now(self):
        return bool(self.opts['shadow']) or os.path.exists(os.path.join(self.data, STOP_FILE))

    def apply_mode(self):
        d = self.dry_now()
        self.hactx.dry = self.ha.dry = d
        if self.client:
            self.client.dry = d
        return d

    # ---- state (pending scope question, checklist messages): survives a restart ----
    def state_path(self): return os.path.join(self.data, 'state.json')

    def save_state(self):
        try:
            tmp = self.state_path() + '.tmp'
            with open(tmp, 'w') as fh:
                json.dump({'pend': self.feature.pend if self.feature else None, 'lists': self.lists}, fh)
            os.replace(tmp, self.state_path())
        except OSError:
            self.lg.info('state not saved')

    def load_state(self):
        try:
            d = json.load(open(self.state_path()))
        except (OSError, ValueError):
            return
        p = d.get('pend')
        self.feature.pend = p if isinstance(p, dict) and p.get('id') else None
        self.lists = d.get('lists') if isinstance(d.get('lists'), dict) else {}

    # ---- bus ops (each logs '[DRY] would ...' when dry, then sends; the transport also forces dry for a shadow grant) ----
    def _do(self, desc, fn):
        if self.dry_now():
            self.lg.info('[DRY] would %s', desc[:1500])
        res = fn()
        self.out_ops += 1
        if res is None:
            self.out_failed += 1
        return res

    def bus_post(self, ck, text, tag=None, reply_to=None):
        return self._do('post%s (%s): %s' % (' (reply to %s)' % reply_to if reply_to else '', tag or '-', text.replace('\n', ' | ')),
                        lambda: self.client.post(ck, text, reply_to=reply_to, tag=tag))

    def bus_react(self, ck, mid, emoji):
        return self._do('react %s on %s' % (emoji, mid), lambda: self.client.react(ck, mid, emoji))

    def bus_watch(self, ck, mid, emojis, ttl, tag):
        return self._do('watch %s for %ds (%s)' % (mid, ttl, tag), lambda: self.client.watch(ck, mid, emojis, ttl_s=ttl, tag=tag))

    def bus_edit(self, ck, mid, text):
        return self._do('edit %s: %s' % (mid, text.replace('\n', ' | ')), lambda: self.client.edit(ck, mid, text))

    def bus_delete(self, ck, mid):
        return self._do('delete %s' % mid, lambda: self.client.delete(ck, mid))

    def bus_delete_tagged(self, ck, tag):
        if tag == 'checklist':
            self.lists.clear()
        return self._do('delete messages tagged %s' % tag, lambda: self.client.delete_tagged(ck, tag))

    # ---- build / start ----
    def build(self):
        if self.ha is None:
            return False
        self.discord = BusDiscord(self)
        self.hactx = core.Ctx(self.discord, self.ha, True, self.cfg)
        self.hactx.allowed = lambda: set(self.cur_allowed)
        self.hactx.now = self.clock
        self.feature = BusShopping(self.hactx, self, os.path.join(self.data, 'root'))
        self.load_state()
        if self.bus is None:
            if not self.opts['mqtt_password']:
                self.bus_reason = 'no mqtt password'
                return False
            self.bus = bus_client.PahoBus(self.opts['mqtt_host'], self.opts['mqtt_port'], self.opts['mqtt_user'], self.opts['mqtt_password'],
                                          self.opts['module'] + '_mod', log=lambda m: self.lg.info('bus: ' + m))
        self.client = bus_client.ModuleClient(self.opts['module'], self.opts['signing_key'], self.bus, version=VERSION, log=lambda m: self.lg.info(m),
                                              clock=self.clock, threaded=self.threaded, log_payloads=self.opts.get('log_payloads', False), ops_needed=OPS_NEEDED, dry=True)
        self.client.on_message(self.opts['channel_key'], self.on_in, any=True)
        hk = self.opts['house_channel_key']
        if hk:   # specific claim: only `shop` and `got ...` (the house feature, if live, keeps everything else)
            self.client.on_message(hk, self.on_in_house, regex=self.house_regex())
        self.apply_mode()
        return True

    def house_regex(self):
        return '(?:' + self.feature.shop_re.pattern + ')|(?s:(?:got|bought|done)\\s+.+)'

    def start(self):
        if not self.build():
            return False
        ok = self.client.start()
        if not ok:
            self.bus_reason = 'no usable signing key' if not self.client.active else 'bus did not start'
        return bool(ok)

    # ---- events ----
    def on_in(self, d):
        """One verified `in` event on the shopping channel key (the ack was already sent): a message or a reaction."""
        with self.lock:
            self.apply_mode()
            if d.get('type') == 'reaction':
                return self.on_reaction(d)
            if d.get('type') != 'message':
                return
            self.in_count += 1
            self.last_in = self.clock()
            uid = str(d.get('author_id') or '')
            self.cur_allowed = {uid} if d.get('allowed') else set()
            self.cur_ck = self.opts['channel_key']
            self.feature.handle_message({'id': d.get('msg_id'), 'content': d.get('content') or '',
                                         'author': {'id': uid, 'username': d.get('author_name') or '', 'bot': False}})
            self.save_state()

    def on_in_house(self, d):
        """`shop` or `got ...` typed in the house channel key: a plain-text answer (same text as the house feature gave)."""
        with self.lock:
            self.apply_mode()
            if d.get('type') != 'message' or not d.get('allowed'):
                return
            self.in_count += 1
            self.last_in = self.clock()
            text = (d.get('content') or '').strip()
            if self.cfg.get('command_event'):
                self.ha.fire(self.cfg['command_event'], {'text': text, 'author': d.get('author_name') or ''})
            reply = self.house_reply(text)
            if reply:
                self.bus_post(self.opts['house_channel_key'], reply, tag='shopping', reply_to=d.get('msg_id'))

    def house_reply(self, text):
        f = self.feature
        tl = text.strip().lower().rstrip("?!. ")
        if f.shop_re.fullmatch(tl):
            f.add_meal_plan()
            items = [lbl for lbl, _ in S.merge_batteries(f.items())]
            if not items:
                return "The shopping list is empty."
            groups = {}
            for i in items:
                groups.setdefault(S.aisle(i, f.aisles), []).append(i)
            out = [f"**Shopping list** ({len(items)})"]
            order, rank = f.learned()
            for g in order:
                if g in groups:
                    out.append(f"__{g}__: " + ", ".join(S.sort_aisle(groups[g], rank)))
            return "\n".join(out)
        gm = S.GOT_RE.fullmatch(text.strip())
        if gm:
            items = f.items()
            done, unsure, missing = S.match_got(items, gm.group(2))
            for i in done:
                self.ha.call('todo', 'update_item', {'entity_id': f.list, 'item': i, 'status': 'completed'})
            return S.got_reply(items, done, unsure, missing)
        return None

    def on_reaction(self, d):
        if d.get('added') is not True:
            return
        self.react_count += 1
        f, mid, em, uid = self.feature, str(d.get('msg_id')), d.get('emoji'), str(d.get('user_id'))
        tag = d.get('tag') or ('scope' if f.pend and f.pend['id'] == mid else 'checklist' if mid in self.lists else '')
        if tag == 'scope':
            p = f.pend
            if p and p['id'] == mid and uid == p['user'] and em in S.KEYS[:2]:
                self.cur_ck = self.opts['channel_key']
                f.pend = None
                self.bus_delete(self.opts['channel_key'], mid)
                f.post_checklist(extras_only=(em == S.KEYS[1]))
        elif tag == 'checklist':
            self.tick_item(mid, em)
        self.save_state()

    def tick_item(self, mid, em):
        lst = self.lists.get(mid)
        if not lst or em not in S.KEYS:
            return
        n = S.KEYS.index(em) + 1
        lines = lst['lines']
        if n >= len(lines) or not lines[n].startswith(em + ' '):
            return   # no such line, or it is already struck through
        line = lines[n]
        item = line[len(em) + 1:].strip()
        names = [item]
        bm = S.BATT_LABEL_RE.match(item)
        if bm:
            names = [x for x in self.feature.items() if (S.BATT_RE.match(x) or [None, None, None])[2] == bm.group(1)]
        for nm in names:
            try:
                self.ha.call('todo', 'update_item', {'entity_id': self.feature.list, 'item': nm, 'status': 'completed'})
            except Exception:
                pass
        lines[n] = f"~~{line}~~ ✓"
        self.bus_edit(self.opts['channel_key'], mid, '\n'.join(lines))

    def tick(self):
        """About every 5 s: the 3-minute scope timeout (no answer = full list), renew checklist watches, drop old checklists."""
        with self.lock:
            f, now = self.feature, self.clock()
            if f is None:
                return
            self.apply_mode()
            p = f.pend
            if p and now - p['t'] > SCOPE_TTL:
                self.cur_ck = self.opts['channel_key']
                f.pend = None
                self.bus_delete(self.opts['channel_key'], p['id'])
                f.post_checklist(extras_only=False)
                self.save_state()
            for mid, l in list(self.lists.items()):
                if now - l['t'] > CHECKLIST_KEEP_S:
                    self.lists.pop(mid, None)
                elif now - l['renewed'] > RENEW_AFTER_S:
                    l['renewed'] = now
                    live = [k for i, k in enumerate(S.KEYS[:len(l['lines']) - 1]) if l['lines'][i + 1].startswith(k + ' ')]
                    if live:
                        self.bus_watch(self.opts['channel_key'], mid, live, CHECKLIST_TTL, 'checklist')

    def healthy(self):
        return self.error is None

    def status(self):
        now = self.clock()
        hb = self.client.last_hello if self.client and self.client.last_hello else None
        p = self.feature.pend if self.feature else None
        return {'name': self.opts['module'], 'version': VERSION, 'shadow': bool(self.opts['shadow']), 'dry_now': self.dry_now(),
                'kill_switch': os.path.exists(os.path.join(self.data, STOP_FILE)), 'uptime_s': int(now - self.started),
                'error': self.error, 'healthy': self.healthy(), 'channel_key': self.opts['channel_key'], 'house_channel_key': self.opts['house_channel_key'],
                'bus': {'connected': bool(self.bus and getattr(self.bus, 'connected', False)), 'active': bool(self.client and self.client.active),
                        'reason': self.bus_reason, 'last_heartbeat_s': None if hb is None else int(now - hb)},
                'counts': {'in': self.in_count, 'reactions': self.react_count, 'out': self.out_ops, 'out_failed': self.out_failed,
                           'rejected': sum((self.client.rejected if self.client else {}).values()), 'rejected_by': dict(self.client.rejected) if self.client else {}},
                'scope_question_open': bool(p), 'checklists_open': len(self.lists),
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
                                               'detail': '%s, bus %s, %d in, %d would-do' % ('shadow' if st['shadow'] else 'live', 'up' if st['bus']['connected'] else 'down',
                                                                                            st['counts']['in'], st['would_total'])}]}]}).encode()
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
    lg = logging.getLogger('ihshopping'); lg.setLevel(logging.INFO); lg.addHandler(ring)
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
    lg.info('ih_discord_shopping %s starting: %s, %s', VERSION, 'SHADOW (nothing is posted)' if opts['shadow'] else 'LIVE-CAPABLE (the transport decides)',
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
