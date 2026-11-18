"""Backup add-on storage: migration, restart, damaged / too-new database, degraded runs, snapshots, trash purge, and the rule that a database
failure never stops a backup-storage run. Temp folders only, fakes only."""
import hashlib, json, os, shutil, sys, tempfile, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import backupdb
from jobs import backupstore
from ihlib import db as ihdb

START = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400
SAMPLE = dict(local_used_gb=40.5, local_backups_gb=7.25, local_count=3, drive_used_gb=5.0, drive_backups_gb=4.5, drive_count=3, disk_known=True)


def put(path, data, mode='w'):
    with open(path, mode) as f:
        f.write(data)


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t


class FakeCtx:
    """What backupstore.run needs: no network, no HA."""
    def __init__(self, data_dir, dry=True, fail_publish=False):
        self.data_dir, self.dry, self.opts, self.logs, self.set = data_dir, dry, {'host_info': False}, [], []
        self.fail_publish = fail_publish
        self.token = 'x'

    def log(self, m):
        self.logs.append(m)

    def set_state(self, entity, state, attrs):
        if self.fail_publish:
            raise RuntimeError('ha down')
        self.set.append((entity, state))


GIB = 1024 ** 3
INFO = lambda ctx: {'backups': [{'agents': {'hassio.local': {'size': 2 * GIB}, 'google_drive.x': {'size': GIB}}},
                                {'agents': {'hassio.local': {'size': GIB}}}]}
DISK = lambda ctx: (100.0, 40.0, 'fake')


class Base(unittest.TestCase):
    def setUp(self):
        self.d, self.clock, self.logs, self.stores = tempfile.mkdtemp(), Clock(), [], []

    def tearDown(self):
        for s in self.stores:
            s.close()
        backupdb.forget(self.d)
        shutil.rmtree(self.d, ignore_errors=True)

    def store(self):
        s = backupdb.BackupStore(self.d, clock=self.clock, log=self.logs.append).open()
        self.stores.append(s)
        return s

    def p(self, *a):
        return os.path.join(self.d, *a)

    def rows(self, path=None):
        con = ihdb.open(path or self.p('ih.db'), backupdb.MIGRATIONS)
        try:
            return con.query('SELECT ts, local_count FROM backup_samples ORDER BY ts')
        finally:
            con.close()


class Basics(Base):
    def test_fresh_database_is_created_and_records(self):
        s = self.store()
        self.assertIsNone(s.degraded)
        self.assertEqual(s.health()['schema_version'], backupdb.EXPECTED_SCHEMA)
        self.assertTrue(s.record_run(SAMPLE, 'live'))
        h = s.health()
        self.assertEqual(h['rows']['backup_samples'], 1)
        self.assertEqual(h['rows']['kv'], 1)
        self.assertEqual(s.last_run()['mode'], 'live')
        self.assertFalse(h['degraded'])
        self.assertEqual(h['legacy_import_errors'], [])

    def test_restart_is_idempotent_and_keeps_rows(self):
        s = self.store()
        s.record_run(SAMPLE)
        s.close()
        s2 = self.store()
        self.assertEqual(s2.health()['rows']['backup_samples'], 1)
        self.assertEqual(s2.imported, 0)
        self.assertEqual(s2.health()['schema_version'], 1)

    def test_same_second_run_replaces_not_duplicates(self):
        s = self.store()
        s.record_run(SAMPLE)
        s.record_run(dict(SAMPLE, local_count=9))
        self.assertEqual(s.health()['rows']['backup_samples'], 1)

    def test_status_has_no_text_only_counts(self):
        h = self.store().health()
        self.assertTrue({'schema_version', 'rows', 'last_snapshot', 'snapshots', 'trash', 'degraded', 'corrupt_copies'} <= set(h))

    def test_unrelated_files_are_left_alone(self):
        for n in ('options.json', 'STOP_LIVE'):
            put(self.p(n), '{}')
        s = self.store()
        s.maintain_now(5)
        self.assertEqual(sorted(x for x in os.listdir(self.d) if x in ('options.json', 'STOP_LIVE')), ['STOP_LIVE', 'options.json'])


