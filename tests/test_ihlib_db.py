"""ihlib.db tests (temp folders only: no network, no real add-on data). run: python3 tests/test_ihlib_db.py"""
import os, sys, json, stat, shutil, sqlite3, subprocess, tempfile, threading, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ihlib import db

WRITER_THREADS = 8            # the plan's "8 threads writing at once"
WRITES_PER_THREAD = 50
START = 1800000000.0          # fake clock start (epoch seconds)
CRASH_EXIT_CODE = 7
CORRUPT_AT = 4096             # offset of the second page: the header stays valid, the data does not
CORRUPT_BYTES = 8192
FILL_ROWS = 500

M1 = (1, 'CREATE TABLE a(id INTEGER PRIMARY KEY, v TEXT)')
M2 = (2, "ALTER TABLE a ADD COLUMN n INTEGER; INSERT INTO a(v, n) VALUES('semi;colon', 1);")
M3 = (3, lambda con: con.execute('CREATE TABLE b(id INTEGER PRIMARY KEY, a_id INTEGER REFERENCES a(id))'))


def put(path, text):
    with open(path, 'w') as f: f.write(text)


def get(path):
    with open(path) as f: return f.read()


class Clock:
    def __init__(self, now=START): self.now = now
    def __call__(self): return self.now
    def days(self, n): self.now += n * db.SECONDS_PER_DAY


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name; self.path = os.path.join(self.dir, db.DB_FILE_NAME); self.clock = Clock()

    def open(self, migrations=(), **kw):
        d = db.open(self.path, migrations, clock=self.clock, **kw); self.addCleanup(d.close); return d


class TestOpen(Base):
    def test_pragmas_mode_and_default_name(self):
        d = self.open()
        self.assertEqual(d.con.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(d.con.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(d.con.execute('PRAGMA busy_timeout').fetchone()[0], db.BUSY_TIMEOUT_MS)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), db.FILE_MODE)
        self.assertIs(db.open, db.open_db); self.assertEqual(d.schema_version(), 0)

    def test_foreign_keys_enforced_and_tx_rolls_back(self):
        d = self.open([M1, M2, M3])
        with self.assertRaises(sqlite3.IntegrityError): d.execute('INSERT INTO b(a_id) VALUES(999)')
        with self.assertRaises(RuntimeError):
            with d.tx() as con:
                con.execute("INSERT INTO a(v) VALUES('gone')")
                with d.tx() as inner: inner.execute("INSERT INTO a(v) VALUES('gone too')")
                raise RuntimeError('boom')
        self.assertEqual(d.query('SELECT v FROM a'), [('semi;colon',)])
        d.execute("INSERT INTO a(v) VALUES('kept')"); self.assertEqual(d.table_counts()['a'], 2)

    def test_one_connection_per_thread(self):
        d = self.open(); seen = []
        t = threading.Thread(target=lambda: seen.append(d.con)); t.start(); t.join()
        self.assertIsNot(seen[0], d.con); self.assertIs(d.con, d.con)
        d.close()
        with self.assertRaises(db.DBError): d.con


