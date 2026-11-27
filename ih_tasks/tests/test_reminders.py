"""Reminders: set (closes the task), due, fire (reopens as high), notify the Claude inbox, API."""
import json, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import main as APP
import model as M
import reminders as R
from test_api import OWNER, WORKER, Server, call
from test_store import Case

DAY = 86400.0


class Resp:
    def __init__(self, status=200): self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Store(Case):
    def date(self, days):
        return self.s.today().fromordinal(self.s.today().toordinal() + days).isoformat()

    def test_set_closes_and_reopens_on_the_date(self):
        tid = self.new('DVLA: check registration reopened', priority='low')
        t = self.s.set_reminder(tid, 'owner', self.date(3), 'recheck about monthly')
        self.assertEqual((t['stage'], t['closed_reason'], t['remind_at']), ('closed', 'reminder', self.date(3)))
        self.assertEqual(self.s.due_reminders(), [])
        self.assertEqual([r['id'] for r in self.s.reminders()], [tid])
        self.t[0] += 3 * DAY
        due = self.s.due_reminders(); self.assertEqual((due[0]['id'], due[0]['note']), (tid, 'recheck about monthly'))
        t = self.s.fire_reminder(tid)
        self.assertEqual((t['stage'], t['priority'], t['remind_at'], t['closed_reason']), ('todo', 'high', '', ''))
        self.assertIn('reminder_fired', [e['kind'] for e in t['history']])
        self.refused('conflict', self.s.fire_reminder, tid)

    def test_rules(self):
        tid = self.new()
        for bad in ('', 'soon', '2026-13-40', self.date(0), self.date(-2), self.date(900)):
            self.refused('bad', self.s.set_reminder, tid, 'owner', bad)
        self.refused('forbidden', self.s.set_reminder, tid, 'check', self.date(2))
        self.refused('bad', self.s.set_reminder, tid, 'owner', self.date(2), 'x' * 600)
        self.refused('bad', self.s.close_task, tid, 'owner', 'reminder', 'sneaky')            # a plain close cannot use it
        self.s.set_reminder(tid, 'owner', self.date(2))
        self.refused('conflict', self.s.set_reminder, tid, 'owner', self.date(5))             # already closed

    def test_reopening_cancels_the_reminder(self):
        tid = self.new(); self.s.set_reminder(tid, 'owner', self.date(4))
        t = self.s.transition(tid, 'todo', 'owner', reason='changed my mind')
        self.assertEqual((t['stage'], t['remind_at']), ('todo', ''))
        self.assertEqual(self.s.reminders(), [])

    def test_parent_with_open_children_cannot_be_closed_by_a_reminder(self):
        p = self.new('Big job'); self.to_planning(p); self.s.transition(p, 'approved', 'owner')
        self.s.create_child(p, 'piece', 'worker')
        self.refused('conflict', self.s.set_reminder, p, 'owner', self.date(2))


class Pass(Store):
    def test_run_fires_after_the_hour_and_tells_the_inbox(self):
        tid = self.new('Renew the passport'); self.s.set_reminder(tid, 'owner', self.date(1), 'forms are in the drawer')
        self.t[0] += DAY
        sent = []
        def opener(req, timeout=0):
            sent.append((req.full_url, req.get_header('Authorization'), json.loads(req.data))); return Resp()
        opts = {'inbox_url': 'http://inbox:8800', 'inbox_token': 'TKN', 'reminder_hour': 8}
        self.assertEqual(R.run(self.s, opts, 6, opener=opener), 0)                  # too early
        self.assertEqual(self.s.get(tid)['stage'], 'closed')
        self.assertEqual(R.run(self.s, opts, 9, opener=opener, log=lambda m: None), 1)
        self.assertEqual(self.s.get(tid)['stage'], 'todo')
        url, auth, body = sent[0]
        self.assertEqual((url, auth, body['kind'], body['source']), ('http://inbox:8800/events', 'Bearer TKN', 'task_reminder', 'ih_tasks'))
        self.assertIn('#%d' % tid, body['text']); self.assertIn('forms are in the drawer', body['text'])

    def test_unreachable_inbox_waits_then_fires_a_day_late(self):
        tid = self.new('Book the MOT'); self.s.set_reminder(tid, 'owner', self.date(1))
        self.t[0] += DAY
        def down(req, timeout=0): raise OSError('no route')
        opts = {'inbox_url': 'http://inbox:8800', 'inbox_token': 'TKN', 'reminder_hour': 8}
        self.assertEqual(R.run(self.s, opts, 9, opener=down, log=lambda m: None), 0)
        self.assertEqual(self.s.get(tid)['stage'], 'closed')
        self.t[0] += DAY                                                              # a day late: never lost
        self.assertEqual(R.run(self.s, opts, 9, opener=down, log=lambda m: None), 1)
        self.assertEqual(self.s.get(tid)['stage'], 'todo')
        self.s.close()
        from store import TaskStore
        self.s = TaskStore(self.dir, clock=lambda: self.t[0])
        self.assertEqual(len(self.s.pending_reminder_notifications()), 1)
        R.run(self.s, {'reminder_hour': 8}, 9, log=lambda m: None)
        self.assertEqual(len(self.s.pending_reminder_notifications()), 1)
        sent = []
        def up(req, timeout=0):
            sent.append(json.loads(req.data)); return Resp()
        self.assertEqual(R.run(self.s, opts, 9, opener=up, log=lambda m: None), 0)
        self.assertEqual(sent[0]['ref'], 'task-%d-%s' % (tid, self.date(-1)))
        self.assertEqual(self.s.pending_reminder_notifications(), [])
        R.run(self.s, opts, 9, opener=up, log=lambda m: None)
        self.assertEqual(len(sent), 1)

    def test_no_inbox_configured_still_reopens(self):
        tid = self.new('Call the plumber'); self.s.set_reminder(tid, 'owner', self.date(1)); self.t[0] += DAY
        self.assertEqual(R.run(self.s, {'reminder_hour': 8}, 9, log=lambda m: None), 1)


class Api(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.srv = Server(self.dir, lambda s: APP.make_api_handler(s, lambda: {'version': APP.VERSION, 'counts': s.counts()}, lambda: {'api_token': WORKER, 'owner_token': OWNER}))
        self.p = self.srv.port

    def tearDown(self):
        self.srv.stop(); shutil.rmtree(self.dir, ignore_errors=True)

    def test_remind_over_http(self):
        c, t = call(self.p, 'POST', '/tasks', {'title': 'Think about the loft'}, OWNER); n = t['task']['id']
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/remind' % n, {'date': '2020-01-01'}, OWNER)[0], 400)
        import datetime
        d = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        code, r = call(self.p, 'POST', '/tasks/%d/remind' % n, {'date': d, 'note': 'quotes'}, OWNER)
        self.assertEqual((code, r['stage'], r['remind_at']), (200, 'closed', d))
        code, lst = call(self.p, 'GET', '/reminders', None, OWNER)
        self.assertEqual([x['id'] for x in lst['reminders']], [n])
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/remind' % n, {'nope': 1}, OWNER)[0], 400)


if __name__ == '__main__':
    unittest.main()
