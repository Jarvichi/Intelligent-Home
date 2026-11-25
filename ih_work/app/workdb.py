"""Storage of the work queue: one SQLite file (/data/ih.db) through the shared ihlib.db helper. Same method names and answers as the old
file store (store.FileStore), so the API and the service do not change.

What is stored where (full table in DOCS.md):
  table work_items   (one row per job, status + seq indexes)   <- old /data/items/<seq>-<id>.json
  KV 'work' / 'seq'                                              <- old /data/seq.counter
  table work_claims  (kind: claims, dry_claims, urgent_claims)   <- old state.json lists of claim times
  table work_fed     (mode live/dry, to-do uid, time)            <- old state.json 'fed' and 'fed_dry'
  KV 'work' / 'gate' (mode, band, mode_since, snapshot, ...)     <- the rest of old /data/state.json
Everything else stays a file: options.json (config + the api_token secret), STOP (kill switch), the add-on log.

Rules this module keeps (same as the pilot ih_jobs_trust 0.2.0):
  - Old files are COPIED into the database and verified inside the import transaction. Only after the commit does ihlib move the file
    to /data/trash. A file that fails the check stays where it is and is listed in `corrupt_files`.
  - If the database is damaged, too new or cannot be opened at start, the queue keeps working on plain files in /data/degraded/ (the old
    file store, same behaviour). The database is never recreated. After it is restored, the next start imports /data/degraded/ again.
  - All database work runs on ONE long-lived worker thread (ihlib keeps one connection per thread; HTTP threads are short-lived).
    Every queue step is one write transaction, so two callers can never get the same job.
  - A database call that fails while the file is open (disk full, locked) is NOT redirected to files (that would split the queue in two):
    the caller gets an error (HTTP 500), `runtime_error` shows in status, and the next call tries the database again.
  - Status shows counts only: never job titles, instructions or results."""
import glob, json, os, re, shutil, sqlite3, threading, time, logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from ihlib import db as ihdb
from store import (ACTIVE, MAX_ATTEMPTS, OPEN, PRIORITIES, STATUSES, WAITING_KINDS, FileStore, StoreError, check_consumer, check_enqueue, check_finish,
                   iso, parse, utcnow)
import uuid

DB_NAME = ihdb.DB_FILE_NAME
TRASH_DIR = 'trash'                  # old files wait here, deleted by ihdb.purge_trash after ihdb.TRASH_KEEP_DAYS
BACKUP_DIR = 'backup'                # nightly snapshots, newest ihdb.SNAPSHOT_KEEP kept
DEGRADED_DIR = 'degraded'            # plain files written while the database is unusable
ITEMS_DIR = 'items'                  # old layout: <data>/items/<seq>-<id>.json
COUNTER_FILE = 'seq.counter'
STATE_FILE = 'state.json'
LOCK_FILE = '.lock'                  # old layout: flock file, not needed any more
KV_NAMESPACE = 'work'
SEQ_KEY, GATE_KEY = 'seq', 'gate'
CLAIM_KINDS = ('claims', 'dry_claims', 'urgent_claims')   # state.json keys that hold lists of claim times
FED_KEYS = {'fed': 'live', 'fed_dry': 'dry'}              # state.json keys that hold {to-do uid: time}
SNAPSHOT_HOUR = 4                    # local hour from which the nightly snapshot may be taken (before the HA backup at about 05:00)
SNAPSHOT_MIN_AGE_S = 20 * 3600       # a snapshot younger than this counts as "done for tonight"
MAINTENANCE_CHECK_S = 600            # how often the maintenance thread looks at the clock
CALL_TIMEOUT_S = 30                  # longest wait for the database worker thread
HEALTH_TIMEOUT_S = 5                 # status must answer fast even when the worker is busy
READ_ONLY_TIMEOUT_S = 2              # the status page reads the database read-only from another process
ERROR_TEXT_MAX = 300
MAX_IMPORT_ERRORS = 20
ITEM_ID_LEN = 8
COLS = ('id', 'seq', 'kind', 'ref', 'title', 'instruction', 'approved', 'status', 'queued_at', 'claimed_by', 'lease_expires_at',
        'heartbeat_at', 'started_at', 'finished_at', 'attempts', 'result_summary', 'error', 'by', 'priority', 'waiting_kind')
