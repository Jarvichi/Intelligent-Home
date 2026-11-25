"""Storage for the Met Office job: one SQLite file (/data/ih.db) through the shared ihlib.db helper.

What is stored where (full table in DOCS.md):
  tables forecast_fetch + forecast_series  <- old /data/metoffice_cache.json (the last API answer) AND a short history of earlier answers
Everything else stays a file: options.json (config and the API key), STOP_LIVE (kill switch), the add-on log.

Rules this module keeps (same as the AI trust test pilot):
  - The old JSON file is COPIED into the database and verified (read back and compared) inside the import transaction. Only after the
    commit does ihlib move the file to /data/trash. A file that fails the check stays where it is.
  - A database failure NEVER stops the sensors: if the database is damaged, too new, or fails during a call, the forecast cache goes to
    a plain file in /data/degraded/ and the job carries on (it can always fetch again). The database is never recreated by itself.
    After the database is restored, the next start imports /data/degraded/ automatically.
  - All database work runs on ONE long-lived worker thread (ihlib keeps one connection per thread; HTTP threads are short-lived).
  - Status shows counts only: never forecast values, place names or keys."""
import json, os, re, threading, time, logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from ihlib import db as ihdb

DB_NAME = ihdb.DB_FILE_NAME
TRASH_DIR = 'trash'                  # old JSON file waits here, deleted by ihdb.purge_trash after ihdb.TRASH_KEEP_DAYS
BACKUP_DIR = 'backup'                # nightly snapshots, newest ihdb.SNAPSHOT_KEEP kept
DEGRADED_DIR = 'degraded'            # plain file written while the database is unusable
CACHE_FILE = 'metoffice_cache.json'  # old layout: <data>/metoffice_cache.json
HISTORY_KEEP_DAYS = 3                # forecast answers older than this are deleted (the newest one is always kept)
SECONDS_PER_DAY = ihdb.SECONDS_PER_DAY
SNAPSHOT_HOUR = 4                    # local hour from which the nightly snapshot may be taken (before the HA backup at about 05:00)
SNAPSHOT_MIN_AGE_S = 20 * 3600       # a snapshot younger than this counts as "done for tonight"
MAINTENANCE_CHECK_S = 600            # how often the maintenance thread looks at the clock
CALL_TIMEOUT_S = 30                  # longest wait for the database worker thread
HEALTH_TIMEOUT_S = 5                 # status must answer fast even when the worker is busy
ERROR_TEXT_MAX = 300
MAX_IMPORT_ERRORS = 10

MIGRATIONS = [
    (1, '''CREATE TABLE forecast_fetch(fetched_at REAL PRIMARY KEY, series_count INTEGER NOT NULL);
CREATE TABLE forecast_series(fetched_at REAL NOT NULL REFERENCES forecast_fetch(fetched_at) ON DELETE CASCADE,
    param TEXT NOT NULL, json TEXT NOT NULL, PRIMARY KEY(fetched_at, param)) WITHOUT ROWID'''),
]
EXPECTED_SCHEMA = MIGRATIONS[-1][0]

_LOG = logging.getLogger('ih_jobs_metoffice')


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
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _good_cache(d):
    return (isinstance(d, dict) and isinstance(d.get('fetched_at'), (int, float)) and not isinstance(d.get('fetched_at'), bool)
            and isinstance(d.get('series'), dict) and all(isinstance(v, dict) for v in d['series'].values()))


def _put_cache(con, obj, replace=False):
    """Store one cache object (rows in both tables). Same transaction as the caller's."""
    ts = float(obj['fetched_at'])
    verb = 'REPLACE' if replace else 'IGNORE'
    if replace:
        con.execute('DELETE FROM forecast_fetch WHERE fetched_at=?', (ts,))
    con.execute('INSERT OR %s INTO forecast_fetch(fetched_at, series_count) VALUES(?, ?)' % verb, (ts, len(obj['series'])))
    for name, s in obj['series'].items():
        con.execute('INSERT OR %s INTO forecast_series(fetched_at, param, json) VALUES(?, ?, ?)' % verb, (ts, name, json.dumps(s)))