class TestMigrations(Base):
    def test_apply_once_in_order(self):
        order = []
        steps = [(1, lambda con: (order.append(1), con.execute('CREATE TABLE a(id INTEGER PRIMARY KEY)'))),
                 (2, lambda con: (order.append(2), con.execute('ALTER TABLE a ADD COLUMN v TEXT'))),
                 (3, lambda con: (order.append(3), con.execute('CREATE TABLE b(id INTEGER)')))]
        d = self.open(steps[:2]); self.assertEqual(order, [1, 2]); self.assertEqual(d.schema_version(), 2)
        self.assertEqual(d.migrate(), []); d.close()
        d = self.open(steps); self.assertEqual(order, [1, 2, 3]); self.assertEqual(d.schema_version(), 3)
        self.assertEqual(d.query('SELECT version FROM schema_version ORDER BY version'), [(1,), (2,), (3,)])
        self.assertEqual(d.query('SELECT applied_at FROM schema_version WHERE version=1')[0][0], '2027-01-15T08:00:00+00:00')
        d.close(); self.open(steps); self.assertEqual(order, [1, 2, 3])

    def test_sql_text_with_several_statements(self):
        d = self.open([M1, M2]); self.assertEqual(d.query('SELECT v, n FROM a'), [('semi;colon', 1)])

    def test_bad_lists_refused(self):
        for bad in ([(2, 'x'), (1, 'y')], [(1, 'x'), (1, 'y')], [(0, 'x')], [(1, 5)], [(True, 'x')]):
            with self.assertRaises(ValueError): db.open(os.path.join(self.dir, 'bad.db'), bad)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'bad.db')))

    def test_newer_schema_refused_and_untouched(self):
        self.open([M1, M2, M3]).close(); before = db.restore_check(self.path)
        with self.assertRaises(db.SchemaTooNewError) as c: db.open(self.path, [M1, M2])
        self.assertIn('version 3', str(c.exception)); self.assertIn('only knows 2', str(c.exception))
        with self.assertRaises(db.SchemaTooNewError): db.open(self.path)
        self.assertEqual(db.restore_check(self.path), before); self.assertEqual(db.quarantined(self.path), [])
        self.assertEqual(self.open([M1, M2, M3]).schema_version(), 3)

    def test_failed_step_rolls_back_whole_step(self):
        def bad(con):
            con.execute('CREATE TABLE half(id INTEGER)'); con.execute("INSERT INTO a(v) VALUES('half')"); raise RuntimeError('boom')
        with self.assertRaises(db.MigrationError) as c: db.open(self.path, [M1, (2, bad), M3], clock=self.clock)
        self.assertIn('still at version 1', str(c.exception))
        d = self.open([M1]); self.assertEqual(d.schema_version(), 1); self.assertEqual(list(d.table_counts()), ['a', 'schema_version'])
        self.assertEqual(d.table_counts()['a'], 0); d.close()
        with self.assertRaises(db.MigrationError): db.open(self.path, [M1, (2, 'CREATE TABLE half(id INTEGER); NOT SQL;')], clock=self.clock)
        self.assertNotIn('half', self.open([M1]).table_counts())

    def test_crash_between_steps_leaves_old_version(self):
        """A real process is killed (os._exit, no cleanup) inside step 3, after its writes and before its commit."""
        code = ('import os, sys\nsys.path.insert(0, %r)\nfrom ihlib import db\n'
                'def die(con):\n    con.execute("CREATE TABLE half(id INTEGER)")\n    con.execute("INSERT INTO a(v) VALUES(\'half\')")\n    os._exit(%d)\n'
                'db.open(%r, [(1, "CREATE TABLE a(id INTEGER PRIMARY KEY, v TEXT)"), (2, "INSERT INTO a(v) VALUES(\'two\')"), (3, die), (4, "CREATE TABLE never(id INTEGER)")])\n'
                % (ROOT, CRASH_EXIT_CODE, self.path))
        self.assertEqual(subprocess.run([sys.executable, '-c', code]).returncode, CRASH_EXIT_CODE)
        r = db.restore_check(self.path); self.assertFalse(os.path.exists(self.path + '.corrupt'))
        d = self.open([M1, (2, "INSERT INTO a(v) VALUES('two')")])
        self.assertEqual(d.schema_version(), 2); self.assertEqual(d.query('SELECT v FROM a'), [('two',)])
        self.assertEqual(sorted(d.table_counts()), ['a', 'schema_version']); self.assertEqual(db.quarantined(self.path), []); d.close()
        d = self.open([M1, (2, 'SELECT 1'), (3, 'CREATE TABLE half(id INTEGER)'), (4, 'CREATE TABLE never(id INTEGER)')])
        self.assertEqual(d.schema_version(), 4); self.assertEqual(d.query('SELECT v FROM a'), [('two',)]); self.assertIsInstance(r, dict)

    def test_snapshot_before_upgrade_only(self):
        snaps = os.path.join(self.dir, 'backup')
        self.open([M1], snapshot_dir=snaps).close(); self.assertFalse(os.path.exists(snaps))     # new file: nothing to save
        self.open([M1], snapshot_dir=snaps).close(); self.assertFalse(os.path.exists(snaps))     # nothing pending
        self.open([M1, M2], snapshot_dir=snaps).close()
        names = os.listdir(snaps); self.assertEqual(len(names), 1)
        self.assertEqual(db.restore_check(os.path.join(snaps, names[0]))['schema_version'], 1)


