"""Work queue store: one JSON file per item under <dir>/items, FIFO by a monotonic seq, with leases.

Item statuses: queued, claimed (handed to a consumer, lease running), running (consumer sent a heartbeat), done, failed, cancelled.
A consumer holds an item only while its lease is valid. `heartbeat` renews it. A lease that runs out puts the item back in the queue
(attempts + 1, same place in line); after MAX_ATTEMPTS expired leases the item is failed. `complete` and `fail` need the consumer that holds the lease.
All changes happen under one file lock; every file is written to a temp name and renamed. Same kind + ref while open (queued, claimed, running) returns the existing item."""
import fcntl, glob, json, os, re, tempfile, threading, uuid
from datetime import datetime, timedelta, timezone

KINDS = ('approved_request', 'ai_flag', 'todo', 'review', 'other', 'plan_todos', 'review_batch')
STATUSES = ('queued', 'claimed', 'running', 'done', 'failed', 'cancelled')
OPEN = ('queued', 'claimed', 'running')
ACTIVE = ('claimed', 'running')
MAX_ATTEMPTS = 3
CONSUMER_RE = re.compile(r'^[A-Za-z0-9_.-]{1,40}$')
LIMITS = {'ref': 200, 'title': 200, 'instruction': 4000, 'summary': 2000, 'error': 500}


