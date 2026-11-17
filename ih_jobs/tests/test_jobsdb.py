"""ih_jobs' own database (jobsdb.py): bus deck answers, the one-time import of the old file, the HTTP helper, and what happens when the database is unusable.
Fictional values, temp folders only."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import jobsdb
from plugins import bus_watch as B


class Base(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.plugin = os.path.join(self.data, 'bus_watch')
        os.makedirs(self.plugin)
        self.logs = []
        self.addCleanup(jobsdb.close_all)

    def legacy(self, obj):
        with open(os.path.join(self.plugin, 'deck_overrides.json'), 'w') as f:
            json.dump(obj, f)

    def make(self):
        j = jobsdb.JobsDB(self.data, log=self.logs.append)
        self.addCleanup(lambda: j.db and j.db.close())
        return j


class TestFleetNumber(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(jobsdb.fleet_number('OP-12345'), '12345')
        self.assertEqual(jobsdb.fleet_number(' 12345 '), '12345')
        self.assertEqual(jobsdb.fleet_number('fbos-1'), '1')
        for bad in ('', None, 'OP-', 'abc', '12 34', '123456789'):
            self.assertIsNone(jobsdb.fleet_number(bad), bad)


class TestStore(Base):
    def test_set_get_clear_and_persist(self):
        j = self.make()
        self.assertEqual(j.set_override('OP-12345', 'single', by='andrew'), (True, 'Saved 12345 as single'))
        self.assertEqual(j.override('OP-12345'), 'single')
        self.assertEqual(j.override('12345'), 'single')
        j.set_override('12345', 'DOUBLE')
        self.assertEqual(j.overrides(), {'12345': 'double'})
        jobsdb.close_all()
        j2 = self.make()                                   # a fresh process sees the same answer
        self.assertEqual(j2.override('12345'), 'double')
        self.assertTrue(j2.set_override('12345', 'unknown')[0])
        self.assertIsNone(j2.override('12345'))

    def test_bad_input_is_refused_and_nothing_stored(self):
        j = self.make()
        self.assertFalse(j.set_override('banana', 'single')[0])
        self.assertFalse(j.set_override('OP-12345', 'triple')[0])
        self.assertFalse(j.set_override('OP-12345', '')[0])
        self.assertEqual(j.overrides(), {})

    def test_set_by_is_cleaned(self):
        j = self.make()
        j.set_override('5', 'single', by='x' * 100 + "'; DROP TABLE bus_deck_overrides; --")
        who = j.db.query('SELECT set_by FROM bus_deck_overrides')[0][0]
        self.assertLessEqual(len(who), jobsdb.BY_MAX)
        self.assertEqual(j.overrides(), {'5': 'single'})


class TestLegacyImport(Base):
    def test_live_imports_once_and_moves_the_file_to_the_trash(self):
        self.legacy({'12346': 'single', '12347': 'double', '1': 'unknown', '2': 'junk'})
        j = self.make()
        self.assertEqual(j.overrides(), {'12346': 'single', '12347': 'double'})
        self.assertEqual(j.imported, 2)
        self.assertFalse(os.path.exists(os.path.join(self.plugin, 'deck_overrides.json')))
        self.assertEqual(os.listdir(os.path.join(self.data, 'trash')), ['deck_overrides.json'])
        self.assertTrue(any("'2'" in m and 'ignored' in m for m in self.logs))      # junk is reported, not silently lost
        self.assertTrue(os.listdir(os.path.join(self.data, 'backup')))              # a safety snapshot right after the import
        jobsdb.close_all()
        j2 = self.make()                                                           # a restart imports nothing more
        self.assertEqual(j2.overrides(), {'12346': 'single', '12347': 'double'})
        self.assertEqual(j2.imported, 0)

    def test_a_person_answer_in_the_database_beats_the_old_file(self):
        j = self.make()
        j.set_override('12346', 'double')
        self.legacy({'12346': 'single'})
        self.assertEqual(j.override('12346'), 'double')

    def test_dry_reads_the_file_but_moves_nothing(self):
        self.legacy({'12346': 'single'})
        j = self.make()
        self.assertEqual(j.override('12346', live=False), 'single')
        self.assertTrue(os.path.exists(os.path.join(self.plugin, 'deck_overrides.json')))
        self.assertFalse(os.path.exists(os.path.join(self.data, 'trash')))

    def test_broken_old_file_is_left_in_place(self):
        with open(os.path.join(self.plugin, 'deck_overrides.json'), 'w') as f:
            f.write('{not json')
        j = self.make()
        self.assertEqual(j.overrides(), {})
        self.assertTrue(os.path.exists(os.path.join(self.plugin, 'deck_overrides.json')))
        self.assertIn('FAILED', ' '.join(self.logs))
        self.assertTrue(j.import_error)


class TestUnusableDatabase(Base):
    def test_reads_fall_back_to_the_old_file_and_writes_are_refused(self):
        with open(os.path.join(self.data, jobsdb.DB_NAME), 'wb') as f:
            f.write(b'this is not a database' * 50)
        self.legacy({'12346': 'single'})
        j = self.make()
        self.assertEqual(j.override('12346'), 'single')          # the bus job keeps working
        ok, msg = j.set_override('12346', 'double')
        self.assertFalse(ok)
        self.assertIn('Nothing was saved', msg)
        self.assertTrue(j.error)
        self.assertTrue(os.path.exists(os.path.join(self.plugin, 'deck_overrides.json')))   # not moved


class TestHttpHelperAndPlugin(Base):
    def test_get_and_post(self):
        code, out = B.http_overrides(self.plugin, 'GET')
        self.assertEqual((code, out['overrides']), (200, {}))
        self.assertEqual(B.http_overrides(self.plugin, 'POST', {'code': 'OP-12345', 'deck': 'single', 'by': 'dash'})[0], 200)
        self.assertEqual(B.http_overrides(self.plugin, 'GET')[1]['overrides'], {'12345': 'single'})
        self.assertEqual(B.http_overrides(self.plugin, 'POST', {'code': 'nope', 'deck': 'single'})[0], 400)
        self.assertEqual(B.http_overrides(self.plugin, 'POST', 'text')[0], 400)
        self.assertEqual(B.http_overrides(self.plugin, 'POST', {'code': '12345', 'deck': 'unknown'})[0], 200)
        self.assertEqual(B.http_overrides(self.plugin, 'GET')[1]['overrides'], {})

    def test_database_down_gives_503_not_a_false_saved(self):
        with open(os.path.join(self.data, jobsdb.DB_NAME), 'wb') as f:
            f.write(b'garbage' * 100)
        code, out = B.http_overrides(self.plugin, 'POST', {'code': '12345', 'deck': 'single'})
        self.assertEqual((code, out['ok']), (503, False))

    def test_answer_set_through_the_api_decides_the_deck(self):
        B.http_overrides(self.plugin, 'POST', {'code': 'OP-77', 'deck': 'double'})
        db = B.jobs_db(self.plugin)
        self.assertEqual(db.override('77'), 'double')
        self.assertEqual(db.override('OP-77'), 'double')


if __name__ == '__main__':
    unittest.main()