SQLCOL = {'by': 'by_who'}            # "by" is an SQL word: the column is by_who, the item key stays "by"
SELECT = ', '.join(SQLCOL.get(c, c) for c in COLS)
SETLIST = ', '.join('%s=?' % SQLCOL.get(c, c) for c in COLS[1:])
PRIORITY_ORDER = "CASE priority WHEN 'high' THEN 0 WHEN 'low' THEN 2 ELSE 1 END, seq"   # same order as store.rank
STATUS_SQL = ', '.join("'%s'" % s for s in STATUSES)

MIGRATIONS = [
    (1, """CREATE TABLE work_items(
        id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, kind TEXT NOT NULL, ref TEXT NOT NULL, title TEXT NOT NULL,
        instruction TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL CHECK(status IN (%s)),
        queued_at TEXT, claimed_by TEXT, lease_expires_at TEXT, heartbeat_at TEXT, started_at TEXT, finished_at TEXT,
        attempts INTEGER NOT NULL DEFAULT 0, result_summary TEXT, error TEXT, by_who TEXT, priority TEXT NOT NULL DEFAULT 'normal', waiting_kind TEXT);
        CREATE INDEX work_items_status ON work_items(status, seq);
        CREATE INDEX work_items_ref ON work_items(kind, ref);
        CREATE TABLE work_claims(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, at TEXT NOT NULL);
        CREATE INDEX work_claims_kind ON work_claims(kind, at);
        CREATE TABLE work_fed(mode TEXT NOT NULL, uid TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(mode, uid))""" % STATUS_SQL),
]
EXPECTED_SCHEMA = MIGRATIONS[-1][0]

_LOG = logging.getLogger('ihwork')


def _short(e):
    return ('%s: %s' % (type(e).__name__, e))[:ERROR_TEXT_MAX]


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---- row <-> item
def _item_of(row):
    d = dict(zip(COLS, row))
    d['approved'] = bool(d['approved'])
    d['attempts'] = int(d['attempts'] or 0)
    return d


def _params(it):
    return [(int(it[c]) if c in ('approved', 'attempts', 'seq') and it[c] is not None else it[c]) for c in COLS[1:]]


def normalize_item(d):
    """A legacy/degraded item file -> a full item dict (missing optional fields filled). Raises ValueError for a bad shape."""
    if not isinstance(d, dict) or not isinstance(d.get('id'), str) or not isinstance(d.get('seq'), int) or isinstance(d.get('seq'), bool) \
            or d.get('status') not in STATUSES:
        raise ValueError('bad shape')
    for c in ('kind', 'ref', 'title', 'instruction'):
        if not isinstance(d.get(c), str):
            raise ValueError('%s must be text' % c)
    it = {c: d.get(c) for c in COLS}
    it['approved'] = bool(d.get('approved'))
    it['priority'] = d.get('priority') if d.get('priority') in PRIORITIES else 'normal'
    try:
        it['attempts'] = int(d.get('attempts') or 0)
    except (TypeError, ValueError):
        raise ValueError('attempts must be a number')
    for c in COLS:
        if c not in ('approved', 'attempts', 'seq') and it[c] is not None and not isinstance(it[c], str):
            raise ValueError('%s must be text' % c)
    return it


def _norm_state(d):
    """state.json content -> the same dict as load_state would return from the tables (empty lists/maps dropped). Raises ValueError."""
    if not isinstance(d, dict):
        raise ValueError('state file is not an object')
    st = dict(d)
    for k in CLAIM_KINDS:
        v = st.pop(k, None) if k in st else None
        if v is None or v == []:
            continue
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError('%s must be a list of times' % k)
        st[k] = list(v)
    for k in FED_KEYS:
        v = st.pop(k, None) if k in st else None
        if not v:
            continue
        if not isinstance(v, dict):
            raise ValueError('%s must be an object' % k)
        st[k] = {str(a): str(b) for a, b in v.items()}
    return st


def _stamp_epoch(path, default):
    m = re.search(r'-(\d{8}T\d{6}Z)\.db$', path)
    try:
        return datetime.strptime(m.group(1), ihdb.STAMP_FORMAT).replace(tzinfo=timezone.utc).timestamp()
    except (AttributeError, ValueError):
        try:
            return os.path.getmtime(path)
        except OSError:
            return default