class TestKV(Base):
    def test_store_compatible_round_trip(self):
        k = db.KV(self.open(), 'p')
        self.assertEqual(k.load('a'), {}); self.assertEqual(k.load('a', []), []); self.assertEqual(k.load(), {})
        self.assertEqual(k.save('a', {'x': 1}), self.path); self.assertEqual(k.load('a'), {'x': 1})
        self.assertEqual(k.update('a', y=2), {'x': 1, 'y': 2}); self.assertEqual(k.load('a'), {'x': 1, 'y': 2})
        self.assertEqual(k.update('new', z=3), {'z': 3})
        for bad in ('../x', '.h', 'a/b', '', 'x' * 61, None):
            with self.assertRaises(ValueError): k.save(bad, {})
            with self.assertRaises(ValueError): k.load(bad)
        with self.assertRaises(TypeError): k.save('a', {'bad': object()})
        self.assertEqual(k.load('a'), {'x': 1, 'y': 2})

    def test_get_set_delete_items_namespaces(self):
        d = self.open(); k, other = db.KV(d, 'p'), db.KV(d, 'q')
        self.assertIsNone(k.get('n')); self.assertEqual(k.get('n', 5), 5)
        k.set('n', [1, 'two', None, {'k': 1.5}]); k.set('m', 'text'); other.set('n', 0)
        self.assertEqual(k.get('n'), [1, 'two', None, {'k': 1.5}]); self.assertEqual(other.get('n'), 0)
        self.assertEqual(k.items(), [('m', 'text'), ('n', [1, 'two', None, {'k': 1.5}])])
        self.assertTrue(k.delete('m')); self.assertFalse(k.delete('m')); self.assertEqual(other.items(), [('n', 0)])
        self.assertEqual(k.mutate('count', lambda v: v + 1, default=0), 1)
        d.con.execute("UPDATE kv SET json='{bad' WHERE name='n' AND namespace='p'"); self.assertEqual(k.load('n'), {})   # unreadable -> {} like Store
        d.close(); self.assertEqual(db.KV(self.open(), 'q').get('n'), 0)                                                     # survives a reopen

    def test_failed_write_inside_tx_keeps_old(self):
        d = self.open(); k = db.KV(d); k.save('a', {'ok': 1})
        with self.assertRaises(RuntimeError):
            with d.tx():
                k.save('a', {'ok': 2}); k.save('b', 1); raise RuntimeError('boom')
        self.assertEqual(k.items(), [('a', {'ok': 1})])

    def test_concurrent_writers(self):
        d = self.open(); k = db.KV(d, 'p'); errors = []
        def work(i):
            try:
                for n in range(WRITES_PER_THREAD):
                    k.mutate('count', lambda v: v + 1, default=0); k.update('shared', **{'t%d' % i: n}); k.save('own%d' % i, n)
            except Exception as e:
                errors.append(repr(e))
        threads = [threading.Thread(target=work, args=(i,)) for i in range(WRITER_THREADS)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, []); self.assertEqual(k.get('count'), WRITER_THREADS * WRITES_PER_THREAD)
        self.assertEqual(k.load('shared'), {'t%d' % i: WRITES_PER_THREAD - 1 for i in range(WRITER_THREADS)})
        self.assertEqual(len(k.items()), WRITER_THREADS + 2)
        self.assertEqual(d.con.execute('PRAGMA integrity_check').fetchone()[0], db.INTEGRITY_OK)


