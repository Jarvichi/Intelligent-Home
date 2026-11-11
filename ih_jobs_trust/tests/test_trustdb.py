"""AI trust test storage: legacy import (copy, verify, trash), restart, damaged and too-new files, snapshots, trash purge, status. Temp folders only."""
import hashlib, json, os, shutil, sys, tempfile, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import trustdb
from ihlib import db as ihdb

START = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t


def result(month, finished=None):
    ag = {q: {'ok': True, 'agent': 'ih_ai_layer_groq', 'chars': 12, 'took_ms': 30, 'score': 8, 'note': 'ok'} for q in 'abcdefgh'}
    return {'month': month, 'format': 2, 'dry': True, 'finished': finished or month + '-01T03:05:00+00:00', 'agents_seen': ['ih_ai_layer_groq'],
            'results': {'ih_ai_layer_groq': ag}, 'summary': {'test': 80, 'scored': 8, 'unscored': 0, 'agents': {'ih_ai_layer_groq': {'test': 80}}}}


STATE = {'month': '2026-10', 'done': True, 'todo_added': False, 'summary': {'test': 80}}


def put(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj, indent=1))


class Base(unittest.TestCase):
    def setUp(self):
        self.d, self.clock, self.logs, self.stores = tempfile.mkdtemp(), Clock(), [], []

    def tearDown(self):
        for s in self.stores:
            s.close()
        shutil.rmtree(self.d, ignore_errors=True)

    def store(self):
        s = trustdb.TrustStore(self.d, clock=self.clock, log=self.logs.append).open()
        self.stores.append(s)
        return s

    def p(self, *a):
        return os.path.join(self.d, *a)

    def legacy(self):
        put(self.p('trust_state.json'), STATE)
        put(self.p('trust_runs', '2026-09.json'), result('2026-09'))
        put(self.p('trust_runs', '2026-10.json'), result('2026-10'))


class Import(Base):
    def test_real_shaped_files_move_to_trash_after_verify(self):
        self.legacy()
        s = self.store()
        self.assertIsNone(s.degraded)
        self.assertEqual(s.state(), STATE)
        self.assertEqual(s.get_run('2026-09'), result('2026-09'))
        self.assertEqual(s.health()['rows']['trust_runs'], 2)
        self.assertFalse(os.path.exists(self.p('trust_state.json')))
        self.assertEqual(sorted(os.listdir(self.p('trash'))), ['2026-09.json', '2026-10.json', 'trust_state.json'])
        self.assertEqual(s.imported, {'state': 1, 'runs': 2})
        self.assertEqual(len(os.listdir(self.p('backup'))), 1)       # safety snapshot right after the move
        self.assertTrue(s.last_check['ok'])

    def test_restart_is_idempotent(self):
        self.legacy()
        self.store().close()
        before = sorted(os.listdir(self.p('trash')))
        s = self.store()
        self.assertEqual(s.imported, {})
        self.assertEqual(sorted(os.listdir(self.p('trash'))), before)
        self.assertEqual(s.health()['rows']['trust_runs'], 2)
        self.assertEqual(s.state(), STATE)

    def test_file_that_reappears_is_trashed_not_reimported(self):
        self.legacy()
        self.store().close()
        put(self.p('trust_state.json'), {'month': '2000-01', 'done': True})
        s = self.store()
        self.assertEqual(s.state(), STATE)                           # the database stays the truth
        self.assertFalse(os.path.exists(self.p('trust_state.json')))

    def test_bad_file_stays_and_is_reported(self):
        self.legacy()
        put(self.p('trust_runs', '2026-11.json'), '{not json')
        s = self.store()
        self.assertTrue(os.path.exists(self.p('trust_runs', '2026-11.json')))
        self.assertEqual(s.health()['rows']['trust_runs'], 2)
        self.assertEqual(len(s.health()['legacy_import_errors']), 1)
        self.assertIsNone(s.degraded)

    def test_failed_verify_leaves_file_and_rows(self):
        self.legacy()
        orig = ihdb.KV.set
        ihdb.KV.set = lambda self, name, obj: None                    # the copy "silently fails": verify must catch it
        try:
            s = self.store()
        finally:
            ihdb.KV.set = orig
        self.assertTrue(os.path.exists(self.p('trust_state.json')))
        self.assertIn('verify failed', ' '.join(s.import_errors))
        self.assertEqual(s.state(), STATE)                            # no row yet, so it reads the old file: nothing lost

    def test_degraded_files_import_after_restore(self):
        s = self.store()
        s.save_run('2026-10', result('2026-10'))
        s.maintain_now(4)
        s.close()
        good = os.listdir(self.p('backup'))
        self.assertEqual(len(good), 1)
        snap = self.p('backup', good[0])
        with open(self.p('ih.db'), 'wb') as f:
            f.write(b'not a database at all' * 50)
        for ext in ('-wal', '-shm'):
            if os.path.exists(self.p('ih.db' + ext)):
                os.unlink(self.p('ih.db' + ext))
        s = self.store()
        self.assertIsNotNone(s.degraded)
        s.save_run('2026-11', result('2026-11'))
        s.save_state({'month': '2026-11', 'done': True})
        s.close()
        shutil.copy(snap, self.p('ih.db'))                            # the restore recipe from DOCS.md
        s = self.store()
        self.assertIsNone(s.degraded)
        self.assertEqual(s.get_run('2026-11'), result('2026-11'))
        self.assertEqual(s.get_run('2026-10'), result('2026-10'))
        self.assertEqual(s.state()['month'], '2026-11')
        self.assertFalse(os.path.exists(self.p('degraded', 'trust_runs', '2026-11.json')))