class Failure(Base):
    def test_corrupt_db_runs_degraded_and_is_never_recreated(self):
        put(self.p('ih.db'), b'garbage' * 200, 'wb')
        s = self.store()
        h = s.health()
        self.assertTrue(h['degraded'])
        self.assertIn('WARNING', h)
        self.assertIn('backup runs are NOT affected', h['WARNING'])
        self.assertTrue(any('DEGRADED' in m for m in self.logs))
        self.assertFalse(s.record_run(SAMPLE))
        self.assertTrue(os.path.exists(self.p('degraded', 'backup_samples.jsonl')))
        self.assertFalse(os.path.exists(self.p('ih.db')))
        self.assertEqual(len(ihdb.quarantined(self.p('ih.db'))), 1)
        s2 = self.store()
        self.assertTrue(s2.degraded)
        self.assertFalse(os.path.exists(self.p('ih.db')))

    def test_too_new_schema_runs_degraded_and_file_untouched(self):
        ihdb.open(self.p('ih.db'), backupdb.MIGRATIONS + [(2, 'CREATE TABLE future(x)')]).close()
        digest = sha(self.p('ih.db'))
        s = self.store()
        self.assertIn('SchemaTooNewError', s.health()['error'])
        s.record_run(SAMPLE)
        s.close()
        for ext in ('-wal', '-shm'):
            if os.path.exists(self.p('ih.db' + ext)):
                os.unlink(self.p('ih.db' + ext))
        self.assertEqual(sha(self.p('ih.db')), digest)

    def test_runtime_failure_falls_back_to_file(self):
        s = self.store()
        s._write_sample = lambda smp: (_ for _ in ()).throw(ihdb.DBError('disk full'))
        self.assertFalse(s.record_run(SAMPLE))
        self.assertTrue(os.path.exists(self.p('degraded', 'backup_samples.jsonl')))
        self.assertIn('disk full', s.health()['error'])

    def test_record_never_raises_even_when_nothing_works(self):
        s = self.store()
        s._write_sample = lambda smp: 1 / 0
        s._write_degraded = lambda smp: (_ for _ in ()).throw(OSError('read-only disk'))
        self.assertFalse(s.record_run(SAMPLE))                        # swallowed
        self.assertTrue(any('not affected' in m for m in self.logs))

    def test_degraded_file_is_bounded(self):
        put(self.p('ih.db'), b'garbage' * 200, 'wb')
        s = self.store()
        s._write_degraded = backupdb.BackupStore._write_degraded.__get__(s)
        old = backupdb.DEGRADED_MAX_BYTES
        backupdb.DEGRADED_MAX_BYTES = 500
        try:
            for i in range(50):
                self.clock.t += 1
                s.record_run(SAMPLE)
        finally:
            backupdb.DEGRADED_MAX_BYTES = old
        self.assertLess(os.path.getsize(self.p('degraded', 'backup_samples.jsonl')), 1500)

    def test_degraded_samples_import_after_restore(self):
        s = self.store()
        s.record_run(SAMPLE)
        s.maintain_now(4)
        s.close()
        snap = self.p('backup', os.listdir(self.p('backup'))[0])
        put(self.p('ih.db'), b'not a database at all' * 50, 'wb')
        for ext in ('-wal', '-shm'):
            if os.path.exists(self.p('ih.db' + ext)):
                os.unlink(self.p('ih.db' + ext))
        s = self.store()
        self.assertIsNotNone(s.degraded)
        self.clock.t += DAY
        s.record_run(dict(SAMPLE, local_count=5))
        s.close()
        shutil.copy(snap, self.p('ih.db'))                            # the restore recipe in DOCS.md
        s = self.store()
        self.assertIsNone(s.degraded)
        self.assertEqual([r[1] for r in self.rows()], [3, 5])
        self.assertFalse(os.path.exists(self.p('degraded', 'backup_samples.jsonl')))
        self.assertEqual(s.imported, 1)
        self.assertIn('backup_samples.jsonl', os.listdir(self.p('trash')))

    def test_bad_degraded_file_stays_and_is_reported(self):
        os.makedirs(self.p('degraded'))
        put(self.p('degraded', 'backup_samples.jsonl'), '{"ts": "2026-11-01T00:00:00+00:00", "local_count": 1}\n{"no_ts": 1}\n')
        s = self.store()
        self.assertTrue(os.path.exists(self.p('degraded', 'backup_samples.jsonl')))
        self.assertEqual(len(s.health()['legacy_import_errors']), 1)
        self.assertEqual(s.health()['rows']['backup_samples'], 0)
        self.assertIsNone(s.degraded)


