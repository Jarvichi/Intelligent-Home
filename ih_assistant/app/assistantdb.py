"""Storage for the assistant's own working data: one SQLite file (/data/ih.db) through the shared ihlib.db helper.

What is stored where (full decision table in DOCS.md):
  KV namespace <persona key>, name 'state'    <- old /data/personas/<key>/state.json   (reminders, plans, dates done, notice texts...)
  KV namespace <persona key>, name 'profile'  <- old /data/personas/<key>/profile.json
  table outbox_log (one row per entry)        <- old /data/personas/<key>/outbox.jsonl (kind, time and lengths only, never text)
Everything else stays a file: options.json (config and secrets), STOP / STOP_AUTOREPLY (kill switches), the add-on log,
the GIF cache and the GIF pictures (rebuildable), and each persona's inbox.jsonl (the people's message text, see DOCS.md).

Rules this module keeps:
  - Old JSON files are COPIED into the database and verified inside the import transaction. Only after the commit does ihlib move
    the file to /data/trash/<persona key>/ (purged after ihdb.TRASH_KEEP_DAYS). A file that fails the check stays where it is.
  - If the database is damaged, too new, or cannot be opened, the add-on keeps running in DEGRADED mode on plain files in
    /data/degraded/personas/<key>/ (seeded from the newest snapshot). The database is never recreated. After it is restored, the next
    start imports /data/degraded/ again.
  - A database call that fails while the database is open is NOT redirected to files (that would split the data in two): the call
    raises, the caller's own error handling runs, and status shows the error.
  - All database work runs on ONE long-lived worker thread (ihlib keeps one connection per thread; the HTTP threads are short-lived).
  - Status shows counts only: never message or reminder text."""
import json, logging, os, re, sqlite3, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote
from ihlib import db as ihdb

DB_NAME = ihdb.DB_FILE_NAME
TRASH_DIR = 'trash'                  # old JSON files wait here (one folder per persona), purged after ihdb.TRASH_KEEP_DAYS
BACKUP_DIR = 'backup'                # nightly snapshots, newest ihdb.SNAPSHOT_KEEP kept
DEGRADED_DIR = 'degraded'            # plain files written while the database is unusable
PERSONAS_DIR = 'personas'            # old layout: <data>/personas/<key>/...
DOC_NAMES = ('profile', 'state')     # the two JSON documents per persona (KV names, and file stems)
OUTBOX_FILE = 'outbox.jsonl'
DOC_SUFFIX = '.json'
OUTBOX_KEEP = 1000                   # outbox rows kept per persona (same limit as the old file)
SNAPSHOT_HOUR = 4                    # local hour from which the nightly snapshot may be taken (before the HA backup at about 05:00)
SNAPSHOT_MIN_AGE_S = 20 * 3600       # a snapshot younger than this counts as "done for tonight"
MAINTENANCE_CHECK_S = 600            # how often the maintenance thread looks at the clock
CALL_TIMEOUT_S = 30                  # longest wait for the database worker thread
HEALTH_TIMEOUT_S = 5                 # status must answer fast even when the worker is busy
READ_ONLY_TIMEOUT_S = 2              # reading a snapshot for degraded mode
ERROR_TEXT_MAX = 300
MAX_IMPORT_ERRORS = 10
TS_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
KEY_RE = re.compile(r'^[a-z][a-z0-9_]{1,20}$')    # same rule as store.KEY_RE

MIGRATIONS = [
    (1, 'CREATE TABLE outbox_log(id INTEGER PRIMARY KEY AUTOINCREMENT, persona TEXT NOT NULL, ts TEXT NOT NULL, kind TEXT, entry TEXT NOT NULL);'
        'CREATE INDEX outbox_log_persona_kind ON outbox_log(persona, kind, id)'),
]
EXPECTED_SCHEMA = MIGRATIONS[-1][0]

_LOG = logging.getLogger('ih_assistant')


def _short(e):
    return ('%s: %s' % (type(e).__name__, e))[:ERROR_TEXT_MAX]


def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _lines(path):
    try:
        with open(path, encoding='utf-8') as f:
            return [ln for ln in f.read().split('\n') if ln.strip()]
    except OSError:
        return []


