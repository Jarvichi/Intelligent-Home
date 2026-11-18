"""Storage for the backup-storage job: one SQLite file (/data/ih.db) through the shared ihlib.db helper.

What is stored where (full table in DOCS.md):
  table backup_samples (one row per job run)  <- NEW history of the storage figures; no old file existed
  KV namespace 'backup', name 'last_run'      <- NEW: when the job last ran and what it saw
Everything else stays a file: options.json (config and secrets), STOP_LIVE (kill switch), the add-on log.
The backups themselves (archives) live in Home Assistant, never here.

Rules this module keeps:
  - A DATABASE FAILURE MUST NEVER STOP A BACKUP-STORAGE RUN. record_run() never raises and waits at most RECORD_TIMEOUT_S.
    The job's work (reading backup/info, publishing the sensors) does not use this database at all.
  - If the database is damaged, too new, or cannot be opened, the add-on runs in DEGRADED mode: samples go to a bounded
    plain file in /data/degraded/ (never the database; the database is never recreated). Status says so loudly.
    After the database is restored, the next start imports that file (copy, verify, then trash).
  - All database work runs on ONE long-lived worker thread (ihlib keeps one connection per thread).
  - Status shows counts and times only."""
import json, os, re, threading, time, logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from ihlib import db as ihdb

DB_NAME = ihdb.DB_FILE_NAME
TRASH_DIR = 'trash'                  # imported degraded files wait here, deleted by ihdb.purge_trash after ihdb.TRASH_KEEP_DAYS
BACKUP_DIR = 'backup'                # nightly snapshots of ih.db (NOT Home Assistant backups), newest ihdb.SNAPSHOT_KEEP kept
DEGRADED_DIR = 'degraded'            # plain files written while the database is unusable
DEGRADED_SAMPLES = 'backup_samples.jsonl'
KV_NAMESPACE = 'backup'
LAST_RUN_KEY = 'last_run'
SNAPSHOT_HOUR = 4                    # local hour from which the nightly snapshot may be taken (before the HA backup at about 05:00)
SNAPSHOT_MIN_AGE_S = 20 * 3600       # a snapshot younger than this counts as "done for tonight"
MAINTENANCE_CHECK_S = 600            # how often the maintenance thread looks at the clock
CALL_TIMEOUT_S = 30                  # longest wait for the database worker at start-up and in maintenance
RECORD_TIMEOUT_S = 8                 # longest a job run waits to record its sample (then it carries on without)
HEALTH_TIMEOUT_S = 5                 # status must answer fast even when the worker is busy
SAMPLE_KEEP_DAYS = 400               # older sample rows are deleted by the nightly maintenance
DEGRADED_MAX_BYTES = 256 * 1024      # the degraded file stops growing here (a run every few minutes would otherwise fill the disk)
ERROR_TEXT_MAX = 300
MAX_IMPORT_ERRORS = 10
SECONDS_PER_DAY = 86400
SAMPLE_COLUMNS = ('ts', 'mode', 'local_used_gb', 'local_backups_gb', 'local_count', 'drive_used_gb', 'drive_backups_gb', 'drive_count', 'disk_known')

MIGRATIONS = [
    (1, 'CREATE TABLE backup_samples(ts TEXT PRIMARY KEY, mode TEXT, local_used_gb REAL, local_backups_gb REAL, local_count INTEGER, '
        'drive_used_gb REAL, drive_backups_gb REAL, drive_count INTEGER, disk_known INTEGER NOT NULL DEFAULT 1)'),
]
EXPECTED_SCHEMA = MIGRATIONS[-1][0]

_LOG = logging.getLogger('ih_jobs_backup')


def _short(e):
    return ('%s: %s' % (type(e).__name__, e))[:ERROR_TEXT_MAX]


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec='seconds')


def _clean(sample):
    """Keep only known columns; numbers or None."""
    out = {}
    for k in SAMPLE_COLUMNS:
        v = sample.get(k)
        out[k] = v if isinstance(v, (int, float, str)) or v is None else None
    out['disk_known'] = 1 if sample.get('disk_known', True) else 0
    return out