class JobNeverStops(Base):
    def run_job(self, **kw):
        ctx = FakeCtx(self.d, **kw)
        backupstore.run(ctx, info=INFO, disk_fn=DISK)
        return ctx

    def test_normal_run_publishes_and_records(self):
        backupdb.get(self.d, clock=self.clock, log=self.logs.append)
        ctx = self.run_job()
        self.assertEqual([e for e, _ in ctx.set], ['sensor.backup_storage_local'])
        s = backupdb.get(self.d)
        self.assertEqual(s.health()['rows']['backup_samples'], 1)

    def test_corrupt_database_run_still_publishes(self):
        put(self.p('ih.db'), b'garbage' * 200, 'wb')
        backupdb.get(self.d, clock=self.clock, log=self.logs.append)
        ctx = self.run_job()
        self.assertEqual([e for e, _ in ctx.set], ['sensor.backup_storage_local'])
        self.assertTrue(os.path.exists(self.p('degraded', 'backup_samples.jsonl')))

    def test_hung_database_does_not_hold_the_run(self):
        s = backupdb.get(self.d, clock=self.clock, log=self.logs.append)
        old = backupdb.RECORD_TIMEOUT_S
        backupdb.RECORD_TIMEOUT_S = 0.2
        import threading, time
        gate = threading.Event()
        s._ex.submit(gate.wait)                                       # the one worker is busy
        try:
            t0 = time.time()
            ctx = self.run_job()
            self.assertLess(time.time() - t0, 3)
            self.assertEqual([e for e, _ in ctx.set], ['sensor.backup_storage_local'])
        finally:
            gate.set()
            backupdb.RECORD_TIMEOUT_S = old

    def test_record_exploding_inside_job_is_swallowed(self):
        orig = backupdb.record
        backupdb.record = lambda *a, **k: 1 / 0
        try:
            ctx = self.run_job()
        finally:
            backupdb.record = orig
        self.assertEqual([e for e, _ in ctx.set], ['sensor.backup_storage_local'])

    def test_unwritable_data_dir_run_still_publishes(self):
        ctx = FakeCtx('/proc/nonexistent/data')
        self.assertTrue(backupstore.run(ctx, info=INFO, disk_fn=DISK))
        self.assertEqual(len(ctx.set), 1)
        backupdb.forget('/proc/nonexistent/data')


class Nightly(Base):
    def test_snapshot_once_a_night_keep_three_and_restore_check(self):
        s = self.store()
        s.record_run(SAMPLE)
        s.maintain_now(3)                                             # before the hour: nothing
        self.assertFalse(os.path.isdir(self.p('backup')) and os.listdir(self.p('backup')))
        for i in range(5):
            self.clock.t += DAY
            s.record_run(SAMPLE)
            s.maintain_now(4)
            s.maintain_now(5)                                         # same night: no second snapshot
        self.assertEqual(len(os.listdir(self.p('backup'))), ihdb.SNAPSHOT_KEEP)
        h = s.health()
        self.assertTrue(h['last_restore_check']['ok'])
        self.assertTrue(h['last_restore_check']['rows_match_live'])
        self.assertEqual(h['snapshots'], ihdb.SNAPSHOT_KEEP)
        snap = self.p('backup', sorted(os.listdir(self.p('backup')))[-1])
        self.assertEqual(len(self.rows(snap)), 6)                     # a snapshot restores as a working database

    def test_old_samples_are_purged(self):
        s = self.store()
        s.record_run(SAMPLE)
        self.clock.t += (backupdb.SAMPLE_KEEP_DAYS + 2) * DAY
        s.record_run(SAMPLE)
        s.maintain_now(5)
        self.assertEqual(s.health()['rows']['backup_samples'], 1)

    def test_trash_purge_after_30_days(self):
        os.makedirs(self.p('degraded'))
        put(self.p('degraded', 'backup_samples.jsonl'), json.dumps(dict(SAMPLE, ts='2026-11-01T00:00:00+00:00')) + '\n')
        s = self.store()
        self.assertEqual(os.listdir(self.p('trash')), ['backup_samples.jsonl'])
        self.clock.t += (ihdb.TRASH_KEEP_DAYS - 1) * DAY
        s.maintain_now(5)
        self.assertEqual(len(os.listdir(self.p('trash'))), 1)
        self.clock.t += 2 * DAY
        s.maintain_now(5)
        self.assertEqual(os.listdir(self.p('trash')), [])

    def test_maintenance_failure_is_reported_not_raised(self):
        s = self.store()
        orig = ihdb.snapshot
        ihdb.snapshot = lambda *a, **k: (_ for _ in ()).throw(OSError('no space'))
        try:
            s.maintain_now(5)
        finally:
            ihdb.snapshot = orig
        self.assertIn('no space', s.health()['maintenance_error'])


if __name__ == '__main__':
    unittest.main()
