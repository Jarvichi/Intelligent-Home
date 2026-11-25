#!/usr/bin/env python3
"""ih_orchestration_inbox 0.3.0: a durable INBOX for chosen Discord channel keys, as a signed bus MODULE of the ih_discord transport.
GENERIC: the channel keys, the emoji, the retention and the token all come from the add-on options, none from code.
It claims (catch-all) every channel key in the option `channel_keys`, stores each delivered message in its own SQLite database /data/ih.db (inboxdb.py; before 0.3.0: JSONL segments; retention_days, default 7) and puts one emoji (default eyes) on the message so the sender knows it arrived. It NEVER answers in the channel.
Read API on its own port for Claude and workers (bearer `api_token`; empty token = the API refuses everything):
  GET  /queue?since=<seq>&channel=<key>[&consumer=<name>][&limit=N][&all=1]   records with seq > since (since omitted = the consumer's cursor)
  POST /ack {"seq": N, "consumer": "name"}                                     move that consumer's cursor (never backwards)
  GET  /status, GET /healthz                                                   no secrets, no token needed
Since 0.2.0 the same queue also takes EVENTS from local services (a hand-over, a reaction, a direct message), so one reader gets everything in one place:
  POST /events {"kind": "handoff", "source": "name", "ref": "unique id", "text": "..."}   store one event (same ref twice = stored once)
  GET  /digest?consumer=<name>[&wait=<seconds>]                                 how many new records per kind, WITHOUT any text
  GET  /queue ... [&kind=<kind>][&wait=<seconds>]                               `wait` holds the request open until a record arrives (long poll)
`wait` lets a small doorbell process learn about new records at once, without polling and without waking anything in between.
Only messages from users on the transport's allow-list are returned (and reacted to) unless all=1; every message is stored with its `allowed` flag.
SHADOW (option, default true): the queue IS written, but reactions are sent with dry=true and logged '[DRY] would react'. /data/STOP_LIVE does the same at once.
Message text is stored in the queue (up to 2000 chars) and is never written to the log or /status.
V1 = transport channels only: DMs and private channels are not on the bus. No Home Assistant access. stdlib only (paho-mqtt for the real bus)."""
import hmac, json, os, re, sys, threading, time, logging, collections, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ihlib import trace
import bus_client, inboxdb
import filequeue
from filequeue import CONSUMER_RE

VERSION = '0.3.0'
DATA = os.environ.get('IHI_DATA', '/data')
OPTIONS = os.path.join(DATA, 'options.json')
PORT = int(os.environ.get('IHI_PORT', '8800'))
DEFAULT_INGEST_URL = 'http://172.30.32.1:8790'
PUSH_EVERY = 60
STOP_FILE = 'STOP_LIVE'
DEFAULT_MODULE = 'ih_orchestration_inbox'
DEFAULT_EMOJI = '\U0001F440'
OPS_NEEDED = ['react']
MAX_TEXT = 2000
MAX_LIMIT = 200
RETENTION_EVERY_S = 3600
KIND_MESSAGE = filequeue.KIND_MESSAGE              # a record without `kind` is a Discord message from the bus
KIND_RE = re.compile(r'^[a-z][a-z0-9_]{0,23}$')
SOURCE_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,40}$')
REF_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,80}$')
EVENT_BODY_MAX = 8192                              # bytes read from one POST /events
MAX_WAIT_S = 25                                    # longest long poll; below the usual 30 s client and proxy timeouts
EVENT_ID_PREFIX = 'ev:'                            # keeps event refs apart from Discord message ids in the duplicate check


kind_of = filequeue.kind_of


def _text(v):
    return str(v or '').strip()


