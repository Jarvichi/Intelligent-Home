"""Work queue database storage: legacy import (copy, verify, trash), restart, damaged and too-new files, degraded run, snapshots, trash purge,
status block, runtime failure, state tables. Temp folders only."""
import glob, hashlib, json, os, shutil, sqlite3, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import workdb
from store import FileStore, StoreError
from ihlib import db as ihdb

T0 = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def move(self, **kw):
        self.t += timedelta(**kw)


def item(seq, iid, status='queued', **k):
    d = dict(id=iid, seq=seq, kind='todo', ref='uid-%d' % seq, title='Job %d' % seq, instruction='Work on this item. Read-only.', approved=False,
             status=status, queued_at='2026-11-08T10:00:00+00:00', claimed_by=None, lease_expires_at=None, heartbeat_at=None, started_at=None,
             finished_at=None, attempts=0, result_summary=None, error=None, by='claude')
    d.update(k)
    return d


STATE = {'mode': 'draining', 'band': 'night', 'mode_since': '2026-11-08T23:00:00+00:00',
         'claims': ['2026-11-12T05:30:00+00:00', '2026-11-12T05:40:00+00:00'], 'dry_claims': ['2026-11-07T01:00:00+00:00'],
         'urgent_claims': ['2026-11-12T05:00:00+00:00'], 'fed': {'uid-a': '2026-11-10T01:00:00+00:00'}, 'fed_dry': {'uid-b': '2026-11-09T01:00:00+00:00'},
         'last_feed_check': '2026-11-12T05:55:00+00:00',
         'snapshot': {'worker': {'state': 'idle', 'reason': 'queue empty', 'band': 'night'}, 'gate': 'queue gate: ok', 'pace': -6.0, 'at': '2026-11-12T05:59:00+00:00'}}


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
        s = workdb.WorkStore(self.d, clock=self.clock, log=self.logs.append).open()
        self.stores.append(s)
        return s

    def p(self, *a):
        return os.path.join(self.d, *a)

    def legacy(self):
        put(self.p('items', '000001-aaaa0001.json'), item(1, 'aaaa0001', 'done', finished_at='2026-11-08T11:00:00+00:00', result_summary='ok'))
        put(self.p('items', '000002-aaaa0002.json'), item(2, 'aaaa0002', 'failed', finished_at='2026-11-08T12:00:00+00:00', error='boom', attempts=3))
        put(self.p('items', '000005-aaaa0005.json'), item(5, 'aaaa0005', 'queued', approved=True, by='owner'))
        put(self.p('items', '000006-aaaa0006.json'), item(6, 'aaaa0006', 'running', claimed_by='runner-1', started_at=self.clock().isoformat(),
                                                             heartbeat_at=self.clock().isoformat(), lease_expires_at=(self.clock() + timedelta(minutes=20)).isoformat()))
        put(self.p('seq.counter'), '6')
        put(self.p('state.json'), STATE)
        put(self.p('.lock'), '')


class Import(Base):
    def test_real_shaped_files_move_to_trash_after_verify(self):
        self.legacy()
        s = self.store()
        self.assertIsNone(s.degraded)
        items = s.list()
        self.assertEqual([i['seq'] for i in items], [1, 2, 5, 6])
        self.assertEqual(items[2]['approved'], True)
        self.assertEqual(items[1]['error'], 'boom')
        self.assertEqual(s.load_state(), STATE)
        self.assertEqual(s.imported, {'items': 4, 'counter': 1, 'state': 1})
        self.assertFalse(os.path.exists(self.p('items', '000001-aaaa0001.json')))
        self.assertFalse(os.path.exists(self.p('.lock')))
        self.assertEqual(len(os.listdir(self.p('trash'))), 6)
        self.assertEqual(len(os.listdir(self.p('backup'))), 1)
        self.assertTrue(s.last_check['ok'])
        self.assertEqual(s.enqueue('other', 'new', 'T', 'I')['seq'], 7)      # counter carried over
        h = s.health()
        self.assertEqual((h['rows']['work_items'], h['rows']['work_claims'], h['rows']['work_fed']), (5, 4, 2))

    def test_restart_is_idempotent(self):
        self.legacy()
        self.store().close()
        before = sorted(os.listdir(self.p('trash')))
        s = self.store()
        self.assertEqual(s.imported, {})
        self.assertEqual(sorted(os.listdir(self.p('trash'))), before)
        self.assertEqual(len(s.list()), 4)
        self.assertEqual(s.load_state(), STATE)

    def test_file_that_reappears_is_trashed_not_reimported(self):
        self.legacy()
        self.store().close()
        put(self.p('items', '000001-aaaa0001.json'), item(1, 'aaaa0001', 'queued', title='CHANGED'))
        put(self.p('state.json'), {'mode': 'idle'})
        s = self.store()
        self.assertEqual(s.get('aaaa0001')['title'], 'Job 1')               # the database stays the truth
        self.assertEqual(s.load_state()['mode'], 'draining')
        self.assertFalse(os.path.exists(self.p('state.json')))

    def test_bad_files_stay_and_are_reported(self):
        self.legacy()
        put(self.p('items', '000099-bad.json'), '{nope')
        put(self.p('items', '000098-odd.json'), {'id': 'x', 'seq': 98, 'status': 'weird'})
        put(self.p('items', '000007-dup.json'), item(5, 'dup00001'))             # same seq as another item
        s = self.store()
        self.assertTrue(os.path.exists(self.p('items', '000099-bad.json')))
        self.assertEqual(sorted(s.corrupt), ['items/000007-dup.json', 'items/000098-odd.json', 'items/000099-bad.json'])
        self.assertEqual(len(s.list()), 4)
        self.assertIsNone(s.degraded)
        self.assertEqual(s.health()['legacy_import_errors'], s.corrupt)

    def test_failed_verify_leaves_files_and_rows(self):
        self.legacy()
        orig = ihdb.KV.set
        ihdb.KV.set = lambda self, name, obj: None                            # the copy "silently fails": verify must catch it
        try:
            s = self.store()
        finally:
            ihdb.KV.set = orig
        self.assertTrue(os.path.exists(self.p('state.json')))
        self.assertTrue(os.path.exists(self.p('seq.counter')))
        self.assertIn('state.json', s.corrupt)

    def test_state_import_with_odd_values_is_refused_not_lost(self):
        put(self.p('state.json'), {'claims': 'not a list'})
        s = self.store()
        self.assertTrue(os.path.exists(self.p('state.json')))
        self.assertEqual(s.corrupt, ['state.json'])


