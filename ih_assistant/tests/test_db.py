import calendar, glob, json, os, shutil, sqlite3, sys, tempfile, threading, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app', 'ihlib', '..'))
import assistantdb
from ihlib import db as ihdb
from store import PersonaStore, OUTBOX_KEEP

MARKER = 'zzmarker-text-1234'          # stands for message text: must never appear in status
OLD_NIGHT = 40 * 24 * 3600
THREADS = 8
LOOPS = 25


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.now = [1_800_000_000.0]
        self.clock = lambda: self.now[0]

    def tearDown(self):
        assistantdb.forget(self.d)
        self.tmp.cleanup()

    def legacy(self, key='aa', state=None, profile=None, outbox=None, inbox=None):
        base = os.path.join(self.d, 'personas', key)
        if state is not None:
            write(os.path.join(base, 'state.json'), json.dumps(state))
        if profile is not None:
            write(os.path.join(base, 'profile.json'), json.dumps(profile))
        if outbox is not None:
            write(os.path.join(base, 'outbox.jsonl'), '\n'.join(outbox) + '\n')
        if inbox is not None:
            write(os.path.join(base, 'inbox.jsonl'), '\n'.join(inbox) + '\n')
        return base

    def store(self, key='aa'):
        assistantdb.get(self.d, clock=self.clock)          # the test clock drives snapshot names and the trash age
        return PersonaStore(self.d, key, self.clock)

    def restart(self):
        assistantdb.forget(self.d)

    def real_state(self):
        return {'next_id': 7, 'dm_channel': 'c1', 'dates_done': '2026-11-10', 'last_seen': '55',
                'reminders': [{'id': 'r1', 'due': '2026-12-01 09:00', 'text': 'alpha note', 'repeat': 'none', 'status': 'pending'}],
                'plans': {'2026-11-11': {'slots': [{'time': '10:00', 'kind': 'random'}], 'tried': 1.5}},
                'usage_own': {'texts': ['sample one {time}'], 'notime': ['sample two']}, 'name_changes': [1.0, 2.0]}


