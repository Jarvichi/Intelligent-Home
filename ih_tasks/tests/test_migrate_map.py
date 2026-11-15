"""Old tags -> stages. Made-up items only. Run: python3 -m unittest discover -s ih_tasks/tests"""
import contextlib, io, json, os, sys, tempfile, shutil, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', 'app'))
import migrate as MG
import migrate_map as MM
from store import TaskStore


def item(desc, uid='aaaaaaaa-1111-2222-3333-444444444444', summary='Fit a shelf', due='2026-12-01'):
    return {'uid': uid, 'summary': summary, 'description': desc, 'due': due, 'status': 'needs_action'}


def rule(desc, **kw):
    m = MM.map_todo(item(desc), **kw)
    return m['rule'], m['stage'], m['needs_input']


class Rules(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(rule(''), ('plain', 'todo', False))
        self.assertEqual(rule('Some notes. [queue 14 Nov] Checked, nothing to do yet. [job 20261113-094834]'), ('plain', 'todo', False))

    def test_needs_ok_waits_for_approve(self):
        m = MM.map_todo(item('Notes\n[needs-ok #21] OK to add the helper?'))
        self.assertEqual((m['rule'], m['stage'], m['needs_input']), ('needs-ok', 'planning', False))
        self.assertIn('Ask #21: OK to add the helper?', m['task']['plan'])
        self.assertIn({'kind': 'ok_number', 'value': '21'}, m['task']['legacy'])

    def test_needs_ok_with_form_is_planning_plus_needs_input(self):
        form = {'q': [{'id': 'q1', 'text': 'Move the page?', 'type': 'yesno'}, {'id': 'q2', 'text': 'Which name?', 'type': 'choice', 'options': ['A', 'B']},
                      {'id': 'q3', 'text': 'Broken choice', 'type': 'choice', 'options': ['only']}]}
        m = MM.map_todo(item('[needs-ok #85] 1. Move the page? 2. Which name?\n[questions #85] ' + json.dumps(form)))
        self.assertEqual((m['rule'], m['stage'], m['needs_input']), ('needs-ok-form', 'planning', True))
        self.assertEqual([(q['id'], q['type']) for q in m['task']['question']['questions']], [('q1', 'yesno'), ('q2', 'choice'), ('q3', 'text')])

    def test_form_of_an_answered_ask_is_ignored(self):
        self.assertEqual(rule('[done-approved #85 14 Nov] Move the page?\n[questions #85] {"q":[{"id":"q1","text":"Move?","type":"yesno"}]}'), ('ran-before', 'todo', False))

    def test_open_approval_is_the_only_way_into_approved(self):
        m = MM.map_todo(item('[queue 15 Nov] note\n[done-approved #126 15 Nov] first ask\n[queue 15 Nov] more\n[approved #126] May I add the client?'))
        self.assertEqual((m['rule'], m['stage']), ('approved-open', 'approved'))
        self.assertTrue(m['task']['plan'].startswith('Approved ask #126'))
        self.assertEqual(rule('[approved #126] May I?\n[done-approved #126 15 Nov] ran'), ('ran-before', 'todo', False))      # the LAST tag for a number wins
        for desc in ('', '[needs-ok #1] x', '[needs-you #2] y', 'PLAN (Claude): brain projects/x/plan', '[replan] bad', '[declined #3] no', 'WAITING for a part'):
            self.assertNotEqual(MM.map_todo(item(desc))['stage'], 'approved', desc)

    def test_needs_you_is_a_flag_and_done_you_clears_it(self):
        m = MM.map_todo(item('[needs-you #109] Unplug the charger for 2 days.'))
        self.assertEqual((m['rule'], m['stage'], m['needs_input']), ('needs-you', 'todo', True))
        self.assertEqual(m['task']['question']['questions'][0]['type'], 'yesno')
        self.assertIn('Unplug the charger', m['task']['question']['questions'][0]['text'])
        self.assertEqual(rule('PLAN (Claude): projects/x/plan\n[needs-you #109] Unplug it.'), ('needs-you', 'planning', True))
        self.assertEqual(rule('[needs-you #64] Do it.\n[done-you #64] 2026-11-13'), ('plain', 'todo', False))
        self.assertEqual(rule('[needs-you #103] '), ('needs-you', 'todo', True))

    def test_plans(self):
        self.assertEqual(rule('PLAN (Claude): brain projects/shelf/plan'), ('checked-plan', 'planning', False))
        self.assertEqual(rule('PLAN (unreviewed): brain projects/shelf/plan, AI job 1. [replan] It invents a sensor.\n[queue 14 Nov] PLAN (Claude, replanned): projects/shelf/plan'),
                         ('checked-plan', 'planning', False))
        self.assertEqual(rule('PLAN (unreviewed): brain projects/shelf/plan'), ('unreviewed-plan', 'todo', False))
        self.assertEqual(rule('[replan] It invents a sensor. (old plan: projects/shelf/plan) [job 1]'), ('replan', 'todo', False))
        self.assertEqual(rule('PLAN (unreviewed): x [replan] wrong'), ('replan', 'todo', False))

    def test_waiting(self):
        self.assertEqual(rule('WAITING for the part to arrive'), ('waiting-other', 'todo', False))
        self.assertEqual(rule('WAITING: Sam to phone the fitter', owner_name='Sam'), ('waiting-on-you', 'todo', True))
        self.assertEqual(rule('WAITING: Sam to phone the fitter\n[done-you 2026-11-14]', owner_name='Sam'), ('waiting-other', 'todo', False))
        m = MM.map_todo(item('WAITING: Sam decides the colour'), owner_name='Sam')
        self.assertIn({'kind': 'wy_id', 'value': 'wyaaaaaaaa'}, m['task']['legacy'])
        self.assertEqual(rule('while waiting for paint to dry'), ('plain', 'todo', False))

    def test_declined_ran_before_dropped(self):
        m = MM.map_todo(item('[declined #41] OK to update?'))
        self.assertEqual((m['rule'], m['stage']), ('declined', 'todo'))
        self.assertTrue(any("won't do" in f for f in m['flags']))
        self.assertEqual(rule('[done-approved #13 24 Oct] OK to add it?'), ('ran-before', 'todo', False))
        m = MM.map_todo(item('DROPPED 5 Nov (undo: untick) old text'))
        self.assertEqual((m['stage'], m['task']['close']['reason_kind']), ('closed', 'dropped'))

    def test_flags_and_limits(self):
        m = MM.map_todo(item('x' * 30000, summary='T' * 300, due=None))
        self.assertEqual((len(m['task']['title']), len(m['task']['description']), m['task']['due']), (200, 20000, ''))
        self.assertEqual(len(m['flags']), 3)
        self.assertEqual(MM.map_todo({'uid': 'u', 'summary': '', 'description': None})['task']['title'], '(no title)')

    def test_every_rule_is_listed(self):
        seen = {rule(d, owner_name='Sam')[0] for d in ('', 'DROPPED x', '[approved #1] a', '[needs-ok #1] a', '[needs-ok #1] a\n[questions #1] {"q":[{"text":"t"}]}',
                                                       '[needs-you #1] a', 'WAITING Sam to call', 'PLAN (Claude): p', '[replan] r', 'PLAN (unreviewed): p', 'WAITING x',
                                                       '[declined #1] a', '[done-approved #1 1 Nov] a')}
        self.assertEqual(seen, set(MM.RULES))


class Queue(unittest.TestCase):
    TODO = [item('[approved #126] May I?', uid='11111111-aaaa-bbbb-cccc-000000000001', summary='Publish the fact'),
            item('[needs-you #9] Find the key', uid='22222222-aaaa-bbbb-cccc-000000000002', summary='Open the shed'),
            item('', uid='33333333-aaaa-bbbb-cccc-000000000003', summary='Paint the fence')]

    def q(self, i, kind, ref, status, title='job', **kw):
        return dict({'id': 'job%d' % i, 'kind': kind, 'ref': ref, 'status': status, 'title': title, 'instruction': 'do it'}, **kw)

    def test_queue_jobs_join_their_task_or_are_counted(self):
        queue = [self.q(1, 'approved_request', '126', 'queued'), self.q(2, 'todo', '22222222-aaaa-bbbb-cccc-000000000002', 'waiting'),
                 self.q(3, 'todo', '22222222', 'waiting'), self.q(4, 'todo', '33333333-aaaa-bbbb-cccc-000000000003', 'running'),
                 self.q(5, 'todo', '99999999-aaaa-bbbb-cccc-000000000009', 'waiting'), self.q(6, 'todo', '33333333', 'done'), self.q(7, 'other', 'x', 'cancelled'),
                 self.q(8, 'review_batch', 'r', 'queued'), self.q(9, 'other', 'handoff-1', 'waiting', title='Handoff: ring the plumber'),
                 self.q(10, 'todo', 'zz', 'paused'), self.q(11, 'todo', 'short', 'queued', title='Revise OK #9: shed'), self.q(12, 'todo', '33333333', 'failed')]
        r = MM.map_all(self.TODO, queue)
        ids = lambda m: [x['value'] for x in m['task']['legacy'] if x['kind'] == 'queue_id']
        self.assertEqual([ids(m) for m in r['tasks']], [['job1'], ['job2', 'job3', 'job11'], ['job4', 'job12']])
        self.assertTrue(any('running now' in f for f in r['tasks'][2]['flags']))
        self.assertTrue(any('failed' in f for f in r['tasks'][2]['flags']))
        self.assertEqual([(o['task']['title'], o['stage'], ids(o)) for o in r['orphans']], [('Handoff: ring the plumber', 'todo', ['job9'])])
        self.assertEqual(r['skipped'], {'queue: finished (done or cancelled)': 2, 'queue: housekeeping job': 1, 'queue: its to-do is closed': 1, 'queue: unknown status': 1})
        c = r['counts']
        self.assertEqual((c['tasks'], c['by_stage'], c['needs_input']), (4, {'approved': 1, 'todo': 3}, 1))
        self.assertEqual(r['tasks'][0]['stage'], 'approved')          # a queue job never moves a task past Approved

    def test_shared_ask_number_stays_on_the_first_item(self):
        r = MM.map_all([item('[done-approved #5 1 Nov] a', uid='u1'), item('[done-approved #5 1 Nov] a', uid='u2')], [])
        self.assertIn({'kind': 'ok_number', 'value': '5'}, r['tasks'][0]['task']['legacy'])
        self.assertNotIn({'kind': 'ok_number', 'value': '5'}, r['tasks'][1]['task']['legacy'])
        self.assertTrue(r['tasks'][1]['flags'])

    def test_report_and_import_into_a_real_store(self):
        r = MM.map_all(self.TODO + [item('DROPPED old', uid='44444444-aaaa-bbbb-cccc-000000000004', summary='Old | thing')], [self.q(1, 'approved_request', '126', 'queued')])
        text = MM.report(r, when='2026-11-15 10:00')
        self.assertIn('DRY RUN: nothing was written anywhere.', text)
        self.assertIn('| Approved | 1 |', text)
        self.assertIn('Old / thing', text)
        d = tempfile.mkdtemp()
        try:
            s = TaskStore(d)
            for m in r['tasks'] + r['orphans']:
                self.assertFalse(s.import_task(m['task'])['existing'])
            self.assertEqual(s.counts(), {'todo': 2, 'planning': 0, 'approved': 1, 'implementing': 0, 'verifying': 0, 'done': 0, 'closed': 1})
            self.assertTrue(all(s.import_task(m['task'])['existing'] for m in r['tasks']))          # a second run adds nothing
            self.assertEqual(s.by_legacy('queue_id', 'job1')['stage'], 'approved')
            self.assertTrue(s.by_legacy('ok_number', '9')['needs_input'])
            s.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Cli(unittest.TestCase):
    def test_dry_run_from_files_writes_only_the_report(self):
        d = tempfile.mkdtemp()
        try:
            todo, queue, rep = os.path.join(d, 'todo.json'), os.path.join(d, 'queue.json'), os.path.join(d, 'report.md')
            with open(todo, 'w') as f:
                json.dump({'type': 'result', 'result': {'response': {'todo.claude_to_do': {'items': Queue.TODO + [dict(item('done one', uid='zz'), status='completed')]}}}}, f)
            with open(queue, 'w') as f:
                json.dump([{'id': 'j1', 'kind': 'approved_request', 'ref': '126', 'status': 'queued', 'title': 't'}], f)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(MG.main(['--todo-json', todo, '--queue-json', queue, '--report', rep]), 0)
            self.assertTrue(out.getvalue().startswith('DRY: 3 to-do items + 1 queue jobs -> 3 tasks'))
            with open(rep) as f:
                text = f.read()
            self.assertIn('Read 3 open to-do items and 1 work queue jobs. They become **3 tasks**.', text)
            self.assertEqual(sorted(os.listdir(d)), ['queue.json', 'report.md', 'todo.json'])
            with self.assertRaises(SystemExit):
                MG.main(['--todo-json', todo, '--report', rep, '--live'])                      # live needs the API address and the owner token file
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