def _scan(folder):
    try:
        return [(e.name, e.stat(follow_symlinks=False).st_size) for e in os.scandir(folder) if e.is_file(follow_symlinks=False)]
    except OSError:
        return []


def peek(data_dir):
    """-> (items list sorted by seq, snapshot dict or None) for the status page (another process): reads the database read-only and also the
    plain files (old layout, degraded mode); a plain-file copy of an id wins over the database row (it is newer). Never raises for a bad file."""
    items, snap = {}, None
    path = os.path.join(data_dir, DB_NAME)
    if os.path.isfile(path):
        con = None
        try:
            con = sqlite3.connect('file:%s?mode=ro' % quote(path), uri=True, timeout=READ_ONLY_TIMEOUT_S)
            for r in con.execute('SELECT %s FROM work_items ORDER BY seq' % SELECT):
                it = _item_of(r)
                items[it['id']] = it
            row = con.execute('SELECT json FROM kv WHERE namespace=? AND name=?', (KV_NAMESPACE, GATE_KEY)).fetchone()
            snap = (json.loads(row[0]) or {}).get('snapshot') if row else None
        except (sqlite3.Error, ValueError):
            pass
        finally:
            if con is not None:
                con.close()
    for folder in (data_dir, os.path.join(data_dir, DEGRADED_DIR)):
        degraded = folder != data_dir
        for p in sorted(glob.glob(os.path.join(folder, ITEMS_DIR, '*.json'))):
            d = _read_json(p)
            try:
                it = normalize_item(d)
            except ValueError:
                continue
            if degraded or it['id'] not in items:
                items[it['id']] = it
        st = _read_json(os.path.join(folder, STATE_FILE))
        if isinstance(st, dict) and (degraded or snap is None) and st.get('snapshot'):
            snap = st['snapshot']
    return sorted(items.values(), key=lambda i: i['seq']), snap


