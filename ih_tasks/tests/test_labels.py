"""Labels: validation, store add/edit, merge union, old rows from before schema 12, API."""
import os, shutil, sqlite3, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import main as APP
import model as M
import store as S
from test_api import OWNER, WORKER, Server, call
from test_store import Case


class Check(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(M.check_labels(None), [])
        self.assertEqual(M.check_labels([]), [])
        self.assertEqual(M.check_labels([' IH ', 'kindle', 'ih', '', 'home-2']), ['ih', 'kindle', 'home-2'])
        self.assertEqual(M.check_labels('IH, kindle ,'), ['ih', 'kindle'])

    def test_refused(self):
        for bad in (['has space'], ['a_b'], ['x' * 25], [5], 7, ['l%d' % i for i in range(9)], {'a': 1}):
            with self.assertRaises(M.RuleError, msg=repr(bad)) as c:
                M.check_labels(bad)
            self.assertEqual(c.exception.code, 'bad')
        self.assertEqual(len(M.check_labels(['l%d' % i for i in range(8)])), 8)
        self.assertEqual(len(M.check_labels(['x' * 24])[0]), 24)


class Store(Case):
    def test_create_edit_list(self):
        self.assertEqual(self.s.get(self.new())['labels'], [])
        tid = self.new('Fix the Kindle clock', labels=['Kindle', 'ih'])
        self.assertEqual(self.s.get(tid)['labels'], ['kindle', 'ih'])
        self.assertEqual([t['labels'] for t in self.s.list() if t['id'] == tid], [['kindle', 'ih']])
        t = self.s.edit(tid, 'owner', labels=['games'])
        self.assertEqual(t['labels'], ['games'])
        self.assertEqual(self.s.edit(tid, 'owner', labels=[])['labels'], [])
        self.assertEqual(self.s.history(tid)[-1]['text'], 'Changed: labels')
        self.refused('bad', self.s.edit, tid, 'owner', labels=['no spaces'])
        self.refused('bad', self.s.create, 'Another thing entirely', 'worker', labels=['bad label'])

    def test_labels_in_use(self):
        self.new('Alpha thing to do', labels=['ih', 'kindle'])
        self.new('Beta other matter', labels=['ih'])
        self.new('Gamma unlabelled')
        self.assertEqual(self.s.labels(), {'labels': [{'label': 'ih', 'count': 2}, {'label': 'kindle', 'count': 1}]})

    def test_merge_keeps_union(self):
        a = self.new('Alpha thing to do', labels=['ih', 'kindle'])
        b = self.new('Zebra crossing repaint', labels=['kindle', 'house'])
        c = self.new('Quantum widget polish')
        t = self.s.merge(a, [b, c], 'owner')['task']
        self.assertEqual(t['labels'], ['ih', 'kindle', 'house'])

    def test_merge_without_labels_adds_no_event(self):
        a, b = self.new('Alpha thing to do'), self.new('Zebra crossing repaint')
        t = self.s.merge(a, [b], 'owner')['task']
        self.assertFalse([h for h in t['history'] if 'labels' in h['text']])

    def test_old_database_gets_empty_labels(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, 'old.db')
            old = S.ihdb.open(path, S.MIGRATIONS[:11], snapshot_dir=os.path.join(d, 'b'))
            old.execute("INSERT INTO tasks(title, stage, created_by, created_at, updated_at) VALUES('Old one', 'todo', 'worker', 'x', 'x')")
            old.close()
            s = S.TaskStore(d, path=path)
            try:
                self.assertEqual(s.get(1)['labels'], [])
                self.assertEqual(s.edit(1, 'owner', labels=['ih'])['labels'], ['ih'])
            finally:
                s.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Api(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.srv = Server(self.dir, lambda s: APP.make_api_handler(s, lambda: {'version': APP.VERSION, 'counts': s.counts()}, lambda: {'api_token': WORKER, 'owner_token': OWNER}))
        self.p = self.srv.port

    def tearDown(self):
        self.srv.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_labels_over_http(self):
        code, r = call(self.p, 'POST', '/tasks', {'title': 'Fit a shelf', 'labels': ['House', 'ih']}, WORKER)
        self.assertEqual((code, r['task']['labels']), (200, ['house', 'ih']))
        code, r = call(self.p, 'POST', '/tasks/1/edit', {'labels': ['kindle']}, OWNER)
        self.assertEqual((code, r['labels']), (200, ['kindle']))
        self.assertEqual(call(self.p, 'POST', '/tasks/1/edit', {'labels': ['bad label']}, OWNER)[0], 400)
        code, r = call(self.p, 'GET', '/labels', token=WORKER)
        self.assertEqual((code, r), (200, {'labels': [{'label': 'kindle', 'count': 1}]}))
        code, r = call(self.p, 'GET', '/tasks', token=WORKER)
        self.assertEqual(r['tasks'][0]['labels'], ['kindle'])
        code, r = call(self.p, 'GET', '/board', token=WORKER)
        self.assertEqual(r['columns'][0]['tasks'][0]['labels'], ['kindle'])


if __name__ == '__main__':
    unittest.main()