class Migration(Base):
    def test_legacy_files_are_imported_verified_and_trashed(self):
        st = self.real_state()
        ob = [json.dumps({'ts': '2026-11-10T10:00:00Z', 'kind': 'reply', 'chars': 5, 'mode': 'live'}),
              'not json at all',
              json.dumps({'ts': '2026-11-10T11:00:00Z', 'kind': 'checkin', 'chars': 9})]
        inbox_line = json.dumps({'id': 1, 'type': 'message', 'text': MARKER})
        base = self.legacy(state=st, profile={'name': 'x1', 'notes': ['n']}, outbox=ob, inbox=[inbox_line])
        s = self.store()
        self.assertEqual(s.state(), st)
        self.assertEqual(s.profile()['name'], 'x1')
        self.assertEqual(s.outbox_count(), 2)
        self.assertEqual(s.last_outbox_ts(('reply',)), calendar.timegm((2026, 11, 10, 10, 0, 0)))
        for f in ('state.json', 'profile.json', 'outbox.jsonl'):
            self.assertFalse(os.path.exists(os.path.join(base, f)))
            self.assertTrue(os.path.isfile(os.path.join(self.d, 'trash', 'aa', f)))
        self.assertTrue(os.path.isfile(os.path.join(base, 'inbox.jsonl')))   # message text stays a file
        self.assertEqual(s.adb.import_errors, [])
        self.assertEqual(s.adb.imported, 3)

    def test_restart_does_not_import_again(self):
        self.legacy(state=self.real_state(), outbox=[json.dumps({'ts': '2026-11-10T10:00:00Z', 'kind': 'reply'})])
        s = self.store()
        s.update_state(lambda x: x.__setitem__('later', 1))
        self.restart()
        s2 = self.store()
        self.assertEqual(s2.adb.imported, 0)
        self.assertEqual(s2.state()['later'], 1)
        self.assertEqual(s2.outbox_count(), 1)
        self.assertEqual(len(glob.glob(os.path.join(self.d, 'trash', 'aa', '*'))), 2)

    def test_two_personas_with_same_file_names(self):
        self.legacy('aa', state={'who': 'a'})
        self.legacy('bb', state={'who': 'b'})
        a, b = self.store('aa'), self.store('bb')
        self.assertEqual((a.state()['who'], b.state()['who']), ('a', 'b'))
        self.assertEqual(len(glob.glob(os.path.join(self.d, 'trash', '*', 'state.json'))), 2)

    def test_broken_state_file_is_left_in_place_and_reported(self):
        base = self.legacy('aa')
        write(os.path.join(base, 'state.json'), '{broken')
        s = self.store()
        self.assertEqual(s.state(), {})
        self.assertTrue(os.path.isfile(os.path.join(base, 'state.json')))
        self.assertEqual(len(s.adb.import_errors), 1)
        self.assertNotIn('broken', json.dumps(s.adb.health()))

    def test_new_persona_starts_empty_and_inbox_is_a_file(self):
        s = self.store()
        ev = s.append_event({'type': 'message', 'text': MARKER})
        self.assertEqual(ev['id'], 1)
        self.assertTrue(os.path.isfile(os.path.join(self.d, 'personas', 'aa', 'inbox.jsonl')))
        self.assertEqual(s.read_inbox(0)[0]['text'], MARKER)
        self.assertEqual(s.state()['next_id'], 2)
        self.assertFalse(os.path.exists(os.path.join(self.d, 'personas', 'aa', 'state.json')))

    def test_outbox_trimmed_to_keep(self):
        s = self.store()
        for i in range(OUTBOX_KEEP + 5):
            s.log_outbox({'kind': 'reply', 'chars': i, 'text': MARKER})
        self.assertEqual(s.outbox_count(), OUTBOX_KEEP)
        rows = s.adb.db.query('SELECT entry FROM outbox_log')
        self.assertTrue(all(MARKER not in r[0] for r in rows))

    def test_legacy_outbox_longer_than_keep_is_trimmed_and_verified(self):
        lines = [json.dumps({'ts': '2026-11-10T10:00:00Z', 'kind': 'reply', 'chars': i}) for i in range(OUTBOX_KEEP + 20)]
        self.legacy(outbox=lines)
        s = self.store()
        self.assertEqual(s.outbox_count(), OUTBOX_KEEP)
        self.assertEqual(s.adb.import_errors, [])