class WorkStore:
    def __init__(self, data_dir, clock=utcnow, log=None):
        self.data_dir, self.clock = data_dir, clock
        self.log = log or _LOG.info
        self.path = os.path.join(data_dir, DB_NAME)
        self.trash_dir = os.path.join(data_dir, TRASH_DIR)
        self.backup_dir = os.path.join(data_dir, BACKUP_DIR)
        self.degraded_dir = os.path.join(data_dir, DEGRADED_DIR)
        self.db = self.kv = None
        self.files = None                # FileStore in degraded mode
        self.degraded = None             # text when the database cannot be used at all
        self.runtime_error = None        # last failed database call while the file was open
        self.import_errors = []
        self.imported = {}               # {'items': n, ...} files moved to the trash this process
        self.last_check = None
        self.last_purge_day = None
        self.last_maintenance_error = None
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix='workdb')
        self._health = None
        self._stop = threading.Event()
        self._last_gate = None           # last state written, to skip identical writes every 30 s
        self._saved = None

    # ---- clocks: the service clock gives datetimes, ihlib wants epoch seconds
    def _epoch(self):
        return self.clock().timestamp()

    # ---- run on the one database thread
    def _call(self, fn, timeout=CALL_TIMEOUT_S):
        return self._ex.submit(fn).result(timeout)

    def open(self):
        """Open, migrate and import the legacy files. Never raises: failure -> degraded mode."""
        try:
            self._call(self._open)
        except Exception as e:                                       # includes a timeout
            self._set_degraded(e)
        return self

    def _set_degraded(self, e):
        self.degraded = _short(e)
        self.db = self.kv = None
        self._seed_degraded()
        self.files = FileStore(self.degraded_dir, self.clock)
        self.log('work: DATABASE DEGRADED, the queue runs on plain files in %s: %s' % (self.degraded_dir, self.degraded))

    def _seed_degraded(self):
        """Old files that were never imported (a database that broke before the first import) are COPIED into the degraded folder so the
        queue still has them. Originals stay where they are. Nothing is copied when the degraded folder already holds items."""
        try:
            if glob.glob(os.path.join(self.degraded_dir, ITEMS_DIR, '*.json')):
                return
            os.makedirs(os.path.join(self.degraded_dir, ITEMS_DIR), exist_ok=True)
            for p in glob.glob(os.path.join(self.data_dir, ITEMS_DIR, '*.json')):
                shutil.copy2(p, os.path.join(self.degraded_dir, ITEMS_DIR, os.path.basename(p)))
            for name in (COUNTER_FILE, STATE_FILE):
                src, dst = os.path.join(self.data_dir, name), os.path.join(self.degraded_dir, name)
                if os.path.isfile(src) and not os.path.exists(dst):
                    shutil.copy2(src, dst)
        except OSError as e:
            self.log('work: could not copy old files into %s: %s' % (self.degraded_dir, _short(e)))

    def _open(self):
        self.db = ihdb.open_db(self.path, MIGRATIONS, snapshot_dir=self.backup_dir, clock=self._epoch)
        self.kv = ihdb.KV(self.db, KV_NAMESPACE)
        self._import_legacy()
        self.maintain()
        if self.imported:
            self._snapshot_and_check()           # a safety copy right after the files moved to the trash

    def close(self):
        self._stop.set()
        try:
            self._call(lambda: self.db and self.db.close(), HEALTH_TIMEOUT_S)
        except Exception:
            pass
        self._ex.shutdown(wait=False)

    @property
    def corrupt(self):
        return list(self.files.corrupt) if self.files is not None else list(self.import_errors)

    # ---- legacy import (copy, verify, then trash)
    def _import_legacy(self):
        self.db.con.execute(ihdb._LEGACY_SQL)
        for folder, replace in ((self.data_dir, False), (self.degraded_dir, True)):
            sources = [(p, 'items', self._put_item(replace)) for p in sorted(glob.glob(os.path.join(folder, ITEMS_DIR, '*.json')))]
            sources.append((os.path.join(folder, COUNTER_FILE), 'counter', self._put_counter))
            sources.append((os.path.join(folder, STATE_FILE), 'state', self._put_state(replace)))
            for path, kind, target in sources:
                if not os.path.isfile(path):
                    continue
                if replace:      # a degraded file name can come back after an earlier import: forget the old marker
                    self.db.execute('DELETE FROM ih_legacy_import WHERE source=?', (os.path.abspath(path),))
                try:
                    r = ihdb.import_legacy_json(self.db, path, target, self.trash_dir)
                except ihdb.LegacyImportError as e:
                    self._import_failed(path, e)
                    continue
                if r['status'] == 'imported':
                    self.imported[kind] = self.imported.get(kind, 0) + 1
                    self.log('work: imported %s into the database, old file moved to trash' % os.path.relpath(path, self.data_dir))
        lock = os.path.join(self.data_dir, LOCK_FILE)
        if os.path.isfile(lock) and not self.import_errors:
            try:
                os.remove(lock)           # the old flock file; nothing to keep
            except OSError:
                pass

    def _import_failed(self, path, e):
        msg = os.path.relpath(path, self.data_dir)
        self.log('work: legacy import FAILED, file left in place: %s: %s' % (msg, _short(e)))
        if msg not in self.import_errors and len(self.import_errors) < MAX_IMPORT_ERRORS:
            self.import_errors.append(msg)

    def _put_item(self, replace):
        def put(con, data):
            it = normalize_item(data)
            row = con.execute('SELECT %s FROM work_items WHERE id=?' % SELECT, (it['id'],)).fetchone()
            if row is None or replace:
                taken = con.execute('SELECT id FROM work_items WHERE seq=? AND id<>?', (it['seq'], it['id'])).fetchone()
                if taken:
                    if not replace:
                        raise ValueError('seq %d is used by another item' % it['seq'])
                    it['seq'] = self._next_seq(con)
                con.execute('INSERT OR REPLACE INTO work_items(%s) VALUES(%s)' % (', '.join(SQLCOL.get(c, c) for c in COLS), ', '.join('?' * len(COLS))),
                            [it['id']] + _params(it))
                stored = con.execute('SELECT %s FROM work_items WHERE id=?' % SELECT, (it['id'],)).fetchone()
                if stored is None or _item_of(stored) != it:          # verify before the commit (and so before the move)
                    raise ValueError('verify failed: item row differs')
            return 1
        return put

    def _put_counter(self, con, data):
        if isinstance(data, bool) or not isinstance(data, int):
            raise ValueError('counter is not a number')
        cur = (self.kv.get(SEQ_KEY) or {}).get('n', 0)
        self.kv.set(SEQ_KEY, {'n': max(cur, data)})
        if (self.kv.get(SEQ_KEY) or {}).get('n', 0) < data:
            raise ValueError('verify failed: counter')
        return 1

    def _put_state(self, replace):
        def put(con, data):
            st = _norm_state(data)
            if replace or (self.kv.get(GATE_KEY) is None and not con.execute('SELECT 1 FROM work_claims LIMIT 1').fetchone()
                           and not con.execute('SELECT 1 FROM work_fed LIMIT 1').fetchone()):
                self._write_state(con, st)
                if self._read_state(con) != st:                          # verify before the commit
                    raise ValueError('verify failed: state differs')
            return 1
        return put

    # ---- state (gate mode, claim times, fed to-dos): tables + one KV row
    def _read_state(self, con):
        st = dict(self.kv.get(GATE_KEY) or {})
        for k in CLAIM_KINDS:
            times = [r[0] for r in con.execute('SELECT at FROM work_claims WHERE kind=? ORDER BY id', (k,))]
            if times:
                st[k] = times
        for k, mode in FED_KEYS.items():
            fed = {r[0]: r[1] for r in con.execute('SELECT uid, at FROM work_fed WHERE mode=? ORDER BY uid', (mode,))}
            if fed:
                st[k] = fed
        return st

    def _write_state(self, con, st):
        rest = {k: v for k, v in st.items() if k not in CLAIM_KINDS and k not in FED_KEYS}
        if rest != (self.kv.get(GATE_KEY) or {}):
            self.kv.set(GATE_KEY, rest)
        for k in CLAIM_KINDS:
            want = list(st.get(k) or [])
            have = [r[0] for r in con.execute('SELECT at FROM work_claims WHERE kind=? ORDER BY id', (k,))]
            if want != have:
                con.execute('DELETE FROM work_claims WHERE kind=?', (k,))
                con.executemany('INSERT INTO work_claims(kind, at) VALUES(?, ?)', [(k, t) for t in want])
        for k, mode in FED_KEYS.items():
            want = {str(a): str(b) for a, b in (st.get(k) or {}).items()}
            have = {r[0]: r[1] for r in con.execute('SELECT uid, at FROM work_fed WHERE mode=?', (mode,))}
            if want != have:
                con.execute('DELETE FROM work_fed WHERE mode=?', (mode,))
                con.executemany('INSERT INTO work_fed(mode, uid, at) VALUES(?, ?, ?)', [(mode, a, b) for a, b in want.items()])

    def load_state(self):
        if self.db is not None:
            return self._call(lambda: self._read_state(self.db.con))
        d = _read_json(os.path.join(self.degraded_dir, STATE_FILE))
        return d if isinstance(d, dict) else {}

    def save_state(self, st):
        if self.db is not None:
            def work():
                with self.db.tx() as con:
                    self._write_state(con, _norm_state(st))
            return self._guard(work)
        FileStore.write_json(os.path.join(self.degraded_dir, STATE_FILE), st)

    # ---- guarded database call
    def _guard(self, fn):
        try:
            out = self._call(fn)
            self.runtime_error = None
            return out
        except StoreError:
            raise
        except Exception as e:
            self.runtime_error = _short(e)
            self.log('work: DATABASE CALL FAILED (%s)' % self.runtime_error)
            raise

    # ---- queue steps (each one write transaction on the database thread)
    def _next_seq(self, con):
        cur = (self.kv.get(SEQ_KEY) or {}).get('n', 0)
        top = con.execute('SELECT COALESCE(MAX(seq), 0) FROM work_items').fetchone()[0]
        n = max(cur, top) + 1
        self.kv.set(SEQ_KEY, {'n': n})
        return n

    def _save(self, con, it):
        con.execute('UPDATE work_items SET %s WHERE id=?' % SETLIST, _params(it) + [it['id']])

    def _reap(self, con, now):
        """Expired leases -> queued (attempts + 1) or failed. Saves and returns the changed items."""
        changed = []
        rows = con.execute('SELECT %s FROM work_items WHERE status IN (%s) ORDER BY seq' % (SELECT, ', '.join('?' * len(ACTIVE))), ACTIVE).fetchall()
        for r in rows:
            i = _item_of(r)
            exp = parse(i.get('lease_expires_at'))
            if exp is None or exp <= now:
                i['attempts'] += 1
                holder = i.get('claimed_by')
                i.update(claimed_by=None, lease_expires_at=None, heartbeat_at=None, started_at=None)
                if i['attempts'] >= MAX_ATTEMPTS:
                    i.update(status='failed', finished_at=iso(now), error='lease expired %d times (last holder %s)' % (i['attempts'], holder))
                else:
                    i['status'] = 'queued'
                self._save(con, i)
                changed.append(i)
        return changed

    def _tx(self, fn):
        """Run fn(con, now) in one write transaction after reaping expired leases."""
        def work():
            with self.db.tx() as con:
                now = self.clock()
                self._reap(con, now)
                return fn(con, now)
        return self._guard(work)

    def _holder(self, con, iid, consumer):
        row = con.execute('SELECT %s FROM work_items WHERE id=?' % SELECT, (iid,)).fetchone()
        if row is None:
            raise StoreError('missing', 'no such item')
        it = _item_of(row)
        if it['status'] not in ACTIVE:
            raise StoreError('conflict', 'item is %s, not held (its lease may have expired)' % it['status'])
        if it.get('claimed_by') != consumer:
            raise StoreError('forbidden', 'this item is held by another consumer')
        return it

    def list(self, statuses=None):
        if self.db is None:
            return self.files.list(statuses)
        want = [s for s in (statuses or []) if s in STATUSES]
        if statuses and not want:
            return []

        def fn(con, now):
            if want:
                rows = con.execute('SELECT %s FROM work_items WHERE status IN (%s) ORDER BY seq' % (SELECT, ', '.join('?' * len(want))), want)
            else:
                rows = con.execute('SELECT %s FROM work_items ORDER BY seq' % SELECT)
            return [_item_of(r) for r in rows.fetchall()]
        return self._tx(fn)

    def get(self, iid):
        if self.db is None:
            return self.files.get(iid)

        def fn(con, now):
            row = con.execute('SELECT %s FROM work_items WHERE id=?' % SELECT, (iid,)).fetchone()
            return _item_of(row) if row else None
        return self._tx(fn)

    def peek(self):
        """Read-only listing (no reaping)."""
        return peek(self.data_dir)[0]

    def reap(self):
        if self.db is None:
            return self.files.reap()

        def work():
            with self.db.tx() as con:
                return self._reap(con, self.clock())
        return self._guard(work)

    def enqueue(self, kind, ref, title, instruction, approved=False, by='claude', priority='normal'):
        if self.db is None:
            return self.files.enqueue(kind, ref, title, instruction, approved, by, priority)
        ref, title, instruction = check_enqueue(kind, ref, title, instruction, approved, by, priority)

        def fn(con, now):
            row = con.execute('SELECT %s FROM work_items WHERE kind=? AND ref=? AND status IN (%s) ORDER BY seq LIMIT 1'
                              % (SELECT, ', '.join('?' * len(OPEN))), (kind, ref) + tuple(OPEN)).fetchone()
            if row:
                return _item_of(row)
            it = dict(id=uuid.uuid4().hex[:ITEM_ID_LEN], seq=self._next_seq(con), kind=kind, ref=ref, title=title, instruction=instruction,
                      approved=approved, status='queued', queued_at=iso(now), claimed_by=None, lease_expires_at=None, heartbeat_at=None,
                      started_at=None, finished_at=None, attempts=0, result_summary=None, error=None, by=by, priority=priority,
                      waiting_kind=None)
            con.execute('INSERT INTO work_items(%s) VALUES(%s)' % (', '.join(SQLCOL.get(c, c) for c in COLS), ', '.join('?' * len(COLS))),
                        [it['id']] + _params(it))
            return it
        return self._tx(fn)

    def claim(self, consumer, lease_s, iid=None):
        if self.db is None:
            return self.files.claim(consumer, lease_s, iid)
        check_consumer(consumer)

        def fn(con, now):
            if con.execute('SELECT 1 FROM work_items WHERE status IN (%s) LIMIT 1' % ', '.join('?' * len(ACTIVE)), ACTIVE).fetchone():
                raise StoreError('conflict', 'an item is already held')
            if iid is None:
                row = con.execute("SELECT %s FROM work_items WHERE status='queued' ORDER BY %s LIMIT 1" % (SELECT, PRIORITY_ORDER)).fetchone()
            else:
                row = con.execute("SELECT %s FROM work_items WHERE status='queued' AND id=?" % SELECT, (iid,)).fetchone()
            if not row:
                return None
            head = _item_of(row)
            head.update(status='claimed', claimed_by=consumer, started_at=iso(now), heartbeat_at=iso(now),
                        lease_expires_at=iso(now + timedelta(seconds=lease_s)))
            self._save(con, head)
            return head
        return self._tx(fn)

    def heartbeat(self, iid, consumer, lease_s):
        if self.db is None:
            return self.files.heartbeat(iid, consumer, lease_s)

        def fn(con, now):
            it = self._holder(con, iid, consumer)
            it.update(status='running', heartbeat_at=iso(now), lease_expires_at=iso(now + timedelta(seconds=lease_s)))
            self._save(con, it)
            return it
        return self._tx(fn)

    def finish(self, iid, consumer, ok, summary=None, error=None):
        if self.db is None:
            return self.files.finish(iid, consumer, ok, summary, error)
        summary, error = check_finish(summary, error)

        def fn(con, now):
            it = self._holder(con, iid, consumer)
            it.update(status='done' if ok else 'failed', finished_at=iso(now), lease_expires_at=None, result_summary=summary, error=error)
            self._save(con, it)
            return it
        return self._tx(fn)

    def wait(self, iid, consumer, kind, summary=None):
        """Held item -> waiting (the worker did NOT finish it). Same rules as FileStore.wait."""
        if self.db is None:
            return self.files.wait(iid, consumer, kind, summary)
        if kind not in WAITING_KINDS:
            raise StoreError('bad', 'kind must be from: ' + ', '.join(WAITING_KINDS))
        summary = check_finish(summary, None)[0]

        def fn(con, now):
            it = self._holder(con, iid, consumer)
            it.update(status='waiting', waiting_kind=kind, finished_at=iso(now), lease_expires_at=None, result_summary=summary, error=None)
            self._save(con, it)
            return it
        return self._tx(fn)

    def requeue(self, iid):
        """waiting -> queued at the BACK (new seq). Attempts start again at 0."""
        if self.db is None:
            return self.files.requeue(iid)

        def fn(con, now):
            row = con.execute('SELECT %s FROM work_items WHERE id=?' % SELECT, (iid,)).fetchone()
            if row is None:
                raise StoreError('missing', 'no such item')
            it = _item_of(row)
            if it['status'] != 'waiting':
                raise StoreError('conflict', 'only waiting items can be requeued')
            it.update(seq=self._next_seq(con), status='queued', waiting_kind=None, claimed_by=None, lease_expires_at=None, heartbeat_at=None,
                      started_at=None, finished_at=None, error=None, attempts=0)
            self._save(con, it)
            return it
        return self._tx(fn)

    def cancel(self, iid):
        if self.db is None:
            return self.files.cancel(iid)

        def fn(con, now):
            row = con.execute('SELECT %s FROM work_items WHERE id=?' % SELECT, (iid,)).fetchone()
            if row is None:
                raise StoreError('missing', 'no such item')
            it = _item_of(row)
            if it['status'] != 'queued':
                raise StoreError('conflict', 'only queued items can be cancelled')
            it.update(status='cancelled', finished_at=iso(now))
            self._save(con, it)
            return it
        return self._tx(fn)

    def retry(self, iid):
        """failed -> queued at the BACK (new seq). Attempts start again at 0."""
        if self.db is None:
            return self.files.retry(iid)

        def fn(con, now):
            row = con.execute('SELECT %s FROM work_items WHERE id=?' % SELECT, (iid,)).fetchone()
            if row is None:
                raise StoreError('missing', 'no such item')
            it = _item_of(row)
            if it['status'] != 'failed':
                raise StoreError('conflict', 'only failed items can be retried')
            it.update(seq=self._next_seq(con), status='queued', claimed_by=None, lease_expires_at=None, heartbeat_at=None, started_at=None,
                      finished_at=None, error=None, attempts=0)
            self._save(con, it)
            return it
        return self._tx(fn)

    # ---- nightly work: trash purge, snapshot, restore check
    def _snapshot_dir_names(self):
        return ihdb._snapshot_names(self.backup_dir, os.path.splitext(DB_NAME)[0])

    def _snapshot_age_s(self):
        names = self._snapshot_dir_names()
        if not names:
            return None
        p = os.path.join(self.backup_dir, names[-1])
        return self._epoch() - _stamp_epoch(p, self._epoch())

    def maintain(self, local_hour=None):
        """Purge the trash once a day; take a snapshot once a night (from SNAPSHOT_HOUR) and check it can be restored. Never raises."""
        if self.db is None:
            return
        try:
            day = datetime.fromtimestamp(self._epoch(), timezone.utc).date()
            if self.last_purge_day != day:
                gone = ihdb.purge_trash(self.trash_dir, clock=self._epoch)
                self.last_purge_day = day
                if gone:
                    self.log('work: trash purge removed %d old file(s)' % len(gone))
            age = self._snapshot_age_s()
            if local_hour is not None and local_hour >= SNAPSHOT_HOUR and (age is None or age > SNAPSHOT_MIN_AGE_S):
                self._snapshot_and_check()
            self.last_maintenance_error = None
        except Exception as e:
            self.last_maintenance_error = _short(e)
            self.log('work: nightly maintenance FAILED: %s' % self.last_maintenance_error)

    def _snapshot_and_check(self):
        snap = ihdb.snapshot(self.path, self.backup_dir, clock=self._epoch)
        chk = ihdb.restore_check(snap)
        live = self.db.table_counts()
        self.last_check = {'at': ihdb._iso(self._epoch()), 'ok': chk['ok'], 'schema_version': chk['schema_version'], 'rows': chk['tables'],
                           'rows_match_live': chk['tables'] == live, 'error': chk['error']}
        self.log('work: snapshot taken, restore check %s, schema %s, rows %s%s' % (
            'OK' if chk['ok'] else 'FAILED', chk['schema_version'], json.dumps(chk['tables'], sort_keys=True),
            '' if self.last_check['rows_match_live'] else ' (differs from live)'))

    def maintain_now(self, local_hour):
        try:
            self._call(lambda: self.maintain(local_hour))
        except Exception as e:
            self.last_maintenance_error = _short(e)

    def start_maintenance(self, tz=timezone.utc):
        """Daemon thread: wakes every MAINTENANCE_CHECK_S, runs maintain() with the local hour. tz = tzinfo."""
        def loop():
            while not self._stop.wait(MAINTENANCE_CHECK_S):
                self.maintain_now(datetime.fromtimestamp(self._epoch(), tz).hour)
        t = threading.Thread(target=loop, daemon=True, name='workdb-maint')
        t.start()
        return t

    # ---- status (counts only)
    def _health_now(self):
        h = {'engine': 'sqlite', 'file': DB_NAME, 'degraded': False, 'schema_version': None, 'expected_schema_version': EXPECTED_SCHEMA, 'rows': {}}
        if self.db is not None:
            h['schema_version'] = self.db.schema_version()
            h['rows'] = self.db.table_counts()
        return h

    def health(self):
        h = None
        if self.db is not None:
            try:
                h = self._call(self._health_now, HEALTH_TIMEOUT_S)
                self._health = h
            except Exception as e:
                h = dict(self._health or {'engine': 'sqlite', 'file': DB_NAME, 'expected_schema_version': EXPECTED_SCHEMA})
                h['status_error'] = _short(e)
        if h is None:
            h = {'engine': 'sqlite', 'file': DB_NAME, 'schema_version': None, 'expected_schema_version': EXPECTED_SCHEMA, 'rows': {}}
        h['degraded'] = self.degraded is not None
        h['error'] = self.degraded or self.runtime_error
        if self.degraded:
            h['WARNING'] = 'DATABASE NOT IN USE: the queue runs on plain files in /data/%s until the database is restored' % DEGRADED_DIR
        h['last_snapshot'] = None
        age = self._snapshot_age_s()
        if age is not None:
            h['last_snapshot'] = ihdb._iso(self._epoch() - age)
        try:
            h['snapshots'] = len(self._snapshot_dir_names())
        except OSError:
            h['snapshots'] = 0
        h['last_restore_check'] = self.last_check
        files = _scan(self.trash_dir)
        h['trash'] = {'files': len(files), 'bytes': sum(s for _, s in files)}
        h['legacy_import_errors'] = list(self.import_errors)
        h['maintenance_error'] = self.last_maintenance_error
        h['corrupt_copies'] = len(ihdb.quarantined(self.path))
        return h