def _get_cache(con, ts=None):
    """The cache object stored at time ts (newest when None), or None."""
    if ts is None:
        row = con.execute('SELECT fetched_at FROM forecast_fetch ORDER BY fetched_at DESC LIMIT 1').fetchone()
        if row is None:
            return None
        ts = row[0]
    elif con.execute('SELECT 1 FROM forecast_fetch WHERE fetched_at=?', (ts,)).fetchone() is None:
        return None
    return {'fetched_at': ts, 'series': {p: json.loads(j) for p, j in con.execute('SELECT param, json FROM forecast_series WHERE fetched_at=?', (ts,))}}


class MetStore:
    def __init__(self, data_dir, clock=time.time, log=None):
        self.data_dir, self.clock = data_dir, clock
        self.log = log or _LOG.info
        self.path = os.path.join(data_dir, DB_NAME)
        self.trash_dir = os.path.join(data_dir, TRASH_DIR)
        self.backup_dir = os.path.join(data_dir, BACKUP_DIR)
        self.degraded_dir = os.path.join(data_dir, DEGRADED_DIR)
        self.db = None
        self.degraded = None             # text when the database cannot be used at all
        self.runtime_error = None        # last failed database call while the file was open
        self.import_errors = []
        self.imported = 0                # legacy files moved to the trash this process
        self.last_check = None           # restore_check result of the newest snapshot (counts only)
        self.last_purge_day = None
        self.last_maintenance_error = None
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix='metodb')
        self._health = None
        self._stop = threading.Event()

    def _call(self, fn, timeout=CALL_TIMEOUT_S):
        return self._ex.submit(fn).result(timeout)

    def open(self):
        """Open, migrate and import the legacy file. Never raises: failure -> degraded mode."""
        try:
            self._call(self._open)
        except Exception as e:                                       # includes the wait timeout
            self.degraded = _short(e)
            self.db = None
            self.log('metoffice: DATABASE DEGRADED, forecast cache goes to a plain file in %s: %s' % (self.degraded_dir, self.degraded))
        return self

    def _open(self):
        self.db = ihdb.open(self.path, MIGRATIONS, snapshot_dir=self.backup_dir, clock=self.clock)
        self._import_legacy()
        self.maintain()
        if self.imported:
            self._snapshot_and_check()           # a safety copy right after the file moved to the trash

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
            path = os.path.join(folder, CACHE_FILE)
            if not os.path.isfile(path):
                continue
            try:
                r = ihdb.import_legacy_json(self.db, path, self._putter(replace), self.trash_dir)
            except ihdb.LegacyImportError as e:
                msg = '%s: %s' % (os.path.relpath(path, self.data_dir), _short(e))
                self.log('metoffice: legacy import FAILED, file left in place: %s' % msg)
                if msg not in self.import_errors and len(self.import_errors) < MAX_IMPORT_ERRORS:
                    self.import_errors.append(msg)
                continue
            if r['status'] == 'imported':
                self.imported += 1
                self.log('metoffice: imported %s into the database, old file moved to trash' % os.path.basename(path))

    def _putter(self, replace):
        def put(con, data):
            if not _good_cache(data):
                raise ValueError('cache file has no fetched_at/series')
            ts = float(data['fetched_at'])
            existed = con.execute('SELECT 1 FROM forecast_fetch WHERE fetched_at=?', (ts,)).fetchone() is not None
            _put_cache(con, data, replace)
            stored = _get_cache(con, ts)                                  # verify before the commit (and so before the move)
            if stored is None or ((replace or not existed) and stored != json.loads(json.dumps(data))):
                raise ValueError('verify failed: stored cache differs')
            return 1
        return put

    # ---- the job's calls
    def load(self):
        """The newest cache object or None. Database first, then the plain files (degraded, then the old path)."""
        if self.db is not None:
            try:
                d = self._call(lambda: _get_cache(self.db.con))
                if d is not None:
                    return d
            except Exception as e:
                self._runtime_failed(e)
        best = None
        for p in (os.path.join(self.degraded_dir, CACHE_FILE), os.path.join(self.data_dir, CACHE_FILE)):
            d = _read_json(p)
            if _good_cache(d) and (best is None or d['fetched_at'] > best['fetched_at']):
                best = d
        return best

    def save(self, obj):
        """Store a fresh API answer and prune old ones. Never raises: on a database failure it writes the plain file instead."""
        try:
            if self.db is not None:
                try:
                    self._call(lambda: self._save(obj))
                    return True
                except Exception as e:
                    self._runtime_failed(e)
            _write_json(os.path.join(self.degraded_dir, CACHE_FILE), obj)
            return True
        except Exception as e:
            self.log('metoffice: could not store the forecast cache (%s); the sensors still use this run\'s data' % _short(e))
            return False

    def _save(self, obj):
        with self.db.tx() as con:
            _put_cache(con, obj, replace=True)
            con.execute('DELETE FROM forecast_fetch WHERE fetched_at < ? AND fetched_at < (SELECT MAX(fetched_at) FROM forecast_fetch)',
                        (self.clock() - HISTORY_KEEP_DAYS * SECONDS_PER_DAY,))

    def _runtime_failed(self, e):
        self.runtime_error = _short(e)
        self.log('metoffice: DATABASE CALL FAILED, using the plain file in %s instead: %s' % (self.degraded_dir, self.runtime_error))

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
        """Purge the trash once a day; take a snapshot once a night (from SNAPSHOT_HOUR) and check it can be restored. Never raises."""
        if self.db is None:
            return
        try:
            day = datetime.fromtimestamp(self.clock(), timezone.utc).date()
            if self.last_purge_day != day:
                gone = ihdb.purge_trash(self.trash_dir, clock=self.clock)
                self.last_purge_day = day
                if gone:
                    self.log('metoffice: trash purge removed %d old file(s)' % len(gone))
            age = self._snapshot_age_s()
            if local_hour is not None and local_hour >= SNAPSHOT_HOUR and (age is None or age > SNAPSHOT_MIN_AGE_S):
                self._snapshot_and_check()
            self.last_maintenance_error = None
        except Exception as e:
            self.last_maintenance_error = _short(e)
            self.log('metoffice: nightly maintenance FAILED: %s' % self.last_maintenance_error)

    def _snapshot_and_check(self):
        snap = ihdb.snapshot(self.path, self.backup_dir, clock=self.clock)
        chk = ihdb.restore_check(snap)
        live = self.db.table_counts()
        self.last_check = {'at': ihdb._iso(self.clock()), 'ok': chk['ok'], 'schema_version': chk['schema_version'], 'rows': chk['tables'],
                           'rows_match_live': chk['tables'] == live, 'error': chk['error']}
        self.log('metoffice: snapshot taken, restore check %s, schema %s, rows %s%s' % (
            'OK' if chk['ok'] else 'FAILED', chk['schema_version'], json.dumps(chk['tables'], sort_keys=True), '' if self.last_check['rows_match_live'] else ' (differs from live)'))

    def maintain_now(self, local_hour):
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
        t = threading.Thread(target=loop, daemon=True, name='metodb-maint')
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
            h['WARNING'] = 'DATABASE NOT IN USE: the forecast cache is kept in a plain file in /data/%s until the database is restored' % DEGRADED_DIR
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
            s = _stores[key] = MetStore(key, clock=clock, log=log)
            s.open()
        return s


def forget(data_dir):
    """Close and drop the cached store (tests)."""
    with _lock:
        s = _stores.pop(os.path.abspath(data_dir), None)
    if s:
        s.close()