class Failure(Base):
    def test_corrupt_db_runs_degraded_and_is_never_recreated(self):
        self.legacy()
        with open(self.p('ih.db'), 'wb') as f:
            f.write(b'garbage' * 200)
        s = self.store()
        h = s.health()
        self.assertTrue(h['degraded'])
        self.assertIn('WARNING', h)
        self.assertIn('CorruptError', h['error'])
        self.assertTrue(any('DEGRADED' in m for m in self.logs))
        self.assertEqual(s.state(), STATE)                            # still reads the old files, which were not touched
        self.assertTrue(os.path.exists(self.p('trust_state.json')))
        s.save_state({'month': '2026-11', 'done': True})
        s.save_run('2026-11', result('2026-11'))
        self.assertEqual(s.state()['month'], '2026-11')
        self.assertEqual(s.get_run('2026-11'), result('2026-11'))
        self.assertEqual(trustdb.read_latest(self.d)['month'], '2026-11')
        self.assertFalse(os.path.exists(self.p('ih.db')))
        self.assertEqual(len(ihdb.quarantined(self.p('ih.db'))), 1)
        s2 = self.store()                                             # a second start does not make a fresh database either
        self.assertTrue(s2.degraded)
        self.assertFalse(os.path.exists(self.p('ih.db')))
        self.assertEqual(s2.state()['month'], '2026-11')

    def test_too_new_schema_runs_degraded_and_file_untouched(self):
        d = ihdb.open(self.p('ih.db'), trustdb.MIGRATIONS + [(2, 'CREATE TABLE future(x)')])
        d.close()
        digest = hashlib.sha256(open(self.p('ih.db'), 'rb').read()).hexdigest()
        s = self.store()
        self.assertIn('SchemaTooNewError', s.health()['error'])
        self.assertTrue(s.health()['degraded'])
        s.save_run('2026-11', result('2026-11'))
        s.close()
        for ext in ('-wal', '-shm'):
            if os.path.exists(self.p('ih.db' + ext)):
                os.unlink(self.p('ih.db' + ext))
        self.assertEqual(hashlib.sha256(open(self.p('ih.db'), 'rb').read()).hexdigest(), digest)

    def test_runtime_write_failure_falls_back_to_files(self):
        s = self.store()
        s.db.execute = lambda *a, **k: (_ for _ in ()).throw(ihdb.DBError('disk full'))
        s.save_run('2026-11', result('2026-11'))
        self.assertTrue(os.path.exists(self.p('degraded', 'trust_runs', '2026-11.json')))
        self.assertIn('disk full', s.health()['error'])