class Degraded(Base):
    def make_corrupt(self):
        write(os.path.join(self.d, 'ih.db'), 'this is not a database ' * 50)

    def test_corrupt_database_runs_on_files_and_is_never_recreated(self):
        self.make_corrupt()
        s = self.store()
        self.assertIsNotNone(s.adb.degraded)
        s.update_state(lambda x: x.__setitem__('k', 1))
        s.log_outbox({'kind': 'reply', 'chars': 3})
        self.assertEqual(s.state()['k'], 1)
        self.assertEqual(s.outbox_count(), 1)
        self.assertIsNotNone(s.last_outbox_ts(('reply',)))
        self.assertTrue(os.path.isfile(os.path.join(self.d, 'degraded', 'personas', 'aa', 'state.json')))
        self.assertFalse(os.path.exists(os.path.join(self.d, 'ih.db')))
        self.assertEqual(len(glob.glob(os.path.join(self.d, 'ih.db.corrupt-*'))), 1)
        h = s.adb.health()
        self.assertTrue(h['degraded'])
        self.assertIn('WARNING', h)
        self.assertEqual(h['corrupt_copies'], 1)

    def test_schema_too_new_is_degraded_and_file_untouched(self):
        con = sqlite3.connect(os.path.join(self.d, 'ih.db'))
        con.execute('CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
        con.execute("INSERT INTO schema_version VALUES(99, 'x')")
        con.commit(); con.close()
        s = self.store()
        self.assertIn('SchemaTooNewError', s.adb.degraded)
        self.assertTrue(os.path.isfile(os.path.join(self.d, 'ih.db')))
        s.update_state(lambda x: x.__setitem__('k', 2))
        self.assertEqual(s.state()['k'], 2)

    def test_degraded_state_is_seeded_from_newest_snapshot(self):
        s = self.store()
        s.update_state(lambda x: x.update(self.real_state()))
        s.adb._call(lambda: s.adb.maintain(5))
        self.restart()
        self.make_corrupt_over_existing()
        s2 = self.store()
        self.assertIsNotNone(s2.adb.degraded)
        self.assertEqual(s2.state()['reminders'][0]['id'], 'r1')

    def make_corrupt_over_existing(self):
        for suffix in ('', '-wal', '-shm'):
            p = os.path.join(self.d, 'ih.db' + suffix)
            if os.path.exists(p):
                os.unlink(p)
        self.make_corrupt()

    def test_restore_then_degraded_files_are_imported(self):
        s = self.store()
        s.update_state(lambda x: x.update(a=1))
        s.adb._call(lambda: s.adb.maintain(5))
        snap = s.adb._newest_snapshot()
        self.restart()
        self.make_corrupt_over_existing()
        d = self.store()
        d.update_state(lambda x: x.update(a=2, b=3))
        d.log_outbox({'kind': 'ask', 'chars': 1})
        self.restart()
        for p in glob.glob(os.path.join(self.d, 'ih.db*')):
            os.unlink(p)
        shutil.copy(snap, os.path.join(self.d, 'ih.db'))
        r = self.store()
        self.assertIsNone(r.adb.degraded)
        self.assertEqual((r.state()['a'], r.state()['b']), (2, 3))
        self.assertEqual(r.outbox_count(), 1)
        self.assertFalse(os.path.exists(os.path.join(self.d, 'degraded', 'personas', 'aa', 'state.json')))
        self.assertEqual(r.adb.import_errors, [])

    def test_runtime_error_is_not_redirected_to_files(self):
        s = self.store()
        s.update_state(lambda x: x.__setitem__('k', 1))
        def boom(ns):
            raise sqlite3.OperationalError('disk is full')
        s.adb._kv = boom
        with self.assertRaises(sqlite3.OperationalError):
            s.state()
        with self.assertRaises(sqlite3.OperationalError):
            s.update_state(lambda x: x.__setitem__('k', 2))
        self.assertFalse(os.path.exists(os.path.join(self.d, 'degraded')))
        self.assertIn('disk is full', s.adb.health()['error'])


class Nightly(Base):
    def test_snapshot_keep_three_and_restore_check_matches(self):
        s = self.store()
        s.log_outbox({'kind': 'reply'})
        for i in range(5):
            self.now[0] += 86400 * 2
            s.adb._call(lambda: s.adb.maintain(4))
        self.assertEqual(len(glob.glob(os.path.join(self.d, 'backup', 'ih-*.db'))), 3)
        c = s.adb.last_check
        self.assertTrue(c['ok'] and c['rows_match_live'])
        self.assertEqual(c['rows']['outbox_log'], 1)
        self.assertEqual(c['schema_version'], 1)

    def test_no_second_snapshot_the_same_night_and_not_before_the_hour(self):
        s = self.store()
        self.now[0] += 86400 * 2
        s.adb._call(lambda: s.adb.maintain(3))
        n0 = len(glob.glob(os.path.join(self.d, 'backup', 'ih-*.db')))
        s.adb._call(lambda: s.adb.maintain(4))
        s.adb._call(lambda: s.adb.maintain(4))
        self.assertEqual(len(glob.glob(os.path.join(self.d, 'backup', 'ih-*.db'))), n0 + 1)

    def test_trash_purge_after_thirty_days(self):
        self.legacy(state={'a': 1})
        s = self.store()
        old = os.path.join(self.d, 'trash', 'aa', 'state.json')
        self.assertTrue(os.path.isfile(old))
        self.now[0] += 29 * 86400
        s.adb.last_purge_day = None
        s.adb._call(lambda: s.adb.maintain())
        self.assertTrue(os.path.isfile(old))
        self.now[0] += 2 * 86400
        s.adb.last_purge_day = None
        os.utime(old, (self.now[0] - 31 * 86400, self.now[0] - 31 * 86400))
        s.adb._call(lambda: s.adb.maintain())
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(os.path.join(self.d, 'trash', 'aa')))


