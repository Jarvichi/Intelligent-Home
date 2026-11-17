"""One-way mirror to a to-do list: the plan is pure; the run uses a fake Home Assistant."""
import os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import mirror as MR
from store import TaskStore


def task(i, stage='todo', **kw):
    return dict({'id': i, 'title': 'Task %d' % i, 'stage': stage, 'due': '', 'needs_input': False, 'waiting_on': 'worker', 'closed_reason': ''}, **kw)


class Plan(unittest.TestCase):
    def test_status_line(self):
        self.assertEqual(MR.status_line(task(12, 'planning', needs_input=True, waiting_on='owner')), 'Task #12 · Planning · needs your input')
        self.assertEqual(MR.status_line(task(3, 'planning', waiting_on='owner')), 'Task #3 · Planning · waiting for you')
        self.assertEqual(MR.status_line(task(4, 'closed', closed_reason='wont_do', waiting_on='')), "Task #4 · Closed · Won't do")
        self.assertEqual(MR.status_line(task(5, 'implementing')), 'Task #5 · Implementing')

    def test_add_only_open_tasks_without_an_item(self):
        ops = MR.plan([task(1, due='2026-12-01'), task(2, 'done', waiting_on='')], [], {})
        self.assertEqual(ops, [{'op': 'add', 'task': 1, 'summary': 'Task 1', 'due': '2026-12-01', 'description': 'Task #1 · To do', 'status': 'needs_action'}])

    def test_update_only_what_differs(self):
        items = [{'uid': 'u1', 'summary': 'Task 1', 'status': 'needs_action', 'description': 'long old text [needs-ok #4]', 'due': '2026-11-30'},
                 {'uid': 'u2', 'summary': 'Old title', 'status': 'needs_action', 'description': 'Task #2 · To do'},
                 {'uid': 'u3', 'summary': 'Task 3', 'status': 'needs_action', 'description': 'Task #3 · To do'},
                 {'uid': 'zz', 'summary': 'Not a task at all', 'status': 'needs_action'}]
        ops = MR.plan([task(1, due='2026-11-30'), task(2, 'done', waiting_on=''), task(3)], items, {1: 'u1', 2: 'u2', 3: 'u3'})
        self.assertEqual(ops, [{'op': 'update', 'task': 1, 'uid': 'u1', 'changes': {'description': 'Task #1 · To do'}},
                               {'op': 'update', 'task': 2, 'uid': 'u2', 'changes': {'summary': 'Task 2', 'description': 'Task #2 · Done', 'status': 'completed'}}])
        self.assertFalse(any(o.get('uid') == 'zz' for o in ops))                     # items of no task are never touched

    def test_deleted_item_is_not_made_again_and_marker_is_adopted(self):
        self.assertEqual(MR.plan([task(1)], [], {1: 'gone'}), [])
        ops = MR.plan([task(7)], [{'uid': 'u7', 'summary': 'Task 7', 'status': 'needs_action', 'description': 'Task #7 · To do'}], {})
        self.assertEqual(ops, [{'op': 'adopt', 'task': 7, 'uid': 'u7'}])


class FakeHA:
    def __init__(self, items):
        self.items, self.calls, self.n = items, [], 0

    def post(self, path, data):
        return {'service_response': {data['entity_id']: {'items': list(self.items)}}}

    def call_service(self, domain, service, data):
        self.calls.append((service, data))
        if service == 'add_item':
            self.n += 1
            self.items.append({'uid': 'new%d' % self.n, 'summary': data['item'], 'status': 'needs_action', 'description': data.get('description', ''), 'due': data.get('due_date', '')})
        else:
            it = next(i for i in self.items if i['uid'] == data['item'])
            for src, dst in (('rename', 'summary'), ('description', 'description'), ('status', 'status'), ('due_date', 'due')):
                if src in data:
                    it[dst] = data[src]


class Run(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.s = TaskStore(self.dir)
        self.log = []

    def tearDown(self):
        self.s.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_off_and_dry_change_nothing(self):
        self.s.create('Fit a shelf', 'worker')
        ha = FakeHA([])
        self.assertEqual(MR.run(ha, self.s, 'todo.x', 'off', self.log.append)['add'], 0)
        self.assertEqual(MR.run(ha, self.s, '', 'live', self.log.append)['add'], 0)
        self.assertEqual(MR.run(ha, self.s, 'todo.x', 'dry', self.log.append)['add'], 1)
        self.assertEqual(ha.calls, [])
        self.assertTrue(self.log and '[DRY]' in self.log[-1] and 'shelf' not in self.log[-1])     # counts only, never titles

    def test_live_adds_then_settles_then_follows_changes(self):
        old = self.s.create('Old item', 'system', legacy=[{'kind': 'todo_uid', 'value': 'old-uid'}])['task']['id']
        new = self.s.create('Fit a shelf', 'worker', due='2026-12-01')['task']['id']
        ha = FakeHA([{'uid': 'old-uid', 'summary': 'Old item', 'status': 'needs_action', 'description': 'tag soup'},
                     {'uid': 'other', 'summary': 'Buy milk', 'status': 'needs_action'}])
        r = MR.run(ha, self.s, 'todo.x', 'live', self.log.append)
        self.assertEqual((r['add'], r['update'], r['errors']), (1, 1, 0))
        self.assertEqual(self.s.mirror_rows()[new]['uid'], 'new1')
        self.assertEqual(ha.items[0]['description'], 'Task #%d · To do' % old)
        self.assertEqual(MR.run(ha, self.s, 'todo.x', 'live', self.log.append), {'mode': 'live', 'add': 0, 'update': 0, 'adopt': 0, 'errors': 0})
        self.s.close_task(new, 'owner', 'dropped')
        r = MR.run(ha, self.s, 'todo.x', 'live', self.log.append)
        self.assertEqual(r['update'], 1)
        self.assertEqual((ha.items[2]['status'], ha.items[2]['description']), ('completed', 'Task #%d · Closed · Dropped' % new))
        self.assertEqual(ha.items[1], {'uid': 'other', 'summary': 'Buy milk', 'status': 'needs_action'})

    def test_a_failing_call_is_counted_not_raised(self):
        self.s.create('Fit a shelf', 'worker')

        class Broken(FakeHA):
            def call_service(self, *a):
                raise OSError('down')
        r = MR.run(Broken([]), self.s, 'todo.x', 'live', self.log.append)
        self.assertEqual((r['add'], r['errors']), (1, 1))


if __name__ == '__main__':
    unittest.main()
