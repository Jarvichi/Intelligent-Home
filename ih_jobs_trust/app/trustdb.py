"""Storage for the AI trust test: one SQLite file (/data/ih.db) through the shared ihlib.db helper.

What is stored where (full table in DOCS.md):
  KV namespace 'trust', name 'state'   <- old /data/trust_state.json (which month is done, to-do added)
  table trust_runs (one row per month) <- old /data/trust_runs/<YYYY-MM>.json
Everything else stays a file: options.json (config and secrets), STOP / STOP_LIVE (kill switches), the add-on log.

Rules this module keeps:
  - Old JSON files are COPIED into the database and verified (rows compared) inside the import transaction. Only after the commit
    does ihlib move the file to /data/trash. A file that fails the check stays where it is.
  - If the database is damaged, too new, or cannot be opened, the add-on keeps running in DEGRADED mode: state and results go to
    plain files in /data/degraded/ (never to the old paths, and the database is never recreated). Status says so loudly.
    After the database is restored, the next start imports /data/degraded/ automatically.
  - All database work runs on ONE long-lived worker thread (ihlib keeps one connection per thread; HTTP threads are short-lived).
  - Status shows counts only: never question or answer text."""
import glob, json, os, re, sqlite3, threading, time, logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from urllib.parse import quote
from ihlib import db as ihdb

DB_NAME = ihdb.DB_FILE_NAME
TRASH_DIR = 'trash'                  # old JSON files wait here, deleted by ihdb.purge_trash after ihdb.TRASH_KEEP_DAYS
BACKUP_DIR = 'backup'                # nightly snapshots, newest ihdb.SNAPSHOT_KEEP kept
DEGRADED_DIR = 'degraded'            # plain files written while the database is unusable
RUNS_DIR = 'trust_runs'              # old layout: <data>/trust_runs/<YYYY-MM>.json
STATE_FILE = 'trust_state.json'      # old layout: <data>/trust_state.json
KV_NAMESPACE = 'trust'
STATE_KEY = 'state'
MONTH_RE = re.compile(r'\d{4}-\d{2}')
RUN_FILE_RE = re.compile(r'(\d{4}-\d{2})\.json')
SNAPSHOT_HOUR = 4                    # local hour from which the nightly snapshot may be taken (before the HA backup at about 05:00)
SNAPSHOT_MIN_AGE_S = 20 * 3600       # a snapshot younger than this counts as "done for tonight"
MAINTENANCE_CHECK_S = 600            # how often the maintenance thread looks at the clock
CALL_TIMEOUT_S = 30                  # longest wait for the database worker thread
HEALTH_TIMEOUT_S = 5                 # status must answer fast even when the worker is busy
ERROR_TEXT_MAX = 300
MAX_IMPORT_ERRORS = 10

MIGRATIONS = [
    (1, 'CREATE TABLE trust_runs(month TEXT PRIMARY KEY, finished TEXT, dry INTEGER NOT NULL DEFAULT 1, json TEXT NOT NULL)'),
]
EXPECTED_SCHEMA = MIGRATIONS[-1][0]

_LOG = logging.getLogger('ih_jobs_trust')


def _short(e):
    return ('%s: %s' % (type(e).__name__, e))[:ERROR_TEXT_MAX]


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _run_files(folder):
    """{month: path} of the run files in a folder."""
    out = {}
    for p in glob.glob(os.path.join(folder, '*.json')):
        m = RUN_FILE_RE.fullmatch(os.path.basename(p))
        if m:
            out[m.group(1)] = p
    return out


def _good_result(d):
    return isinstance(d, dict) and isinstance(d.get('results'), dict)


def read_latest(data_dir):
    """Newest monthly result dict or None. Safe to call from any process or thread (the status page uses it):
    reads the database read-only, and also looks at the plain files (old layout, degraded mode); the newest month wins."""
    best = {}
    path = os.path.join(data_dir, DB_NAME)
    if os.path.isfile(path):
        con = None
        try:
            con = sqlite3.connect('file:%s?mode=ro' % quote(path), uri=True, timeout=2)
            for month, text in con.execute('SELECT month, json FROM trust_runs ORDER BY month DESC'):
                try:
                    d = json.loads(text)
                except ValueError:
                    continue
                if _good_result(d):
                    best[month] = d
                    break
        except sqlite3.Error:
            pass
        finally:
            if con is not None:
                con.close()
    for folder in (os.path.join(data_dir, RUNS_DIR), os.path.join(data_dir, DEGRADED_DIR, RUNS_DIR)):
        for month, p in sorted(_run_files(folder).items(), reverse=True):
            if month in best:
                break
            d = _read_json(p)
            if _good_result(d):
                best[month] = d
                break
    return best[max(best)] if best else None


