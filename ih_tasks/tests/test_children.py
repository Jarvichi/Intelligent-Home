"""Child tasks: a parent's approval covers its children; the parent cannot go to Verifying while one is open."""
import os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import main as APP
from test_api import OWNER, WORKER, Server, call
from test_store import Case


class Children(Case):
    def parent(self, stage='implementing', title='Move five add-ons to databases'):
        tid = self.new(title, priority='high', due='2026-12-01')
        self.to_planning(tid)
        self.s.transition(tid, 'approved', 'owner')
        if stage == 'implementing':
            self.s.transition(tid, 'implementing', 'worker')
        return tid

    def test_child_starts_approved_and_inherits(self):
        p = self.parent()
        c = self.s.create_child(p, 'ih_jobs_metoffice database', 'worker', 'Push branch 4948849')
        self.assertEqual((c['stage'], c['parent_id'], c['priority'], c['due'], c['waiting_on']), ('approved', p, 'high', '2026-12-01', 'worker'))
        self.assertIn('Part of task #%d' % p, c['plan'])
        self.assertEqual(c['parent']['id'], p)
        t = self.s.get(p)
        self.assertEqual([x['id'] for x in t['children']], [c['id']])
        self.assertEqual((t['children_open'], t['children_total'], t['waiting_on']), (1, 1, 'children'))

    def test_parent_needs_approval_first_and_children_have_no_children(self):
        tid = self.new('Not approved yet'); self.to_planning(tid)
        self.refused('conflict', self.s.create_child, tid, 'x', 'worker')
        p = self.parent(); c = self.s.create_child(p, 'a', 'worker')
        self.refused('conflict', self.s.create_child, c['id'], 'b', 'worker')
        self.refused('forbidden', self.s.create_child, p, 'b', 'check')
        self.refused('bad', self.s.create_child, p, '  ', 'worker')

    def test_replan_is_a_move_back_to_planning_flagged_for_the_worker(self):
        p = self.parent()
        t = self.s.transition(p, 'planning', 'owner', reason='Replan requested by the owner: look at the current state.')
        self.assertEqual((t['stage'], t['revise'], t['waiting_on']), ('planning', True, 'worker'))
        self.assertIn('Replan requested', [e['text'] for e in t['history'] if e['kind'] == 'moved'][-1])
        self.refused('bad', self.s.transition, self.parent(title='Quite different second job'), 'planning', 'owner')   # a reason is needed

    def test_approved_parent_moves_to_implementing(self):
        p = self.parent('approved')
        self.s.create_child(p, 'a', 'worker')
        self.assertEqual(self.s.get(p)['stage'], 'implementing')

    def test_parent_cannot_verify_until_children_finished(self):
        p = self.parent()
        a = self.s.create_child(p, 'a', 'worker')['id']; b = self.s.create_child(p, 'b', 'worker')['id']
        self.refused('conflict', self.s.transition, p, 'verifying', 'worker')
        for c in (a, b):
            self.s.transition(c, 'implementing', 'worker'); self.s.transition(c, 'verifying', 'worker', verify_by='owner')
        self.refused('conflict', self.s.transition, p, 'verifying', 'worker')
        self.s.transition(a, 'done', 'owner')
        self.refused('conflict', self.s.transition, p, 'verifying', 'worker')
        self.assertEqual(self.s.get(p)['waiting_on'], 'children')
        self.s.close_task(b, 'worker', 'wont_do', 'not needed')          # Closed also counts as finished
        t = self.s.get(p)
        self.assertEqual((t['children_open'], t['waiting_on']), (0, 'worker'))
        self.assertIn('children_finished', [e['kind'] for e in t['history']])
        self.assertEqual(self.s.transition(p, 'verifying', 'worker', verify_by='owner')['stage'], 'verifying')

    def test_list_counts_and_limits_and_merge(self):
        p = self.parent(); c = self.s.create_child(p, 'a', 'worker')['id']
        row = [t for t in self.s.list() if t['id'] == p][0]
        self.assertEqual((row['children_open'], row['children_total']), (1, 1))
        self.assertEqual([t for t in self.s.list() if t['id'] == c][0]['parent_id'], p)
        self.assertEqual([t['id'] for t in self.s.list(waiting='children')], [p])
        other = self.new('Another task')
        self.refused('conflict', self.s.merge, other, [c], 'owner')
        for i in range(11):
            self.s.create_child(p, 'c%d' % i, 'worker')
        self.refused('conflict', self.s.create_child, p, 'one too many', 'worker')

    def test_duplicate_tools_ignore_child_tasks(self):
        p = self.parent()
        a = self.s.create_child(p, 'Database storage: ih_jobs_metoffice on its own SQLite file', 'worker')['id']
        b = self.s.create_child(p, 'Database storage: ih_orchestration_inbox on its own SQLite file', 'worker')['id']
        self.assertEqual(self.s.duplicates()['groups'], [])
        # a new ordinary task is not auto-closed because it resembles a child's title
        r = self.s.create('Database storage: ih_jobs_metoffice on its own SQLite file', 'worker')
        self.assertIsNone(r['duplicate_of'])
        # but two ordinary tasks still match
        x = self.new('Fit a smoke alarm in the hall')
        self.s.create('Fit a smoke alarm in the hall.', 'worker', allow_duplicate=True)
        self.assertTrue(self.s.duplicates()['groups'])

    def test_old_database_gets_the_column(self):
        import sqlite3, store as S
        d = tempfile.mkdtemp()
        try:
            st = S.TaskStore(d); tid = st.create('old', 'worker')['task']['id']; st.close()
            st = S.TaskStore(d); self.assertIsNone(st.get(tid)['parent_id']); st.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Api(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.srv = Server(self.dir, lambda s: APP.make_api_handler(s, lambda: {'version': APP.VERSION, 'counts': s.counts()}, lambda: {'api_token': WORKER, 'owner_token': OWNER}))
        self.p = self.srv.port

    def tearDown(self):
        self.srv.stop(); shutil.rmtree(self.dir, ignore_errors=True)

    def test_children_over_http(self):
        c, t = call(self.p, 'POST', '/tasks', {'title': 'Big job'}, WORKER); n = t['task']['id']
        call(self.p, 'POST', '/tasks/%d/move' % n, {'to': 'planning', 'plan': 'p'}, WORKER)
        code, _ = call(self.p, 'POST', '/tasks/%d/children' % n, {'title': 'piece'}, WORKER)
        self.assertEqual(code, 409)                                  # not approved yet
        call(self.p, 'POST', '/tasks/%d/move' % n, {'to': 'approved'}, OWNER)
        code, ch = call(self.p, 'POST', '/tasks/%d/children' % n, {'title': 'piece one', 'description': 'd'}, WORKER)
        self.assertEqual((code, ch['stage'], ch['parent_id']), (200, 'approved', n))
        code, par = call(self.p, 'GET', '/tasks/%d' % n, None, WORKER)
        self.assertEqual((par['stage'], par['children'][0]['id']), ('implementing', ch['id']))
        code, _ = call(self.p, 'POST', '/tasks/%d/move' % n, {'to': 'verifying'}, WORKER)
        self.assertEqual(code, 409)
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/children' % n, {'nope': 1}, WORKER)[0], 400)


if __name__ == '__main__':
    unittest.main()
