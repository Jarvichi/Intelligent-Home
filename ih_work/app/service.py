"""The work service: queue store + pace gate + persisted state. Used by the API (main.py) and the ticker.
The gate override is a Python argument of claim() only. It is not read from any request, so no consumer can bypass the gate."""
import json, os, threading
from datetime import datetime, timedelta, timezone

import feed
import gate as G
from store import ACTIVE, OPEN, STATUSES, CONSUMER_RE, Store, StoreError, iso, parse, utcnow

VERSION = '0.1.0'
READING_TTL_S = 30
FEED_CHECK_S = 300
FED_KEEP_DAYS = 30


def brief(i):
    return i and {k: i.get(k) for k in ('id', 'seq', 'kind', 'title', 'approved', 'queued_at', 'started_at', 'finished_at', 'status', 'claimed_by')}


class Service:
    def __init__(self, data_dir, opts, reader=None, ha=None, clock=utcnow, log=lambda *a: None):
        self.dir = data_dir
        self.opts = opts          # mode, lease_s, stop, bands, tz (tzinfo), feed_backlog, feed_prefixes, todo_entity
        self.reader, self.ha, self.clock, self.log = reader, ha, clock, log
        self.store = Store(data_dir, clock)
        self.state_path = os.path.join(data_dir, 'state.json')
        self.lock = threading.RLock()
        self._reading = (0.0, None, '')

    # ---- state ----
    def load_state(self):
        try:
            with open(self.state_path) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, st):
        Store.write_json(self.state_path, st)

    def stopped(self):
        return bool(self.opts.get('stop')) or os.path.exists(os.path.join(self.dir, 'STOP'))

    def local(self, now=None):
        return (now or self.clock()).astimezone(self.opts.get('tz') or timezone.utc)

    def reading(self, now=None):
        """-> (reading dict or None, problem text). Cached for READING_TTL_S."""
        now = now or self.clock()
        t, val, err = self._reading
        if now.timestamp() - t < READING_TTL_S and (val or err):
            return val, err
        try:
            val, err = self.reader.read(now), ''
        except Exception as e:   # the reason shown is the error type only: nothing from the response is passed on
            val, err = None, 'usage sensors could not be read (%s)' % type(e).__name__
        self._reading = (now.timestamp(), val, err)
        return val, err

    def _recent_claims(self, st, now):
        key = 'dry_claims' if self.opts.get('mode') != 'live' else 'claims'
        cut = now - timedelta(hours=1)
        keep = [t for t in st.get(key, []) if (parse(t) or cut) > cut]
        return key, keep

    def decide(self, st, now):
        r, err = self.reading(now)
        _, recent = self._recent_claims(st, now)
        d = G.decide(st.get('mode', 'idle'), st.get('band'), r and r['pace'], r and r['session'], r and r['week'], len(recent),
                     self.stopped(), self.local(now), self.opts['bands'])
        if r is None and not self.stopped():
            d = d._replace(reason='%s band: %s, nothing runs' % (d.band, err or 'pace cannot be read'))
        return d, r

    def gate_line(self, d, r):
        return 'queue gate: %s, %s; state %s; session %s%%, week %s%%' % ('ok' if d.ok else 'STOP', d.reason, d.mode,
                                                                           r and r['session'], r and r['week'])

    def worker_view(self, items, d, r):
        active = next((i for i in items if i['status'] in ACTIVE), None)
        queued = [i for i in items if i['status'] == 'queued']
        if self.stopped():
            state, reason = 'stopped', d.reason
        elif active:
            state, reason = 'working', 'an item is running'
        elif not queued:
            state, reason = 'idle', 'queue empty'
        elif not d.ok:
            state, reason = 'waiting for pace', d.reason
        else:
            state, reason = ('ready' if self.opts.get('mode') == 'live' else 'ready (test mode)'), d.reason
        return dict(state=state, reason=reason, mode=d.mode, band=d.band, start=d.start, stop=d.stop)

    # ---- gate-guarded claim ----
    def claim(self, consumer, lease_s=None, override=False):
        if not isinstance(consumer, str) or not CONSUMER_RE.match(consumer):
            raise StoreError('bad', 'consumer must be 1-40 letters, digits, . _ -')
        lease = self.opts['lease_s'] if lease_s is None else lease_s
        with self.lock:
            now = self.clock()
            self.store.reap()
            st = self.load_state()
            items = self.store.list()
            if self.stopped():
                return dict(claimed=False, reason='STOP switch is on: nothing runs')
            held = next((i for i in items if i['status'] in ACTIVE), None)
            if held:
                return dict(claimed=False, reason='an item is already running')
            if not any(i['status'] == 'queued' for i in items):
                return dict(claimed=False, reason='queue empty')
            d, r = self.decide(st, now)
            if d.mode != st.get('mode') or d.band != st.get('band'):
                st.update(mode=d.mode, band=d.band, mode_since=iso(now))
            if override:
                d = d._replace(ok=True, reason='override: ' + d.reason)
            if not d.ok:
                self.save_state(st)
                return dict(claimed=False, reason=d.reason, gate=self.gate_line(d, r))
            key, recent = self._recent_claims(st, now)
            if self.opts.get('mode') != 'live':
                head = next(i for i in items if i['status'] == 'queued')
                st[key] = recent + [iso(now)]
                self.save_state(st)
                self.log('[DRY] would hand out item %s: %s' % (head['id'], head['title']))
                return dict(claimed=False, dry=True, would_claim=brief(head), reason='test mode: would hand out item %s' % head['id'], gate=self.gate_line(d, r))
            it = self.store.claim(consumer, lease)
            st[key] = recent + [iso(now)]
            self.save_state(st)
            return dict(claimed=True, item=it, lease_expires_at=it['lease_expires_at'], reason=d.reason, gate=self.gate_line(d, r))

    # ---- views ----
    def status(self):
        with self.lock:
            now = self.clock()
            self.store.reap()
            items = self.store.list()
            st = self.load_state()
            d, r = self.decide(st, now)
            counts = {s: 0 for s in ('queued', 'running', 'done', 'failed', 'cancelled')}
            for i in items:
                counts['running' if i['status'] in ACTIVE else i['status']] += 1
            fin = [i for i in items if i.get('finished_at') and i['status'] in ('done', 'failed')]
            w = self.worker_view(items, d, r)
            return dict(version=VERSION, run_mode=self.opts.get('mode'), counts=counts,
                        next=brief(next((i for i in items if i['status'] == 'queued'), None)),
                        running=brief(next((i for i in items if i['status'] in ACTIVE), None)),
                        last_finished=brief(max(fin, key=lambda i: i['finished_at'])) if fin else None,
                        worker=dict(state=w['state'], reason=w['reason'], mode=w['mode'], band=w['band'], updated=iso(now)),
                        gate=self.gate_line(d, r), gate_at=iso(now), pace=r and r['pace'], week=r and r['week'], session=r and r['session'],
                        corrupt_files=list(self.store.corrupt))

    # ---- ticker ----
    def tick(self):
        """Every ~30 s: free expired leases, keep the band and mode up to date, write the snapshot the page reads, maybe feed the backlog."""
        with self.lock:
            now = self.clock()
            for i in self.store.reap():
                self.log('lease expired for item %s: now %s' % (i['id'], i['status']))
            items = self.store.list()
            st = self.load_state()
            d, r = self.decide(st, now)
            if not self.stopped() and r is not None and (d.mode != st.get('mode') or d.band != st.get('band')):
                st.update(mode=d.mode, band=d.band, mode_since=iso(now))
            w = self.worker_view(items, d, r)
            st['snapshot'] = dict(worker=dict(w, updated=iso(now)), gate=self.gate_line(d, r), pace=r and r['pace'], at=iso(now))
            self.save_state(st)
            try:
                self.feed_once(st, items, d, now)
            except Exception as e:
                self.log('feed failed (%s)' % type(e).__name__)

    # ---- backlog feeder (default off) ----
    def feed_once(self, st, items, d, now):
        if not self.opts.get('feed_backlog') or not self.ha or self.stopped():
            return None
        if any(i['status'] in OPEN for i in items) or not d.ok:
            return None
        t = parse(st.get('last_feed_check'))
        if t and (now - t).total_seconds() < FEED_CHECK_S:
            return None
        st['last_feed_check'] = iso(now)
        ent = self.opts['todo_entity']
        resp = self.ha.post('services/todo/get_items?return_response', {'entity_id': ent, 'status': 'needs_action'}, 20)
        todo = ((resp or {}).get('service_response') or {}).get(ent, {}).get('items') or []
        live = self.opts.get('mode') == 'live'
        fed = {k: v for k, v in (st.get('fed' if live else 'fed_dry') or {}).items() if (parse(v) or now) > now - timedelta(days=FED_KEEP_DAYS)}
        item, approved = feed.pick(todo, self.opts.get('feed_prefixes') or [], fed, now)
        if item:
            fed[item['uid']] = iso(now)
            if live:
                self.store.enqueue('todo', item['uid'], str(item['summary'])[:200], feed.instruction(item), approved, 'claude')
                self.log('fed to-do item: %s (%s)' % (str(item['summary'])[:80], 'approved' if approved else 'read-only'))
            else:
                self.log('[DRY] would feed: %s (%s)' % (str(item['summary'])[:80], 'approved' if approved else 'read-only'))
        st['fed' if live else 'fed_dry'] = fed
        self.save_state(st)
        return item