class BackupStore:
    def __init__(self, data_dir, clock=time.time, log=None):
        self.data_dir, self.clock = data_dir, clock
        self.log = log or _LOG.info
        self.path = os.path.join(data_dir, DB_NAME)
        self.trash_dir = os.path.join(data_dir, TRASH_DIR)
        self.backup_dir = os.path.join(data_dir, BACKUP_DIR)
        self.degraded_dir = os.path.join(data_dir, DEGRADED_DIR)
        self.degraded_file = os.path.join(self.degraded_dir, DEGRADED_SAMPLES)
        self.db = self.kv = None
        self.degraded = None             # text when the database cannot be used at all
        self.runtime_error = None        # last failed database call while the file was open
        self.import_errors = []
        self.imported = 0                # degraded files moved to the trash by this process
        self.last_check = None           # restore_check of the newest snapshot (counts only)
        self.last_purge_day = None
        self.last_maintenance_error = None
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix='backupdb')
        self._health = None
        self._stop = threading.Event()

    def _call(self, fn, timeout=CALL_TIMEOUT_S):
        return self._ex.submit(fn).result(timeout)

    def open(self):
        """Open, migrate, import. Never raises: failure -> degraded mode."""
        try:
            self._call(self._open)
        except Exception as e:                                   # includes the wait timeout
            self._set_degraded(e)
        return self

    def _set_degraded(self, e):
        self.degraded = _short(e)
        self.db = self.kv = None
        self.log('backup: DATABASE DEGRADED, backup runs continue, samples go to %s: %s' % (self.degraded_dir, self.degraded))

    def _open(self):
        self.db = ihdb.open(self.path, MIGRATIONS, snapshot_dir=self.backup_dir, clock=self.clock)
        self.kv = ihdb.KV(self.db, KV_NAMESPACE)
        self._import_degraded()
        self.maintain()
        if self.imported:
            self._snapshot_and_check()

    def close(self):
        self._stop.set()
        try:
            self._call(lambda: self.db and self.db.close(), HEALTH_TIMEOUT_S)
        except Exception:
            pass
        self._ex.shutdown(wait=False)

    # ---- import of the plain file written while degraded (copy, verify, then trash)
    def _put_samples(self, con, data):
        if not isinstance(data, list):
            raise ValueError('samples file is not a list')
        before = con.execute('SELECT COUNT(*) FROM backup_samples').fetchone()[0]
        wanted = set()
        for row in data:
            if not isinstance(row, dict) or not row.get('ts'):
                raise ValueError('a sample has no ts')
            c = _clean(row)
            wanted.add(c['ts'])
            con.execute('INSERT OR IGNORE INTO backup_samples(%s) VALUES(%s)' % (', '.join(SAMPLE_COLUMNS), ', '.join('?' * len(SAMPLE_COLUMNS))),
                        [c[k] for k in SAMPLE_COLUMNS])
        have = {r[0] for r in con.execute('SELECT ts FROM backup_samples')}
        after = len(have)
        if not wanted <= have or after < before:
            raise ValueError('verify failed: row counts %d -> %d' % (before, after))
        return len(wanted)

    def _import_degraded(self):
        if not os.path.isfile(self.degraded_file):
            return
        try:
            r = ihdb.import_legacy_json(self.db, self.degraded_file, self._put_samples, self.trash_dir, skip_bad_lines=True)
        except ihdb.LegacyImportError as e:
            msg = '%s: %s' % (DEGRADED_SAMPLES, _short(e))
            self.log('backup: import of degraded samples FAILED, file left in place: %s' % msg)
            if msg not in self.import_errors and len(self.import_errors) < MAX_IMPORT_ERRORS:
                self.import_errors.append(msg)
            return
        if r['status'] == 'imported':
            self.imported += 1
            self.log('backup: imported %d degraded sample(s) into the database, file moved to trash' % r['rows'])

    # ---- the job's call: NEVER raises
    def record_run(self, sample, mode='dry'):
        """Store one sample row and the last-run marker. Best effort: any failure is logged and swallowed."""
        try:
            s = _clean(dict(sample, ts=_iso(self.clock()), mode=mode))
            if self.db is not None:
                try:
                    self._call(lambda: self._write_sample(s), RECORD_TIMEOUT_S)
                    return True
                except Exception as e:
                    self._runtime_failed(e)
            self._write_degraded(s)
            return False
        except Exception as e:
            try:
                self.log('backup: could not record the sample (the backup run is not affected): %s' % _short(e))
            except Exception:
                pass
            return False

    def _write_sample(self, s):
        with self.db.tx() as con:
            con.execute('INSERT OR REPLACE INTO backup_samples(%s) VALUES(%s)' % (', '.join(SAMPLE_COLUMNS), ', '.join('?' * len(SAMPLE_COLUMNS))),
                        [s[k] for k in SAMPLE_COLUMNS])
        self.kv.set(LAST_RUN_KEY, {'ts': s['ts'], 'mode': s['mode'], 'disk_known': bool(s['disk_known'])})

    def _write_degraded(self, s):
        os.makedirs(self.degraded_dir, exist_ok=True)
        if os.path.isfile(self.degraded_file) and os.path.getsize(self.degraded_file) >= DEGRADED_MAX_BYTES:
            return
        with open(self.degraded_file, 'a') as f:
            f.write(json.dumps(s) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def _runtime_failed(self, e):
        self.runtime_error = _short(e)
        self.log('backup: DATABASE CALL FAILED, sample goes to plain file in %s instead: %s' % (self.degraded_dir, self.runtime_error))

    def last_run(self):
        """Last-run marker or None (never raises)."""
        if self.db is None:
            return None
        try:
            return self._call(lambda: self.kv.get(LAST_RUN_KEY), HEALTH_TIMEOUT_S)
        except Exception:
            return None

    # ---- nightly work: trash purge, old samples, snapshot, restore check
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
        """Once a day: purge the trash and old samples. Once a night (from SNAPSHOT_HOUR): snapshot + restore check. Never raises."""
        if self.db is None:
            return
        try:
            day = datetime.fromtimestamp(self.clock(), timezone.utc).date()
            if self.last_purge_day != day:
                gone = ihdb.purge_trash(self.trash_dir, clock=self.clock)
                self.db.execute('DELETE FROM backup_samples WHERE ts < ?', (_iso(self.clock() - SAMPLE_KEEP_DAYS * SECONDS_PER_DAY),))
                self.last_purge_day = day
                if gone:
                    self.log('backup: trash purge removed %d old file(s)' % len(gone))
            age = self._snapshot_age_s()
            if local_hour is not None and local_hour >= SNAPSHOT_HOUR and (age is None or age > SNAPSHOT_MIN_AGE_S):
                self._snapshot_and_check()
            self.last_maintenance_error = None
        except Exception as e:
            self.last_maintenance_error = _short(e)
            self.log('backup: nightly maintenance FAILED: %s' % self.last_maintenance_error)

    def _snapshot_and_check(self):
        snap = ihdb.snapshot(self.path, self.backup_dir, clock=self.clock)
        chk = ihdb.restore_check(snap)
        live = self.db.table_counts()
        self.last_check = {'at': ihdb._iso(self.clock()), 'ok': chk['ok'], 'schema_version': chk['schema_version'], 'rows': chk['tables'],
                           'rows_match_live': chk['tables'] == live, 'error': chk['error']}
        self.log('backup: snapshot taken, restore check %s, schema %s, rows %s%s' % (
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
        t = threading.Thread(target=loop, daemon=True, name='backupdb-maint')
        t.start()
        return t

    # ---- status (counts only)
    def _health_now(self):
        return {'schema_version': self.db.schema_version(), 'rows': self.db.table_counts()}

    def health(self):
        h = {'engine': 'sqlite', 'file': DB_NAME, 'schema_version': None, 'expected_schema_version': EXPECTED_SCHEMA, 'rows': {}}
        if self.db is not None:
            try:
                got = self._call(self._health_now, HEALTH_TIMEOUT_S)
                self._health = got
            except Exception as e:
                got = dict(self._health or {})
                h['status_error'] = _short(e)
            h.update(got)
        h['degraded'] = self.degraded is not None
        h['error'] = self.degraded or self.runtime_error
        if self.degraded:
            h['WARNING'] = 'DATABASE NOT IN USE: backup runs are NOT affected; samples are kept in plain files in /data/%s until the database is restored' % DEGRADED_DIR
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
        h['degraded_file_bytes'] = dict(_scan(self.degraded_dir)).get(DEGRADED_SAMPLES, 0)
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
    """The store of this data folder, opened once per process. Never raises."""
    key = os.path.abspath(data_dir)
    with _lock:
        s = _stores.get(key)
        if s is None:
            s = _stores[key] = BackupStore(key, clock=clock, log=log)
            s.open()
        return s


def forget(data_dir):
    """Close and drop the cached store (tests)."""
    with _lock:
        s = _stores.pop(os.path.abspath(data_dir), None)
    if s:
        s.close()


def record(data_dir, sample, mode='dry', log=None):
    """Called by the job after it published its sensors. NEVER raises, whatever the database does."""
    try:
        return get(data_dir, log=log).record_run(sample, mode)
    except Exception:
        return False