def load_options(path=None):
    try:
        with open(path or OPTIONS) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}
    name = _text(o.get('module_name')) or DEFAULT_MODULE
    try:
        days = max(1, min(30, int(o.get('retention_days') or 7)))
    except (TypeError, ValueError):
        days = 7
    keys = []
    raw = o.get('channel_keys')
    for k in (raw if isinstance(raw, list) else []):
        k = _text(k) if isinstance(k, str) else ''
        if bus_client.KEY_RE.match(k) and k not in keys:
            keys.append(k)
    return {'module': name if bus_client.NAME_RE.match(name) else DEFAULT_MODULE,
            'signing_key': _text(o.get('signing_key')), 'log_payloads': o.get('log_payloads') is True, 'shadow': o.get('shadow') is not False,   # anything but an explicit false = shadow
            'channel_keys': keys, 'ack_emoji': _text(o.get('ack_emoji'))[:16] or DEFAULT_EMOJI, 'retention_days': days,
            'api_token': _text(o.get('api_token')),
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
        self.lg = logging.getLogger('ihorchinbox')
        self.threaded = threaded
        self.tracer = trace.from_options('ih_orchestration_inbox', opts)   # metadata-only spans; off without an ingest token
        self.lock = threading.RLock()
        self.error, self.client, self.bus, self.bus_reason = None, None, bus, ''
        self.in_count = self.queued = self.dup = self.skipped = self.reacts = self.react_failed = 0
        self.events = self.event_dup = 0
        self.last_in = None
        self.last_retention = 0.0
        self.queue = inboxdb.InboxStore(self.data, opts['retention_days'], clock, log=lambda m: self.lg.info(m)).open()   # database, or degraded files; never raises

    # ---- mode ----
    def dry_now(self):
        return bool(self.opts['shadow']) or os.path.exists(os.path.join(self.data, STOP_FILE))

    def apply_mode(self):
        d = self.dry_now()
        if self.client:
            self.client.dry = d
        return d

    # ---- build / start ----
    def build(self):
        if self.bus is None:
            if not self.opts['mqtt_password']:
                self.bus_reason = 'no mqtt password'
                return False
            self.bus = bus_client.PahoBus(self.opts['mqtt_host'], self.opts['mqtt_port'], self.opts['mqtt_user'], self.opts['mqtt_password'],
                                          self.opts['module'] + '_mod', log=lambda m: self.lg.info('bus: ' + m))
        self.client = bus_client.ModuleClient(self.opts['module'], self.opts['signing_key'], self.bus, version=VERSION, log=lambda m: self.lg.info(m),
                                              clock=self.clock, threaded=self.threaded, log_payloads=self.opts.get('log_payloads', False), ops_needed=OPS_NEEDED, dry=True)
        for ck in self.opts['channel_keys']:
            self.client.on_message(ck, self.on_in, any=True)
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
    @trace.traced('message_in')
    def on_in(self, d):
        """One verified `in` event on a claimed channel key (the ack was already sent). Stores it, then reacts. Never posts."""
        with self.lock:
            self.apply_mode()
            if d.get('type') != 'message':
                return
            self.in_count += 1
            self.last_in = self.clock()
            att = d.get('attachments') if isinstance(d.get('attachments'), dict) else {}
            rec = {'msg_id': str(d.get('msg_id') or ''), 'channel_key': str(d.get('channel_key') or ''), 'author_id': str(d.get('author_id') or ''),
                   'allowed': bool(d.get('allowed')), 'ts': d.get('msg_ts') or int(self.clock()), 'received': int(self.clock()),
                   'text': str(d.get('content') or '')[:MAX_TEXT], 'reply_to': d.get('reply_to'),
                   'attachments': {'count': int(att.get('count') or 0), 'kinds': [str(k)[:20] for k in (att.get('kinds') or [])][:10]}}
            if not rec['msg_id'] or not rec['channel_key']:
                self.skipped += 1
                return
            try:
                stored = self.queue.add(rec)
            except OSError:
                self.error = 'queue write failed'
                self.lg.info('queue write failed')
                return
            if stored is None:
                self.dup += 1
                return
            self.error = None
            self.queued += 1
            if not rec['allowed']:
                return   # stored for the record, but we do not acknowledge strangers
            self.lg.info('queued seq %d on %s', stored['seq'], rec['channel_key'])   # no text, no author
            emoji = self.opts['ack_emoji']
            if self.dry_now():
                self.lg.info('[DRY] would react %s on %s message %s', emoji, rec['channel_key'], rec['msg_id'])
            res = self.client.react(rec['channel_key'], rec['msg_id'], emoji)
            self.reacts += 1
            if res is None or not res.get('ok'):
                self.react_failed += 1

    def add_event(self, kind, source, ref='', text=''):
        """One event from a local service (already validated by the API). Stored like a message; no reaction, nothing posted.
        -> the stored record, or None when an event with this kind and ref is already in the queue."""
        with self.lock:
            now = int(self.clock())
            rec = {'kind': kind, 'source': source, 'ref': ref, 'msg_id': EVENT_ID_PREFIX + kind + ':' + ref if ref else '', 'channel_key': '',
                   'author_id': '', 'allowed': True, 'ts': now, 'received': now, 'text': str(text or '')[:MAX_TEXT]}
            try:
                stored = self.queue.add(rec)
            except OSError:
                self.error = 'queue write failed'
                self.lg.info('queue write failed')
                raise
            if stored is None:
                self.event_dup += 1
                return None
            self.error = None
            self.events += 1
            self.lg.info('queued seq %d, event %s from %s', stored['seq'], kind, source)   # no text
            return stored

    def digest(self, consumer):
        """What is new for this consumer, per kind, WITHOUT any text (safe to show or to type into a terminal)."""
        with self.lock:
            cursor = self.queue.cursor(consumer)
            recs = self.queue.read(cursor, None, MAX_LIMIT)
            kinds = {}
            for r in recs:
                kinds[kind_of(r)] = kinds.get(kind_of(r), 0) + 1
            return {'consumer': consumer, 'cursor': cursor, 'new': len(recs), 'kinds': kinds, 'newest_seq': self.queue.seq}

    # ---- jobs ----
    def tick(self):
        """About every 5 s: hourly retention."""
        with self.lock:
            now = self.clock()
            self.apply_mode()
            if now - self.last_retention >= RETENTION_EVERY_S:
                self.queue.retain()
                self.last_retention = now

    def healthy(self):
        return self.error is None

    def status(self):
        now = self.clock()
        hb = self.client.last_hello if self.client and self.client.last_hello else None
        return {'name': self.opts['module'], 'version': VERSION, 'shadow': bool(self.opts['shadow']), 'dry_now': self.dry_now(),
                'kill_switch': os.path.exists(os.path.join(self.data, STOP_FILE)), 'uptime_s': int(now - self.started),
                'error': self.error, 'healthy': self.healthy(), 'channel_keys': list(self.opts['channel_keys']),
                'retention_days': self.opts['retention_days'], 'api_enabled': bool(self.opts['api_token']),
                'bus': {'connected': bool(self.bus and getattr(self.bus, 'connected', False)), 'active': bool(self.client and self.client.active),
                        'reason': self.bus_reason, 'last_heartbeat_s': None if hb is None else int(now - hb)},
                'counts': {'in': self.in_count, 'queued': self.queued, 'duplicates': self.dup, 'skipped': self.skipped, 'reactions': self.reacts,
                           'reactions_failed': self.react_failed, 'events': self.events, 'event_duplicates': self.event_dup, 'rejected': sum((self.client.rejected if self.client else {}).values())},
                'queue': self.queue.stats(), 'db': self.queue.health(), 'db_degraded': self.queue.degraded is not None,
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
                                               'detail': '%s, bus %s, %d queued, %d would-do' % ('shadow' if st['shadow'] else 'live', 'up' if st['bus']['connected'] else 'down',
                                                                                                st['counts']['queued'], st['would_total'])}]}]}).encode()
    try:
        r = urllib.request.Request(o['ingest_url'] + '/ingest', data=body, method='POST',
                                   headers={'Authorization': 'Bearer ' + o['ingest_token'], 'Content-Type': 'application/json'})
        with (opener or urllib.request.urlopen)(r, timeout=5) as x:
            return x.status == 200
    except Exception as e:
        log('ih push: failed (%s)' % type(e).__name__)
        return False