class TestLegacyImport(Base):
    def setUp(self):
        super().setUp(); self.trash = os.path.join(self.dir, 'trash'); self.old = os.path.join(self.dir, 'old'); os.makedirs(self.old)

    def write(self, name, text):
        p = os.path.join(self.old, name); put(p, text); return p

    def test_kv_import_once_then_trash(self):
        d = self.open(); k = db.KV(d, 'trust'); p = self.write('state.json', json.dumps({'level': 2}))
        r = db.import_legacy_json(d, p, k, self.trash)
        self.assertEqual((r['status'], r['rows']), ('imported', 1)); self.assertEqual(k.load('state'), {'level': 2})
        self.assertFalse(os.path.exists(p)); self.assertEqual(r['trash'], os.path.join(self.trash, 'state.json'))
        self.assertEqual(json.loads(get(r['trash'])), {'level': 2}); self.assertEqual(os.stat(r['trash']).st_mtime, START)
        k.save('state', {'level': 3})
        self.assertEqual(db.import_legacy_json(d, p, k, self.trash), {'status': 'already', 'rows': 1, 'trash': None})
        self.write('state.json', json.dumps({'level': 99}))                    # an old version wrote the file again: never imported twice
        self.clock.now += 1
        r = db.import_legacy_json(d, p, k, self.trash); self.assertEqual(r['status'], 'already')
        self.assertEqual(k.load('state'), {'level': 3}); self.assertEqual(len(os.listdir(self.trash)), 2); self.assertFalse(os.path.exists(p))
        self.assertEqual(d.table_counts()['ih_legacy_import'], 1)

    def test_missing_and_named(self):
        d = self.open(); k = db.KV(d)
        self.assertEqual(db.import_legacy_json(d, os.path.join(self.old, 'nope.json'), k, self.trash)['status'], 'missing')
        self.assertFalse(os.path.exists(self.trash))
        db.import_legacy_json(d, self.write('trust_state.json', '[1, 2]'), k, self.trash, name='levels'); self.assertEqual(k.get('levels'), [1, 2])

    def test_table_import_json_and_jsonl(self):
        d = self.open([(1, 'CREATE TABLE log(id INTEGER PRIMARY KEY, at TEXT, what TEXT, extra TEXT)')])
        p = self.write('log.jsonl', '{"at": "t1", "what": "a"}\n\n{"at": "t2", "what": "b", "extra": {"k": [1]}}\n')
        self.assertEqual(db.import_legacy_json(d, p, 'log', self.trash)['rows'], 2)
        self.assertEqual(d.query('SELECT at, what, extra FROM log ORDER BY id'), [('t1', 'a', None), ('t2', 'b', '{"k": [1]}')])
        self.assertEqual(db.import_legacy_json(d, p, 'log', self.trash)['status'], 'already'); self.assertEqual(d.table_counts()['log'], 2)
        db.import_legacy_json(d, self.write('more.json', '[{"at": "t3"}]'), 'log', self.trash); self.assertEqual(d.table_counts()['log'], 3)
        r = db.import_legacy_json(d, self.write('c.json', '{"n": 4}'), lambda con, data: con.execute('INSERT INTO log(what) VALUES(?)', (str(data['n']),)).rowcount, self.trash)
        self.assertEqual(r['rows'], 1); self.assertEqual(d.table_counts()['log'], 4)

    def test_bad_files_left_in_place_nothing_stored(self):
        d = self.open([(1, 'CREATE TABLE log(id INTEGER PRIMARY KEY, at TEXT)')]); k = db.KV(d)
        cases = [(self.write('broken.json', '{bad'), k), (self.write('torn.jsonl', '{"at": "t1"}\n{"at": '), 'log'),
                 (self.write('cols.json', '[{"at": "t1"}, {"nope": 1}]'), 'log'), (self.write('shape.json', '{"at": "t1"}'), 'log'),
                 (self.write('table.json', '[]'), 'missing_table'), (self.write('inject.json', '[]'), 'log; DROP TABLE log')]
        for p, target in cases:
            with self.assertRaises(db.LegacyImportError): db.import_legacy_json(d, p, target, self.trash)
            self.assertTrue(os.path.exists(p), p)
        self.assertEqual(d.table_counts()['log'], 0); self.assertEqual(k.items(), []); self.assertEqual(d.table_counts()['ih_legacy_import'], 0)
        self.assertFalse(os.path.exists(self.trash))
        r = db.import_legacy_json(d, cases[1][0], 'log', self.trash, skip_bad_lines=True); self.assertEqual(r['rows'], 1)

    def test_crash_after_commit_before_move_is_finished_next_time(self):
        d = self.open(); k = db.KV(d); p = self.write('state.json', '{"a": 1}'); real = db.move_to_trash
        def boom(*a, **kw): raise OSError('killed here')
        db.move_to_trash = boom
        try:
            with self.assertRaises(OSError): db.import_legacy_json(d, p, k, self.trash)
        finally:
            db.move_to_trash = real
        self.assertTrue(os.path.exists(p)); self.assertEqual(k.load('state'), {'a': 1}); k.save('state', {'a': 2})
        r = db.import_legacy_json(d, p, k, self.trash)
        self.assertEqual(r['status'], 'already'); self.assertFalse(os.path.exists(p)); self.assertTrue(os.path.exists(r['trash'])); self.assertEqual(k.load('state'), {'a': 2})