class StoreError(Exception):
    """code: bad (400), forbidden (403), missing (404), conflict (409)."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds')


def parse(text):
    try:
        d = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def clean(value, field):
    if not isinstance(value, str):
        raise StoreError('bad', '%s must be text' % field)
    value = value.strip()
    if len(value) > LIMITS[field]:
        raise StoreError('bad', '%s is longer than %d characters' % (field, LIMITS[field]))
    return value


class Store:
    def __init__(self, directory, clock=utcnow):
        self.dir = directory
        self.items = os.path.join(directory, 'items')
        self.counter = os.path.join(directory, 'seq.counter')
        self.lockfile = os.path.join(directory, '.lock')
        self.clock = clock
        self.corrupt = []
        self._tl = threading.RLock()
        os.makedirs(self.items, exist_ok=True)

    # ---- plumbing ----
    class _Lock:
        def __init__(self, store):
            self.s = store

        def __enter__(self):
            self.s._tl.acquire()
            self.f = open(self.s.lockfile, 'a+')
            fcntl.flock(self.f, fcntl.LOCK_EX)
            return self

        def __exit__(self, *a):
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()
            self.s._tl.release()

    def lock(self):
        return Store._Lock(self)

    @staticmethod
    def write_json(path, obj):
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.tmp-')
        with os.fdopen(fd, 'w') as f:
            json.dump(obj, f, indent=1)
        os.replace(tmp, path)

    def _path(self, it):
        return os.path.join(self.items, '%06d-%s.json' % (it['seq'], it['id']))

    def _load_all(self):
        out, bad = [], []
        for p in sorted(glob.glob(os.path.join(self.items, '*.json'))):
            try:
                with open(p) as fh:
                    it = json.load(fh)
                if not isinstance(it, dict) or not isinstance(it.get('id'), str) or not isinstance(it.get('seq'), int) or it.get('status') not in STATUSES:
                    raise ValueError('bad shape')
                out.append(it)
            except (OSError, ValueError):
                bad.append(os.path.basename(p))
        self.corrupt = bad
        return sorted(out, key=lambda i: i['seq'])

    def _next_seq(self):
        try:
            with open(self.counter) as f:
                n = int(f.read().strip())
        except (OSError, ValueError):
            n = max([i['seq'] for i in self._load_all()] + [0])
        n += 1
        with open(self.counter, 'w') as f:
            f.write(str(n))
        return n

    def _save(self, it):
        self.write_json(self._path(it), it)

    def _reap(self, items, now):
        """Expired leases -> queued (attempts + 1) or failed. Saves and returns the changed items."""
        changed = []
        for i in items:
            if i['status'] in ACTIVE:
                exp = parse(i.get('lease_expires_at'))
                if exp is None or exp <= now:
                    i['attempts'] = int(i.get('attempts') or 0) + 1
                    holder = i.get('claimed_by')
                    i.update(claimed_by=None, lease_expires_at=None, heartbeat_at=None, started_at=None)
                    if i['attempts'] >= MAX_ATTEMPTS:
                        i.update(status='failed', finished_at=iso(now), error='lease expired %d times (last holder %s)' % (i['attempts'], holder))
                    else:
                        i['status'] = 'queued'
                    self._save(i)
                    changed.append(i)
        return changed

    def _fresh(self):
        now = self.clock()
        items = self._load_all()
        self._reap(items, now)
        return items, now

    # ---- reads ----
    def list(self, statuses=None):
        with self.lock():
            items, _ = self._fresh()
        return [i for i in items if not statuses or i['status'] in statuses]

    def get(self, iid):
        for i in self.list():
            if i['id'] == iid:
                return i
        return None

    def peek(self):
        """Read-only listing for a second process (no reaping, no lock needed for correctness)."""
        return self._load_all()

    # ---- writes ----
    def enqueue(self, kind, ref, title, instruction, approved=False, by='claude'):
        if kind not in KINDS:
            raise StoreError('bad', 'kind must be one of ' + ', '.join(KINDS))
        if not isinstance(by, str) or not CONSUMER_RE.match(by):
            raise StoreError('bad', 'by must be 1-40 letters, digits, . _ -')
        if not isinstance(approved, bool):
            raise StoreError('bad', 'approved must be true or false')
        ref, title, instruction = clean(str(ref) if isinstance(ref, int) else ref, 'ref'), clean(title, 'title'), clean(instruction, 'instruction')
        if not ref or not title or not instruction:
            raise StoreError('bad', 'ref, title and instruction are needed')
        with self.lock():
            items, now = self._fresh()
            for i in items:
                if i['kind'] == kind and i['ref'] == ref and i['status'] in OPEN:
                    return i
            it = dict(id=uuid.uuid4().hex[:8], seq=self._next_seq(), kind=kind, ref=ref, title=title, instruction=instruction, approved=approved,
                      status='queued', queued_at=iso(now), claimed_by=None, lease_expires_at=None, heartbeat_at=None, started_at=None,
                      finished_at=None, attempts=0, result_summary=None, error=None, by=by)
            self._save(it)
            return it

    def _find(self, items, iid):
        for i in items:
            if i['id'] == iid:
                return i
        raise StoreError('missing', 'no such item')

    def claim(self, consumer, lease_s, iid=None):
        """Give the lowest-seq queued item (or the queued item `iid`) to `consumer` with a lease. Returns the item, or None when the queue is empty.
        Raises conflict when another item is still held (one at a time)."""
        if not isinstance(consumer, str) or not CONSUMER_RE.match(consumer):
            raise StoreError('bad', 'consumer must be 1-40 letters, digits, . _ -')
        with self.lock():
            items, now = self._fresh()
            held = next((i for i in items if i['status'] in ACTIVE), None)
            if held:
                raise StoreError('conflict', 'an item is already held')
            head = next((i for i in items if i['status'] == 'queued' and (iid is None or i['id'] == iid)), None)
            if not head:
                return None
            head.update(status='claimed', claimed_by=consumer, started_at=iso(now), heartbeat_at=iso(now), lease_expires_at=iso(now + timedelta(seconds=lease_s)))
            self._save(head)
            return head

    def _holder(self, items, iid, consumer):
        it = self._find(items, iid)
        if it['status'] not in ACTIVE:
            raise StoreError('conflict', 'item is %s, not held (its lease may have expired)' % it['status'])
        if it.get('claimed_by') != consumer:
            raise StoreError('forbidden', 'this item is held by another consumer')
        return it

    def heartbeat(self, iid, consumer, lease_s):
        with self.lock():
            items, now = self._fresh()
            it = self._holder(items, iid, consumer)
            it.update(status='running', heartbeat_at=iso(now), lease_expires_at=iso(now + timedelta(seconds=lease_s)))
            self._save(it)
            return it

    def finish(self, iid, consumer, ok, summary=None, error=None):
        summary = clean(summary, 'summary') if summary is not None else None
        error = clean(error, 'error') if error is not None else None
        with self.lock():
            items, now = self._fresh()
            it = self._holder(items, iid, consumer)
            it.update(status='done' if ok else 'failed', finished_at=iso(now), lease_expires_at=None, result_summary=summary, error=error)
            self._save(it)
            return it

    def cancel(self, iid):
        with self.lock():
            items, now = self._fresh()
            it = self._find(items, iid)
            if it['status'] != 'queued':
                raise StoreError('conflict', 'only queued items can be cancelled')
            it.update(status='cancelled', finished_at=iso(now))
            self._save(it)
            return it

    def retry(self, iid):
        """failed -> queued at the BACK (new seq, old file removed). Attempts start again at 0."""
        with self.lock():
            items, now = self._fresh()
            it = self._find(items, iid)
            if it['status'] != 'failed':
                raise StoreError('conflict', 'only failed items can be retried')
            os.remove(self._path(it))
            it.update(seq=self._next_seq(), status='queued', claimed_by=None, lease_expires_at=None, heartbeat_at=None, started_at=None,
                      finished_at=None, error=None, attempts=0)
            self._save(it)
            return it

    def reap(self):
        with self.lock():
            items = self._load_all()
            return self._reap(items, self.clock())