class StateTables(Base):
    def test_state_roundtrip_and_empty_lists_dropped(self):
        s = self.store()
        s.save_state({'mode': 'idle', 'claims': ['2026-11-12T01:00:00+00:00'], 'fed': {'u': 't'}})
        self.assertEqual(s.load_state(), {'mode': 'idle', 'claims': ['2026-11-12T01:00:00+00:00'], 'fed': {'u': 't'}})
        s.save_state({'mode': 'idle', 'claims': [], 'fed': {}})
        self.assertEqual(s.load_state(), {'mode': 'idle'})
        self.assertEqual(s.health()['rows']['work_claims'], 0)


class Degraded(Base):
    def digest(self):
        with open(self.p('ih.db'), 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()

    def corrupt_db(self):
        with open(self.p('ih.db'), 'wb') as f:
            f.write(b'garbage' * 200)

    def test_corrupt_db_runs_degraded_queue_works_and_is_never_recreated(self):
        self.legacy()
        self.corrupt_db()
        s = self.store()
        h = s.health()
        self.assertTrue(h['degraded'])
        self.assertIn('WARNING', h)
        self.assertIn('CorruptError', h['error'])
        self.assertTrue(any('DEGRADED' in m for m in self.logs))
        self.assertTrue(os.path.exists(self.p('items', '000005-aaaa0005.json')))      # originals untouched
        self.assertEqual([i['seq'] for i in s.list()], [1, 2, 5, 6])                   # copied into degraded
        a = s.enqueue('other', 'x', 'T', 'I')
        self.assertEqual(a['seq'], 7)
        self.clock.move(minutes=30)                                                # the running item's lease from the old file runs out
        self.assertEqual(s.get('aaaa0006')['status'], 'queued')
        self.assertEqual(s.claim('w', 60, iid=a['id'])['id'], a['id'])
        self.assertEqual(s.finish(a['id'], 'w', True, 'ok')['status'], 'done')
        s.save_state({'mode': 'idle'})
        self.assertEqual(s.load_state(), {'mode': 'idle'})
        self.assertTrue(glob.glob(self.p('degraded', 'items', '*.json')))
        self.assertFalse(os.path.exists(self.p('ih.db')))
        self.assertEqual(len(ihdb.quarantined(self.p('ih.db'))), 1)
        s2 = self.store()                                                          # second start: still degraded, still no fresh database
        self.assertTrue(s2.degraded)
        self.assertFalse(os.path.exists(self.p('ih.db')))
        self.assertEqual(s2.get(a['id'])['status'], 'done')
        self.assertEqual(s2.corrupt, [])

    def test_too_new_schema_runs_degraded_and_file_untouched(self):
        d = ihdb.open_db(self.p('ih.db'), workdb.MIGRATIONS + [(2, 'CREATE TABLE future(x)')])
        d.close()
        digest = self.digest()
        s = self.store()
        self.assertIn('SchemaTooNewError', s.health()['error'])
        s.enqueue('other', 'x', 'T', 'I')
        s.close()
        for ext in ('-wal', '-shm'):
            if os.path.exists(self.p('ih.db' + ext)):
                os.unlink(self.p('ih.db' + ext))
        self.assertEqual(self.digest(), digest)

    def test_degraded_files_import_after_restore(self):
        s = self.store()
        old = s.enqueue('other', 'keep', 'Kept', 'I')
        s.maintain_now(4)
        s.close()
        snap = self.p('backup', os.listdir(self.p('backup'))[0])
        self.corrupt_db()
        for ext in ('-wal', '-shm'):
            if os.path.exists(self.p('ih.db' + ext)):
                os.unlink(self.p('ih.db' + ext))
        s = self.store()
        self.assertIsNotNone(s.degraded)
        new = s.enqueue('other', 'during', 'During outage', 'I')
        s.save_state({'mode': 'draining'})
        s.close()
        shutil.copy(snap, self.p('ih.db'))                                         # the restore recipe from DOCS.md
        s = self.store()
        self.assertIsNone(s.degraded)
        self.assertEqual(sorted(i['title'] for i in s.list()), ['During outage', 'Kept'])
        self.assertEqual(s.get(new['id'])['seq'], new['seq'] if new['seq'] > old['seq'] else old['seq'] + 1)
        self.assertEqual(s.load_state()['mode'], 'draining')
        self.assertEqual(glob.glob(self.p('degraded', 'items', '*.json')), [])
        self.assertEqual(len({i['seq'] for i in s.list()}), 2)

    def test_runtime_failure_is_an_error_not_a_split_queue(self):
        s = self.store()
        a = s.enqueue('other', 'x', 'T', 'I')
        orig = s.db.tx
        s.db.tx = lambda: (_ for _ in ()).throw(ihdb.DBError('disk full'))
        with self.assertRaises(ihdb.DBError):
            s.enqueue('other', 'y', 'T', 'I')
        self.assertIn('disk full', s.health()['error'])
        s.db.tx = orig
        self.assertEqual([i['id'] for i in s.list()], [a['id']])                   # nothing leaked into files
        self.assertFalse(os.path.exists(self.p('degraded')))
        self.assertIsNone(s.health()['error'])


class Nightly(Base):
    def test_snapshot_once_a_night_keep_three_and_restore_check(self):
        self.legacy()
        s = self.store()
        s.maintain_now(3)
        self.assertEqual(len(os.listdir(self.p('backup'))), 1)
        for i in range(5):
            self.clock.move(days=1)
            s.maintain_now(4)
            s.maintain_now(5)
        names = sorted(os.listdir(self.p('backup')))
        self.assertEqual(len(names), ihdb.SNAPSHOT_KEEP)
        h = s.health()
        self.assertTrue(h['last_restore_check']['ok'])
        self.assertTrue(h['last_restore_check']['rows_match_live'])
        self.assertEqual(h['last_restore_check']['rows']['work_items'], 4)
        chk = ihdb.restore_check(self.p('backup', names[-1]))
        self.assertEqual(chk['tables']['work_items'], 4)

    def test_snapshot_restores_a_working_queue(self):
        s = self.store()
        a = s.enqueue('other', 'x', 'T', 'I')
        s.maintain_now(4)
        s.close()
        snap = self.p('backup', os.listdir(self.p('backup'))[0])
        os.unlink(self.p('ih.db'))
        for ext in ('-wal', '-shm'):
            if os.path.exists(self.p('ih.db' + ext)):
                os.unlink(self.p('ih.db' + ext))
        shutil.copy(snap, self.p('ih.db'))
        s = self.store()
        self.assertEqual(s.get(a['id'])['title'], 'T')

    def test_trash_purged_after_30_days_with_fake_clock(self):
        self.legacy()
        s = self.store()
        self.assertEqual(len(os.listdir(self.p('trash'))), 6)
        self.clock.move(days=29)
        s.maintain_now(5)
        self.assertEqual(len(os.listdir(self.p('trash'))), 6)
        self.clock.move(days=2)
        s.maintain_now(5)
        self.assertEqual(os.listdir(self.p('trash')), [])
        self.assertEqual(s.health()['trash'], {'files': 0, 'bytes': 0})

    def test_status_has_db_health_counts_only(self):
        self.legacy()
        s = self.store()
        h = s.health()
        for k in ('schema_version', 'rows', 'last_snapshot', 'trash', 'degraded', 'last_restore_check', 'snapshots'):
            self.assertIn(k, h)
        self.assertEqual(h['schema_version'], workdb.EXPECTED_SCHEMA)
        self.assertEqual(h['trash']['files'], 6)
        blob = json.dumps(h)
        for secret in ('Job 1', 'Work on this item', 'boom', 'runner-1'):
            self.assertNotIn(secret, blob)

    def test_status_via_service_has_db(self):
        import service, gate
        svc = service.Service(self.d, dict(mode='dry', lease_s=60, stop=False, bands=gate.DEFAULT_BANDS, tz=timezone.utc), None, None, self.clock)
        self.stores.append(svc.store)
        st = svc.status()
        self.assertFalse(st['db_degraded'])
        self.assertEqual(st['db']['schema_version'], 1)


class Reader(Base):
    def test_peek_reads_db_read_only_and_old_files(self):
        self.assertEqual(workdb.peek(self.d), ([], None))
        put(self.p('items', '000001-aaaa0001.json'), item(1, 'aaaa0001'))
        self.assertEqual([i['id'] for i in workdb.peek(self.d)[0]], ['aaaa0001'])
        s = self.store()
        s.enqueue('other', 'n', 'N', 'I')
        s.save_state({'snapshot': {'at': 'x'}})
        items, snap = workdb.peek(self.d)
        self.assertEqual(len(items), 2)
        self.assertEqual(snap, {'at': 'x'})


if __name__ == '__main__':
    unittest.main()