class TestTrash(Base):
    def test_purge_by_age_with_fake_clock(self):
        trash = os.path.join(self.dir, 'trash'); self.assertEqual(db.purge_trash(trash, clock=self.clock), [])
        src = os.path.join(self.dir, 'a.json'); put(src, '{}'); os.utime(src, (0, 0))     # a very old file: age counts from the move
        db.move_to_trash(src, trash, self.clock)
        self.clock.days(10); src = os.path.join(self.dir, 'b.json'); put(src, '{}'); db.move_to_trash(src, trash, self.clock)
        os.mkdir(os.path.join(trash, 'folder')); os.utime(os.path.join(trash, 'folder'), (0, 0))
        self.assertEqual(db.TRASH_KEEP_DAYS, 30)
        self.clock.days(db.TRASH_KEEP_DAYS - 10); self.assertEqual(db.purge_trash(trash, clock=self.clock), [])        # a.json is exactly 30 days: kept
        self.clock.now += 1; self.assertEqual(db.purge_trash(trash, clock=self.clock), ['a.json'])
        self.assertEqual(sorted(os.listdir(trash)), ['b.json', 'folder'])
        self.assertEqual(db.purge_trash(trash, days=1, clock=self.clock), ['b.json']); self.assertEqual(os.listdir(trash), ['folder'])


class TestSnapshot(Base):
    def test_snapshot_restore_check_and_keep(self):
        d = self.open([M1, M2]); k = db.KV(d, 'p'); k.save('a', {'x': 1}); snaps = os.path.join(self.dir, 'backup'); made = []
        for i in range(db.SNAPSHOT_KEEP + 2):
            d.execute('INSERT INTO a(v) VALUES(?)', ('row%d' % i,)); made.append(db.snapshot(self.path, snaps, clock=self.clock)); self.clock.now += 1
        self.assertEqual(db.SNAPSHOT_KEEP, 3)
        self.assertEqual(sorted(os.listdir(snaps)), [os.path.basename(p) for p in made[-db.SNAPSHOT_KEEP:]])
        self.assertEqual(os.path.basename(made[-1]), 'ih-20270115T080004Z.db'); self.assertEqual(stat.S_IMODE(os.stat(made[-1]).st_mode), db.FILE_MODE)
        r = db.restore_check(made[-1])
        self.assertEqual(r, {'ok': True, 'integrity': 'ok', 'schema_version': 2, 'error': None, 'tables': {'a': db.SNAPSHOT_KEEP + 3, 'kv': 1, 'schema_version': 2}})
        self.assertEqual(r['tables'], d.table_counts())
        self.assertEqual(db.restore_check(made[-2])['tables']['a'], db.SNAPSHOT_KEEP + 2)       # a snapshot is a point in time
        before = (sorted(os.listdir(snaps)), os.stat(made[-1]).st_mtime_ns, os.path.getsize(made[-1]))
        db.restore_check(made[-1]); self.assertEqual(before, (sorted(os.listdir(snaps)), os.stat(made[-1]).st_mtime_ns, os.path.getsize(made[-1])))
        restored = os.path.join(self.dir, 'restored.db'); shutil.copyfile(made[-1], restored)           # a restore is a file copy
        d2 = db.open(restored, [M1, M2]); self.addCleanup(d2.close); self.assertEqual(db.KV(d2, 'p').load('a'), {'x': 1})

    def test_snapshot_while_writing(self):
        d = self.open([M1]); snaps = os.path.join(self.dir, 'backup'); stop = threading.Event(); errors = []
        def work():
            try:
                while not stop.is_set(): d.execute("INSERT INTO a(v) VALUES('w')")
            except Exception as e:
                errors.append(repr(e))
        t = threading.Thread(target=work); t.start()
        try:
            for _ in range(db.SNAPSHOT_KEEP):
                self.assertTrue(db.restore_check(db.snapshot(self.path, snaps, clock=self.clock))['ok']); self.clock.now += 1
        finally:
            stop.set(); t.join()
        self.assertEqual(errors, [])

    def test_errors(self):
        snaps = os.path.join(self.dir, 'backup')
        with self.assertRaises(db.SnapshotError): db.snapshot(self.path, snaps)
        self.assertFalse(os.path.exists(self.path))                               # a snapshot never creates the source
        put(self.path, 'this is not a database, it is a text file' * 50)
        with self.assertRaises(db.SnapshotError): db.snapshot(self.path, snaps)
        self.assertEqual(os.listdir(snaps), [])
        with self.assertRaises(ValueError): db.snapshot(self.path, snaps, keep=0)
        r = db.restore_check(self.path); self.assertFalse(r['ok']); self.assertTrue(r['error'])
        self.assertEqual(db.restore_check(os.path.join(self.dir, 'nope.db'))['error'], 'no such file')