class TrustStore:
    def __init__(self, data_dir, clock=time.time, log=None):
        self.data_dir, self.clock = data_dir, clock
        self.log = log or _LOG.info
        self.path = os.path.join(data_dir, DB_NAME)
        self.trash_dir = os.path.join(data_dir, TRASH_DIR)
        self.backup_dir = os.path.join(data_dir, BACKUP_DIR)
        self.degraded_dir = os.path.join(data_dir, DEGRADED_DIR)
        self.db = self.kv = None
        self.degraded = None             # text when the database cannot be used at all
        self.runtime_error = None        # last failed database call while the file was open
        self.import_errors = []
        self.imported = {}               # {'state': n, 'runs': n} files moved to the trash this process
        self.last_check = None           # restore_check result of the newest snapshot (counts only)
        self.last_purge_day = None
        self.last_maintenance_error = None
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix='trustdb')
        self._health = None
        self._stop = threading.Event()

    # ---- run on the one database thread
    def _call(self, fn, timeout=CALL_TIMEOUT_S):
        return self._ex.submit(fn).result(timeout)

    def open(self):
        """Open, migrate and import the legacy files. Never raises: failure -> degraded mode."""
        try:
            self._call(self._open)
        except Exception as e:                                       # includes FutureTimeout
            self._set_degraded(e)
        return self

    def _set_degraded(self, e):
        self.degraded = _short(e)
        self.db = self.kv = None
        self.log('trust: DATABASE DEGRADED, running on plain files in %s: %s' % (self.degraded_dir, self.degraded))

    def _open(self):
        self.db = ihdb.open(self.path, MIGRATIONS, snapshot_dir=self.backup_dir, clock=self.clock)
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

    # ---- legacy import (copy, verify, then trash)
    def _import_legacy(self):
        for folder, replace in ((self.data_dir, False), (self.degraded_dir, True)):
            sources = [(os.path.join(folder, STATE_FILE), 'state', None)]
            sources += [(p, 'runs', m) for m, p in sorted(_run_files(os.path.join(folder, RUNS_DIR)).items())]
            for path, kind, month in sources:
                if not os.path.isfile(path):
                    continue
                target = self._put_state(replace) if kind == 'state' else self._put_run(month, replace)
                try:
                    r = ihdb.import_legacy_json(self.db, path, target, self.trash_dir)
                except ihdb.LegacyImportError as e:
                    self._import_failed(path, e)
                    continue
                if r['status'] == 'imported':
                    self.imported[kind] = self.imported.get(kind, 0) + 1
                    self.log('trust: imported %s (%d row) into the database, old file moved to trash' % (os.path.basename(path), r['rows']))

    def _import_failed(self, path, e):
        msg = '%s: %s' % (os.path.relpath(path, self.data_dir), _short(e))
        self.log('trust: legacy import FAILED, file left in place: %s' % msg)
        if msg not in self.import_errors and len(self.import_errors) < MAX_IMPORT_ERRORS:
            self.import_errors.append(msg)

    def _put_state(self, replace):
        def put(con, data):
            if not isinstance(data, dict):
                raise ValueError('state file is not an object')
            if replace or self.kv.get(STATE_KEY) is None:
                self.kv.set(STATE_KEY, data)
            stored = self.kv.get(STATE_KEY)                           # verify before the commit (and so before the move)
            if stored is None or (replace and stored != data):
                raise ValueError('verify failed: state row differs')
            return 1
        return put

    def _put_run(self, month, replace):
        def put(con, data):
            if not _good_result(data):
                raise ValueError('run file has no results')
            before = con.execute('SELECT COUNT(*) FROM trust_runs').fetchone()[0]
            verb = 'REPLACE' if replace else 'IGNORE'
            con.execute('INSERT OR %s INTO trust_runs(month, finished, dry, json) VALUES(?, ?, ?, ?)'
                        % verb, (month, str(data.get('finished') or ''), 1 if data.get('dry', True) else 0, json.dumps(data)))
            row = con.execute('SELECT json FROM trust_runs WHERE month=?', (month,)).fetchone()
            after = con.execute('SELECT COUNT(*) FROM trust_runs').fetchone()[0]
            if row is None or after < before or (replace and json.loads(row[0]) != data):
                raise ValueError('verify failed: row counts %d -> %d' % (before, after))
            return 1
        return put

    # ---- the job's calls
    def state(self):
        if self.db is not None:
            try:
                d = self._call(lambda: self.kv.get(STATE_KEY))
                if d is not None:
                    return d                 # no row yet (for example an import that failed): read the old file below
            except Exception as e:
                self._runtime_failed(e)
        for p in (os.path.join(self.degraded_dir, STATE_FILE), os.path.join(self.data_dir, STATE_FILE)):
            d = _read_json(p)
            if isinstance(d, dict):
                return d
        return {}

    def save_state(self, st):
        if self.db is not None:
            try:
                self._call(lambda: self.kv.set(STATE_KEY, st))
                return
            except Exception as e:
                self._runtime_failed(e)
        _write_json(os.path.join(self.degraded_dir, STATE_FILE), st)

    def save_run(self, month, obj):
        if not MONTH_RE.fullmatch(month):
            raise ValueError('bad month')
        if self.db is not None:
            try:
                self._call(lambda: self.db.execute('INSERT OR REPLACE INTO trust_runs(month, finished, dry, json) VALUES(?, ?, ?, ?)',
                                                   (month, str(obj.get('finished') or ''), 1 if obj.get('dry', True) else 0, json.dumps(obj))))
                return
            except Exception as e:
                self._runtime_failed(e)
        _write_json(os.path.join(self.degraded_dir, RUNS_DIR, month + '.json'), obj)

    def get_run(self, month):
        if self.db is not None:
            try:
                rows = self._call(lambda: self.db.query('SELECT json FROM trust_runs WHERE month=?', (month,)))
                if rows:
                    return json.loads(rows[0][0])
            except Exception as e:
                self._runtime_failed(e)
        for folder in (os.path.join(self.degraded_dir, RUNS_DIR), os.path.join(self.data_dir, RUNS_DIR)):
            d = _read_json(os.path.join(folder, month + '.json'))
            if d is not None:
                return d
        return None

    def _runtime_failed(self, e):
        self.runtime_error = _short(e)
        self.log('trust: DATABASE CALL FAILED, wrote to plain files in %s instead: %s' % (self.degraded_dir, self.runtime_error))

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

    def maintain(self, local_hour=None):
        """Purge the trash once a day; take a snapshot once a night (from SNAPSHOT_HOUR) and check it can be restored.
        local_hour None = no hour gate (used at start only for the purge). Never raises."""
        if self.db is None:
            return
        try:
            day = datetime.fromtimestamp(self.clock(), timezone.utc).date()
            if self.last_purge_day != day:
                gone = ihdb.purge_trash(self.trash_dir, clock=self.clock)
                self.last_purge_day = day
                if gone:
                    self.log('trust: trash purge removed %d old file(s)' % len(gone))
            age = self._snapshot_age_s()
            if local_hour is not None and local_hour >= SNAPSHOT_HOUR and (age is None or age > SNAPSHOT_MIN_AGE_S):
                self._snapshot_and_check()
            self.last_maintenance_error = None
        except Exception as e:
            self.last_maintenance_error = _short(e)
            self.log('trust: nightly maintenance FAILED: %s' % self.last_maintenance_error)

    def _snapshot_and_check(self):
        snap = ihdb.snapshot(self.path, self.backup_dir, clock=self.clock)
        chk = ihdb.restore_check(snap)
        live = self.db.table_counts()
        self.last_check = {'at': ihdb._iso(self.clock()), 'ok': chk['ok'], 'schema_version': chk['schema_version'], 'rows': chk['tables'],
                           'rows_match_live': chk['tables'] == live, 'error': chk['error']}
        self.log('trust: snapshot taken, restore check %s, schema %s, rows %s%s' % (
            'OK' if chk['ok'] else 'FAILED', chk['schema_version'], json.dumps(chk['tables'], sort_keys=True), '' if self.last_check['rows_match_live'] else ' (differs from live)'))

    def maintain_now(self, local_hour):
        """Run maintain() on the database thread."""
        try:
            self._call(lambda: self.maintain(local_hour))
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
        t = threading.Thread(target=loop, daemon=True, name='trustdb-maint')
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
            h['WARNING'] = 'DATABASE NOT IN USE: results are kept in plain files in /data/%s until the database is restored' % DEGRADED_DIR
        h['last_snapshot'] = None
        age = self._snapshot_age_s()
        if age is not None:
            h['last_snapshot'] = ihdb._iso(self.clock() - age)
        try:
            h['snapshots'] = len(ihdb._snapshot_names(self.backup_dir, os.path.splitext(DB_NAME)[0]))
        except OSError:
            h['snapshots'] = 0
        h['last_restore_check'] = self.last_check
        files = [e for e in _scan(self.trash_dir)]
        h['trash'] = {'files': len(files), 'bytes': sum(s for _, s in files)}
        h['legacy_import_errors'] = list(self.import_errors)
        h['maintenance_error'] = self.last_maintenance_error
        h['corrupt_copies'] = len(ihdb.quarantined(self.path))
        return h


def _scan(folder):
    try:
        return [(e.name, e.stat(follow_symlinks=False).st_size) for e in os.scandir(folder) if e.is_file(follow_symlinks=False)]
    except OSError:
        return []


_stores, _lock = {}, threading.Lock()


def get(data_dir, clock=time.time, log=None):
    """The store of this data folder, opened once per process."""
    key = os.path.abspath(data_dir)
    with _lock:
        s = _stores.get(key)
        if s is None:
            s = _stores[key] = TrustStore(key, clock=clock, log=log)
            s.open()
        return s


def forget(data_dir):
    """Close and drop the cached store (tests)."""
    with _lock:
        s = _stores.pop(os.path.abspath(data_dir), None)
    if s:
        s.close()