def make_handler(mod):
    def authed(h):
        tok = mod.opts['api_token']
        if not tok:
            return False   # no token configured: the API refuses everything
        given = h.headers.get('Authorization', '')
        return given.startswith('Bearer ') and hmac.compare_digest(given[7:].encode(), tok.encode())

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct='application/json'):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path); p = u.path
            if p == '/healthz':
                return self._send(200 if mod.healthy() else 503, b'ok' if mod.healthy() else b'not ok', 'text/plain')
            if p == '/status':
                return self._send(200, mod.status())
            if p in ('/queue', '/digest'):
                if not authed(self):
                    return self._send(401, {'error': 'unauthorized'})
                q = parse_qs(u.query)
                one = lambda k: (q.get(k) or [''])[0]
                consumer = one('consumer') or 'default'
                if not CONSUMER_RE.match(consumer):
                    return self._send(400, {'error': 'bad consumer'})
                try:
                    since = int(one('since')) if one('since') != '' else mod.queue.cursor(consumer)
                    limit = max(1, min(MAX_LIMIT, int(one('limit') or 100)))
                    wait = max(0.0, min(MAX_WAIT_S, float(one('wait') or 0)))
                except ValueError:
                    return self._send(400, {'error': 'bad number'})
                ch, kind = one('channel'), one('kind')
                if ch and not bus_client.KEY_RE.match(ch):
                    return self._send(400, {'error': 'bad channel'})
                if kind and not KIND_RE.match(kind):
                    return self._send(400, {'error': 'bad kind'})
                if p == '/digest':
                    answer = lambda: mod.digest(consumer)
                    empty = lambda a: not a['new']
                else:
                    answer = lambda: {'since': since, 'consumer': consumer, 'newest_seq': mod.queue.seq,
                                      'messages': mod.queue.read(since, ch or None, limit, allowed_only=one('all') != '1', kind=kind or None)}
                    empty = lambda a: not a['messages']
                mark = mod.queue.seq   # taken BEFORE the read: a record stored in between ends the wait at once
                a = answer()
                if wait and empty(a) and mod.queue.wait_beyond(mark, wait):
                    a = answer()
                return self._send(200, a)
            self.send_error(404)

        def _event(self):
            try:
                n = int(self.headers.get('Content-Length') or 0)
                if n > EVENT_BODY_MAX:
                    return self._send(413, {'error': 'too large'})
                d = json.loads(self.rfile.read(n) or b'{}')
                kind, source, ref, text = d['kind'], d['source'], d.get('ref') or '', d.get('text') or ''
            except (ValueError, KeyError, TypeError):
                return self._send(400, {'error': 'need {"kind": "...", "source": "..."}'})
            if not (isinstance(kind, str) and KIND_RE.match(kind)) or kind == KIND_MESSAGE:
                return self._send(400, {'error': 'bad kind'})
            if not (isinstance(source, str) and SOURCE_RE.match(source)):
                return self._send(400, {'error': 'bad source'})
            if not isinstance(ref, str) or (ref and not REF_RE.match(ref)) or not isinstance(text, str):
                return self._send(400, {'error': 'bad ref or text'})
            try:
                rec = mod.add_event(kind, source, ref, text)
            except OSError:
                return self._send(500, {'error': 'queue write failed'})
            return self._send(200, {'stored': rec is not None, 'seq': rec['seq'] if rec else None})

        def do_POST(self):
            p = urlparse(self.path).path
            if p not in ('/ack', '/events'):
                return self.send_error(404)
            if not authed(self):
                return self._send(401, {'error': 'unauthorized'})
            if p == '/events':
                return self._event()
            try:
                n = int(self.headers.get('Content-Length') or 0)
                d = json.loads(self.rfile.read(min(n, 4096)) or b'{}')
                seq = int(d['seq']); consumer = str(d.get('consumer') or 'default')
            except (ValueError, KeyError, TypeError):
                return self._send(400, {'error': 'need {"seq": N}'})
            if not CONSUMER_RE.match(consumer) or seq < 0:
                return self._send(400, {'error': 'bad request'})
            return self._send(200, {'consumer': consumer, 'cursor': mod.queue.ack(consumer, seq)})

        def log_message(self, *a):
            pass
    return H


def main():
    ring = Ring()
    lg = logging.getLogger('ihorchinbox'); lg.setLevel(logging.INFO); lg.addHandler(ring)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    opts = load_options()
    mod = Module(opts, ring=ring)
    started = False
    try:
        started = mod.start()
    except Exception as e:
        mod.error = 'start failed (%s)' % type(e).__name__
    lg.info('ih_orchestration_inbox %s starting: %s, %d channel key(s), API %s, %s', VERSION, 'SHADOW (queue written, no reactions)' if opts['shadow'] else 'LIVE-CAPABLE (the transport decides)',
            len(opts['channel_keys']), 'on' if opts['api_token'] else 'OFF (no api_token)', 'bus started' if started else 'bus not started: %s' % (mod.bus_reason or mod.error))
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
    mod.queue.start_maintenance()   # nightly snapshot + restore check, daily trash purge
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(mod)).serve_forever()


if __name__ == '__main__':
    main()