class Nightly(Base):
    def test_snapshot_once_a_night_keep_three_and_restore_check(self):
        self.legacy()
        s = self.store()
        s.maintain_now(3)                                             # before the hour: nothing new
        n0 = len(os.listdir(self.p('backup')))
        s.clock = self.clock
        for i in range(5):
            self.clock.t += DAY
            s.maintain_now(4)
            s.maintain_now(5)                                         # same night: no second snapshot
        names = sorted(os.listdir(self.p('backup')))
        self.assertEqual(n0, 1)
        self.assertEqual(len(names), ihdb.SNAPSHOT_KEEP)
        h = s.health()
        self.assertEqual(h['snapshots'], 3)
        self.assertTrue(h['last_restore_check']['ok'])
        self.assertTrue(h['last_restore_check']['rows_match_live'])
        self.assertEqual(h['last_restore_check']['rows']['trust_runs'], 2)
        self.assertTrue(any('restore check OK' in m and 'trust_runs' in m for m in self.logs))
        self.assertEqual(ihdb.restore_check(self.p('backup', names[-1]))['tables']['trust_runs'], 2)

    def test_trash_purged_after_30_days_with_fake_clock(self):
        self.legacy()
        s = self.store()
        self.assertEqual(len(os.listdir(self.p('trash'))), 3)
        self.clock.t += 29 * DAY
        s.maintain_now(5)
        self.assertEqual(len(os.listdir(self.p('trash'))), 3)
        self.clock.t += 2 * DAY
        s.maintain_now(5)
        self.assertEqual(os.listdir(self.p('trash')), [])
        self.assertEqual(s.health()['trash'], {'files': 0, 'bytes': 0})

    def test_status_has_db_health_counts_only(self):
        self.legacy()
        s = self.store()
        h = s.health()
        for k in ('schema_version', 'rows', 'last_snapshot', 'trash', 'degraded', 'last_restore_check', 'snapshots'):
            self.assertIn(k, h)
        self.assertEqual(h['schema_version'], trustdb.EXPECTED_SCHEMA)
        self.assertEqual(h['trash']['files'], 3)
        self.assertGreater(h['trash']['bytes'], 0)
        self.assertIsNotNone(h['last_snapshot'])
        self.assertNotIn('results', json.dumps(h))


class Reader(Base):
    def test_read_latest_prefers_newest_and_reads_files(self):
        self.assertIsNone(trustdb.read_latest(self.d))
        put(self.p('trust_runs', '2026-08.json'), result('2026-08'))
        self.assertEqual(trustdb.read_latest(self.d)['month'], '2026-08')
        s = self.store()                                              # moves the old file into the database
        self.assertFalse(os.path.exists(self.p('trust_runs', '2026-08.json')))
        s.save_run('2026-10', result('2026-10'))
        self.assertEqual(trustdb.read_latest(self.d)['month'], '2026-10')

    def test_status_wrapper_adds_db(self):
        import main, jobs_child
        orig, old_data = jobs_child.Child.status, main.DATA
        main.DATA = self.d
        try:
            main._with_db_status()
            s = jobs_child.Child({'job': 'trust', 'source': 'x', 'version': '1', 'port': 1, 'slug': 'x', 'title': 't', 'runner': lambda c: True},
                                 {'mode': 'dry'}, data=self.d).status()
            self.assertIn('db', s)
            self.assertFalse(s['db_degraded'])
        finally:
            jobs_child.Child.status = orig
            main.DATA = old_data
            trustdb.forget(self.d)


if __name__ == '__main__':
    unittest.main()