def _ts_to_epoch(text):
    import calendar
    return calendar.timegm(time.strptime(text, TS_FORMAT))


class AssistantDB:
    def __init__(self, data_dir, clock=time.time, log=None):
        self.data_dir, self.clock = data_dir, clock
        self.log = log or _LOG.info
        self.path = os.path.join(data_dir, DB_NAME)
        self.trash_dir = os.path.join(data_dir, TRASH_DIR)
        self.backup_dir = os.path.join(data_dir, BACKUP_DIR)
        self.degraded_dir = os.path.join(data_dir, DEGRADED_DIR)
        self.db = None
        self._kvs = {}                   # namespace -> KV (made on the worker thread)
        self.degraded = None             # text when the database cannot be used at all
        self.runtime_error = None        # last failed database call while the file was open
        self.import_errors = []
        self.imported = 0                # files moved to the trash by this process
        self.last_check = None           # restore_check result of the newest snapshot (counts only)
        self.last_purge_day = None
        self.last_maintenance_error = None
        self._imported_keys = set()
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix='assistantdb')
        self._health = None
        self._stop = threading.Event()
        self._seed_lock = threading.Lock()

    # ---- run on the one database thread
    def _call(self, fn, timeout=CALL_TIMEOUT_S):
        try:
            return self._ex.submit(fn).result(timeout)
        except Exception as e:                                       # includes FutureTimeout
            if self.db is not None:
                self.runtime_error = _short(e)
                self.log('assistant: DATABASE CALL FAILED: %s' % self.runtime_error)
            raise

    def open(self):
        """Open, migrate. Never raises: failure -> degraded mode."""
        try:
            self._call(self._open)
        except Exception as e:
            self._set_degraded(e)
        return self

    def _set_degraded(self, e):
        self.degraded = _short(e)
        self.runtime_error = None
        self.db = None
        self._kvs = {}
        self.log('assistant: DATABASE DEGRADED, running on plain files in %s: %s' % (self.degraded_dir, self.degraded))

    def _open(self):
        db = ihdb.open(self.path, MIGRATIONS, snapshot_dir=self.backup_dir, clock=self.clock)
        self.db = db
        self.maintain()

    def close(self):
        self._stop.set()
        try:
            self._call(lambda: self.db and self.db.close(), HEALTH_TIMEOUT_S)
        except Exception:
            pass
        self._ex.shutdown(wait=False)

    def _kv(self, ns):
        kv = self._kvs.get(ns)
        if kv is None:
            kv = self._kvs[ns] = ihdb.KV(self.db, ns)
        return kv

    # ---- documents (profile, state)
    def _legacy_path(self, key, which):
        return os.path.join(self.data_dir, PERSONAS_DIR, key, which + DOC_SUFFIX)

    def _degraded_path(self, key, name):
        return os.path.join(self.degraded_dir, PERSONAS_DIR, key, name)

    def read_doc(self, key, which):
        """-> the stored dict, or None when there is none yet."""
        if self.db is not None:
            v = self._call(lambda: self._kv(key).get(which))
            if v is None:
                v = _read_json(self._legacy_path(key, which))   # not imported (import failed) or a persona with no row yet
            return v if isinstance(v, dict) else None
        v = _read_json(self._degraded_path(key, which + DOC_SUFFIX))
        if v is None:
            v = _read_json(self._legacy_path(key, which))
        if v is None:
            v = self._seed_from_snapshot(key, which)
        return v if isinstance(v, dict) else None

    def write_doc(self, key, which, obj):
        json.dumps(obj)                      # a value that cannot be stored fails here, before anything changes
        if self.db is not None:
            self._call(lambda: self._kv(key).set(which, obj))
            return
        _write_json(self._degraded_path(key, which + DOC_SUFFIX), obj)

    def _seed_from_snapshot(self, key, which):
        """Degraded mode only: the last good copy of this document from the newest snapshot (read-only). Written to the degraded
        file at once so it is read from the snapshot only once."""
        with self._seed_lock:
            path = self._newest_snapshot()
            if not path:
                return None
            con = None
            try:
                con = sqlite3.connect('file:%s?mode=ro&immutable=1' % quote(os.path.abspath(path)), uri=True, timeout=READ_ONLY_TIMEOUT_S)
                row = con.execute('SELECT json FROM kv WHERE namespace=? AND name=?', (key, which)).fetchone()
                value = json.loads(row[0]) if row else None
            except (sqlite3.Error, ValueError):
                return None
            finally:
                if con is not None:
                    con.close()
            if isinstance(value, dict):
                _write_json(self._degraded_path(key, which + DOC_SUFFIX), value)
            return value

    # ---- outbox log (kind, time, lengths: never text)
    def outbox_add(self, key, entry, keep=OUTBOX_KEEP):
        text = json.dumps(entry, ensure_ascii=False)
        if self.db is not None:
            def add():
                with self.db.tx() as con:
                    con.execute('INSERT INTO outbox_log(persona, ts, kind, entry) VALUES(?, ?, ?, ?)',
                                (key, str(entry.get('ts') or ''), entry.get('kind') if isinstance(entry.get('kind'), str) else None, text))
                    self._trim_outbox(con, key, keep)
            self._call(add)
            return
        path = self._degraded_path(key, OUTBOX_FILE)
        lines = _lines(path) + [text]
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines[-keep:]) + '\n')
        os.replace(tmp, path)

    @staticmethod
    def _trim_outbox(con, key, keep):
        con.execute('DELETE FROM outbox_log WHERE persona=? AND id NOT IN '
                    '(SELECT id FROM outbox_log WHERE persona=? ORDER BY id DESC LIMIT ?)', (key, key, keep))

    def outbox_count(self, key):
        if self.db is not None:
            return self._call(lambda: self.db.query('SELECT COUNT(*) FROM outbox_log WHERE persona=?', (key,))[0][0])
        return len(_lines(self._degraded_path(key, OUTBOX_FILE)))

    def outbox_last_ts(self, key, kinds):
        """Unix time of the newest outbox entry whose kind is in `kinds` (None if none). Reads only times and kinds."""
        kinds = list(kinds)
        best = None
        if self.db is not None:
            marks = ','.join('?' * len(kinds))
            rows = self._call(lambda: self.db.query('SELECT ts FROM outbox_log WHERE persona=? AND kind IN (%s)' % marks, [key] + kinds)) if kinds else []
            stamps = [r[0] for r in rows]
        else:
            stamps = []
            for ln in _lines(self._degraded_path(key, OUTBOX_FILE)):
                try:
                    e = json.loads(ln)
                    if e.get('kind') in kinds:
                        stamps.append(e['ts'])
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
        for s in stamps:
            try:
                t = _ts_to_epoch(s)
            except (ValueError, TypeError, OverflowError):
                continue
            best = t if best is None else max(best, t)
        return best

    # ---- legacy import (copy, verify, then trash)
    def import_persona(self, key, folder):
        """Import this persona's old files once per process. Never raises."""
        if self.db is None or key in self._imported_keys:
            return
        self._imported_keys.add(key)
        try:
            self._call(lambda: self._import_persona(key, folder))
        except Exception as e:
            self._import_failed(folder, e)

    def _import_persona(self, key, folder):
        trash = os.path.join(self.trash_dir, key)
        self.db.con.execute(ihdb._LEGACY_SQL)
        before = self.imported
        for base, replace in ((folder, False), (os.path.join(self.degraded_dir, PERSONAS_DIR, key), True)):
            sources = [(os.path.join(base, w + DOC_SUFFIX), self._put_doc(key, w, replace)) for w in DOC_NAMES]
            sources.append((os.path.join(base, OUTBOX_FILE), self._put_outbox(key)))
            for path, target in sources:
                if not os.path.isfile(path):
                    continue
                if replace:
                    # a degraded file at an old path must be imported again, so forget the earlier marker of that path
                    self.db.execute('DELETE FROM ih_legacy_import WHERE source=?', (os.path.abspath(path),))
                try:
                    r = ihdb.import_legacy_json(self.db, path, target, trash, skip_bad_lines=True)
                except ihdb.LegacyImportError as e:
                    self._import_failed(path, e)
                    continue
                if r['status'] == 'imported':
                    self.imported += 1
                    self.log('assistant: imported %s (%d row) into the database, old file moved to trash' % (os.path.basename(path), r['rows']))
        if self.imported != before:
            self._snapshot_and_check()           # a safety copy right after files moved to the trash

    def _import_failed(self, path, e):
        shown = os.path.relpath(path, self.data_dir) if isinstance(path, str) and path.startswith(self.data_dir) else 'persona folder'
        msg = '%s: %s' % (shown, _short(e))
        self.log('assistant: legacy import FAILED, file left in place: %s' % msg)
        if msg not in self.import_errors and len(self.import_errors) < MAX_IMPORT_ERRORS:
            self.import_errors.append(msg)

    def _put_doc(self, key, which, replace):
        def put(con, data):
            if not isinstance(data, dict):
                raise ValueError('%s file is not an object' % which)
            kv = self._kv(key)
            wrote = replace or kv.get(which) is None
            if wrote:
                kv.set(which, data)
            stored = kv.get(which)                                   # verify before the commit (and so before the move)
            if stored is None or (wrote and stored != data):
                raise ValueError('verify failed: %s row differs' % which)
            return 1
        return put

    def _put_outbox(self, key):
        def put(con, data):
            rows = [e for e in data if isinstance(e, dict)]
            count = lambda: con.execute('SELECT COUNT(*) FROM outbox_log WHERE persona=?', (key,)).fetchone()[0]
            before = count()
            for e in rows:
                con.execute('INSERT INTO outbox_log(persona, ts, kind, entry) VALUES(?, ?, ?, ?)',
                            (key, str(e.get('ts') or ''), e.get('kind') if isinstance(e.get('kind'), str) else None, json.dumps(e, ensure_ascii=False)))
            self._trim_outbox(con, key, OUTBOX_KEEP)
            if count() != min(before + len(rows), OUTBOX_KEEP):
                raise ValueError('verify failed: outbox row counts')
            return len(rows)
        return put

    # ---- nightly work: trash purge, snapshot, restore check
    def _newest_snapshot(self):
        names = ihdb._snapshot_names(self.backup_dir, os.path.splitext(DB_NAME)[0])
        return os.path.join(self.backup_dir, names[-1]) if names else None

    def _snapshot_age_s(self):
        p = self._newest_snapshot()
        if not p:
            return None
        stamp = re.search(r'-(\d{8}T\d{6}Z)\.db$', p)
        try:
            t = datetime.strptime(stamp.group(1), ihdb.STAMP_FORMAT).replace(tzinfo=timezone.utc).timestamp()
        except (AttributeError, ValueError):
            t = os.path.getmtime(p)
        return self.clock() - t

    def _purge_trash(self):
        gone = ihdb.purge_trash(self.trash_dir, clock=self.clock)
        try:
            subs = [e.path for e in os.scandir(self.trash_dir) if e.is_dir(follow_symlinks=False)]
        except OSError:
            subs = []
        for sub in subs:
            gone += ihdb.purge_trash(sub, clock=self.clock)
            try:
                os.rmdir(sub)                      # only succeeds when empty
            except OSError:
                pass
        return gone

    def maintain(self, local_hour=None):
        """Purge the trash once a day; take a snapshot once a night (from SNAPSHOT_HOUR) and check it can be restored.
        local_hour None = no hour gate (used at start only for the purge). Never raises."""
        if self.db is None:
            return
        try:
            day = datetime.fromtimestamp(self.clock(), timezone.utc).date()
            if self.last_purge_day != day:
                gone = self._purge_trash()
                self.last_purge_day = day
                if gone:
                    self.log('assistant: trash purge removed %d old file(s)' % len(gone))
            age = self._snapshot_age_s()
            if local_hour is not None and local_hour >= SNAPSHOT_HOUR and (age is None or age > SNAPSHOT_MIN_AGE_S):
                self._snapshot_and_check()
            self.last_maintenance_error = None
        except Exception as e:
            self.last_maintenance_error = _short(e)
            self.log('assistant: nightly maintenance FAILED: %s' % self.last_maintenance_error)

    def _snapshot_and_check(self):
        snap = ihdb.snapshot(self.path, self.backup_dir, clock=self.clock)
        chk = ihdb.restore_check(snap)
        live = self.db.table_counts()
        self.last_check = {'at': ihdb._iso(self.clock()), 'ok': chk['ok'], 'schema_version': chk['schema_version'], 'rows': chk['tables'],
                           'rows_match_live': chk['tables'] == live, 'error': chk['error']}
        self.log('assistant: snapshot taken, restore check %s, schema %s, rows %s%s' % (
            'OK' if chk['ok'] else 'FAILED', chk['schema_version'], json.dumps(chk['tables'], sort_keys=True),
            '' if self.last_check['rows_match_live'] else ' (differs from live)'))

    def maintain_now(self, local_hour):
        """Run maintain() on the database thread."""
        try:
            self._ex.submit(lambda: self.maintain(local_hour)).result(CALL_TIMEOUT_S)
        except Exception as e:
            self.last_maintenance_error = _short(e)

    def start_maintenance(self, tz_name='Europe/London'):
        """Daemon thread: wakes every MAINTENANCE_CHECK_S, runs maintain() with the local hour."""
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc

        def loop():
            while not self._stop.wait(MAINTENANCE_CHECK_S):
                self.maintain_now(datetime.fromtimestamp(self.clock(), tz).hour)
        t = threading.Thread(target=loop, daemon=True, name='assistantdb-maint')
        t.start()
        return t

    # ---- status (counts only)
    def _health_now(self):
        return {'schema_version': self.db.schema_version(), 'rows': self.db.table_counts()}

    def health(self):
        h = {'engine': 'sqlite', 'file': DB_NAME, 'schema_version': None, 'expected_schema_version': EXPECTED_SCHEMA, 'rows': {}}
        if self.db is not None:
            try:
                got = self._ex.submit(self._health_now).result(HEALTH_TIMEOUT_S)
                self._health = got
            except Exception as e:
                got = dict(self._health or {})
                h['status_error'] = _short(e)
            h.update(got)
        h['degraded'] = self.degraded is not None
        h['error'] = self.degraded or self.runtime_error
        if self.degraded:
            h['WARNING'] = 'DATABASE NOT IN USE: state is kept in plain files in /data/%s until the database is restored' % DEGRADED_DIR
        h['last_snapshot'] = None
        age = self._snapshot_age_s()
        if age is not None:
            h['last_snapshot'] = ihdb._iso(self.clock() - age)
        try:
            h['snapshots'] = len(ihdb._snapshot_names(self.backup_dir, os.path.splitext(DB_NAME)[0]))
        except OSError:
            h['snapshots'] = 0
        h['last_restore_check'] = self.last_check
        files = _scan(self.trash_dir)
        h['trash'] = {'files': len(files), 'bytes': sum(s for _, s in files)}
        h['legacy_import_errors'] = list(self.import_errors)
        h['maintenance_error'] = self.last_maintenance_error
        h['corrupt_copies'] = len(ihdb.quarantined(self.path))
        return h


def _scan(folder):
    out = []
    try:
        for e in os.scandir(folder):
            if e.is_file(follow_symlinks=False):
                out.append((e.name, e.stat(follow_symlinks=False).st_size))
            elif e.is_dir(follow_symlinks=False):
                out += _scan(e.path)
    except OSError:
        pass
    return out


_stores, _lock = {}, threading.Lock()


def get(data_dir, clock=time.time, log=None):
    """The database of this data folder, opened once per process (re-opened when its file has been deleted, as tests do)."""
    key = os.path.abspath(data_dir)
    with _lock:
        s = _stores.get(key)
        if s is not None and s.degraded is None and not os.path.exists(s.path):
            _stores.pop(key).close()
            s = None
        if s is None:
            s = _stores[key] = AssistantDB(key, clock=clock, log=log)
            s.open()
        return s


def forget(data_dir):
    """Close and drop the cached database (tests, restart simulation)."""
    with _lock:
        s = _stores.pop(os.path.abspath(data_dir), None)
    if s:
        s.close()