class Concurrency(Base):
    def test_concurrent_update_state_loses_nothing(self):
        s = self.store()
        def run():
            for _ in range(LOOPS):
                s.update_state(lambda x: x.__setitem__('n', int(x.get('n', 0)) + 1))
        ts = [threading.Thread(target=run) for _ in range(THREADS)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(s.state()['n'], THREADS * LOOPS)

    def test_many_personas_writers_readers_and_outbox_together(self):
        stores = [self.store(k) for k in ('aa', 'bb', 'cc')]
        errors = []
        def run(st):
            try:
                for i in range(LOOPS):
                    st.update_state(lambda x: x.__setitem__('n', int(x.get('n', 0)) + 1))
                    st.log_outbox({'kind': 'reply', 'chars': i})
                    st.state(); st.outbox_count(); st.last_outbox_ts(('reply',)); st.adb.health()
                    st.append_event({'type': 'message', 'text': 'x'})
            except Exception as e:
                errors.append(repr(e))
        ts = [threading.Thread(target=run, args=(st,)) for st in stores for _ in range(3)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errors, [])
        for st in stores:
            self.assertEqual(st.outbox_count(), 3 * LOOPS)
        # three threads per persona: the state counter is incremented by update_state AND by append_event (next_id)
        self.assertEqual(stores[0].state()['next_id'], 3 * LOOPS + 1)


class Status(Base):
    def test_health_has_counts_only_and_no_text(self):
        self.legacy(state={'reminders': [{'text': MARKER}]}, profile={'name': MARKER}, outbox=[json.dumps({'ts': '2026-11-10T10:00:00Z', 'kind': 'reply', 'chars': 2})],
                    inbox=[json.dumps({'id': 1, 'type': 'message', 'text': MARKER})])
        s = self.store()
        s.append_event({'type': 'message', 'text': MARKER})
        s.adb._call(lambda: s.adb.maintain(5))
        h = s.adb.health()
        self.assertNotIn(MARKER, json.dumps(h))
        self.assertEqual(h['schema_version'], 1)
        self.assertFalse(h['degraded'])
        self.assertEqual(h['rows']['outbox_log'], 1)
        self.assertEqual(h['snapshots'], 1)
        self.assertEqual(h['trash']['files'], 3)
        self.assertTrue(h['last_restore_check']['ok'])
        self.assertEqual(h['legacy_import_errors'], [])

    def test_assistant_status_carries_db_block_without_text(self):
        from main import Assistant
        opts = {'mode': 'dry', 'api_token': '', 'personas': [{'key': 'pp', 'user_id': '1', 'bot_token': '', 'live': False,
                'max_per_day': 0, 'checkins': [], 'can_tell': [], 'checkin_days': [], 'closed_dates': [], 'checkin_texts': [], 'checkin_send': False}]}
        a = Assistant(opts, self.d, log=lambda *x: None)
        a.stores['pp'].append_event({'type': 'message', 'text': MARKER})
        a.stores['pp'].update_state(lambda x: x.__setitem__('reminders', [{'text': MARKER}]))
        out = a.status()
        self.assertFalse(out['db_degraded'])
        self.assertEqual(out['db']['schema_version'], 1)
        self.assertNotIn(MARKER, json.dumps(out))
        self.assertEqual(out['personas']['pp']['inbox'], 1)


if __name__ == '__main__':
    unittest.main()