class TestCorrupt(Base):
    def test_not_a_database_is_kept_aside_never_recreated(self):
        junk = 'this is not a database, it is a text file' * 50; put(self.path, junk)
        with self.assertRaises(db.CorruptError) as c: db.open(self.path, [M1], clock=self.clock)
        aside = c.exception.quarantined
        self.assertEqual(aside, self.path + '.corrupt-20270115T080000Z'); self.assertEqual(get(aside), junk)
        self.assertIn(aside, str(c.exception)); self.assertFalse(os.path.exists(self.path)); self.assertEqual(db.quarantined(self.path), [aside])
        with self.assertRaises(db.CorruptError) as c: db.open(self.path, [M1], clock=self.clock)      # the restart loop must not make an empty one
        self.assertEqual(c.exception.quarantined, aside); self.assertFalse(os.path.exists(self.path))
        d = self.open([M1], accept_fresh=True); self.assertEqual(d.schema_version(), 1); self.assertEqual(get(aside), junk)

    def test_damaged_pages_detected_and_snapshot_restores(self):
        d = self.open([M1]); d.con.executemany('INSERT INTO a(v) VALUES(?)', [('x' * 200,) for _ in range(FILL_ROWS)])
        snap = db.snapshot(self.path, os.path.join(self.dir, 'backup'), clock=self.clock)
        d.con.execute('PRAGMA wal_checkpoint(TRUNCATE)'); d.close()
        with open(self.path, 'r+b') as f:
            f.seek(CORRUPT_AT); f.write(b'\xff' * CORRUPT_BYTES)
        self.assertFalse(db.restore_check(self.path)['ok'])
        with self.assertRaises(db.CorruptError) as c: db.open(self.path, [M1], clock=self.clock)
        self.assertTrue(os.path.exists(c.exception.quarantined)); self.assertFalse(os.path.exists(self.path))
        shutil.copyfile(snap, self.path)
        self.assertEqual(self.open([M1]).table_counts()['a'], FILL_ROWS)

    def test_second_damage_gets_its_own_name(self):
        for _ in range(2):
            put(self.path, 'junk' * 500)
            with self.assertRaises(db.CorruptError): db.open(self.path, clock=self.clock, accept_fresh=True)
        self.assertEqual(len(db.quarantined(self.path)), 2)


if __name__ == '__main__':
    unittest.main()
