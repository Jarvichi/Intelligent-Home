"""Storage: the whole life of a task, history, questions, duplicates, old ids, import. Temp folders only."""
import os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import model as M
from store import TaskStore, waiting_on


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.t = [1000000.0]
        self.s = TaskStore(self.dir, clock=lambda: self.t[0])

    def tearDown(self):
        self.s.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def refused(self, code, fn, *a, **kw):
        with self.assertRaises(M.RuleError) as c:
            fn(*a, **kw)
        self.assertEqual(c.exception.code, code)

    def new(self, title='Fit a shelf in the garage', **kw):
        return self.s.create(title, 'worker', **kw)['task']['id']

    def to_planning(self, tid, plan='Buy brackets. Fit them.'):
        return self.s.transition(tid, 'planning', 'worker', plan=plan)


class Life(Case):
    def test_whole_life_with_history(self):
        tid = self.new(due='2026-12-01')
        self.assertEqual(tid, 1)
        t = self.to_planning(tid)
        self.assertEqual((t['stage'], t['waiting_on'], t['plan']), ('planning', 'owner', 'Buy brackets. Fit them.'))
        self.refused('forbidden', self.s.transition, tid, 'approved', 'worker')
        self.assertEqual(self.s.transition(tid, 'approved', 'owner')['waiting_on'], 'worker')
        self.s.transition(tid, 'implementing', 'worker')
        t = self.s.transition(tid, 'verifying', 'worker', verify_by='check')
        self.assertEqual(t['waiting_on'], 'check')
        t = self.s.transition(tid, 'done', 'check', result='Shelf holds 20 kg')
        self.assertEqual((t['stage'], t['result'], t['waiting_on']), ('done', 'Shelf holds 20 kg', ''))
        moves = [(h['from'], h['to'], h['actor']) for h in t['history'] if h['kind'] == 'moved']
        self.assertEqual(moves, [('todo', 'planning', 'worker'), ('planning', 'approved', 'owner'), ('approved', 'implementing', 'worker'),
                                 ('implementing', 'verifying', 'worker'), ('verifying', 'done', 'check')])
        self.assertEqual(t['history'][0]['kind'], 'created')
        self.assertEqual(self.s.counts()['done'], 1)

    def test_refused_move_changes_nothing(self):
        tid = self.new()
        self.refused('conflict', self.s.transition, tid, 'approved', 'owner')
        t = self.s.get(tid)
        self.assertEqual(t['stage'], 'todo')
        self.assertEqual(len(t['history']), 1)

    def test_owner_tick_when_it_needs_his_eyes(self):
        tid = self.new(); self.to_planning(tid); self.s.transition(tid, 'approved', 'owner'); self.s.transition(tid, 'implementing', 'worker')
        t = self.s.transition(tid, 'verifying', 'worker', verify_by='owner')
        self.assertEqual(t['waiting_on'], 'owner')
        self.refused('forbidden', self.s.transition, tid, 'done', 'check', result='looks fine')
        self.assertEqual(self.s.transition(tid, 'done', 'owner')['stage'], 'done')

    def test_reject_keeps_planning_and_flags_revise(self):
        tid = self.new(); self.to_planning(tid)
        self.refused('forbidden', self.s.reject, tid, 'worker', 'no')
        self.refused('bad', self.s.reject, tid, 'owner', '  ')
        t = self.s.reject(tid, 'owner', 'Use the other wall')
        self.assertEqual((t['stage'], t['revise'], t['waiting_on']), ('planning', True, 'worker'))
        self.assertEqual(t['history'][-1]['kind'], 'rejected')
        t = self.s.set_plan(tid, 'worker', 'Use the north wall.')
        self.assertEqual((t['revise'], t['waiting_on'], t['plan']), (False, 'owner', 'Use the north wall.'))
        self.refused('forbidden', self.s.set_plan, tid, 'owner', 'my own plan')

    def test_approved_plan_cannot_be_edited_in_place(self):
        tid = self.new(); self.to_planning(tid); self.s.transition(tid, 'approved', 'owner')
        self.refused('conflict', self.s.set_plan, tid, 'worker', 'A different plan')
        self.refused('bad', self.s.transition, tid, 'implementing', 'worker', plan='sneaky new plan')
        t = self.s.transition(tid, 'planning', 'worker', reason='the wall has pipes in it')
        self.assertEqual((t['stage'], t['revise']), ('planning', True))
        self.refused('forbidden', self.s.transition, tid, 'approved', 'worker')

    def test_close_needs_reason_and_reopen(self):
        tid = self.new()
        self.refused('bad', self.s.close_task, tid, 'owner', '')
        t = self.s.close_task(tid, 'owner', 'wont_do', 'Garage is being cleared')
        self.assertEqual((t['stage'], t['closed_reason'], t['closed_detail']), ('closed', 'wont_do', 'Garage is being cleared'))
        self.assertEqual(t['history'][-1]['data']['reason'], 'wont_do')
        self.refused('forbidden', self.s.transition, tid, 'todo', 'worker', reason='again')
        t = self.s.transition(tid, 'todo', 'owner', reason='wanted after all')
        self.assertEqual((t['stage'], t['closed_reason']), ('todo', ''))

    def test_close_as_duplicate_checks_the_other_task(self):
        a, b = self.new(), self.new('Something quite different')
        self.refused('missing', self.s.close_task, b, 'worker', 'duplicate', duplicate_of=99)
        self.assertEqual(self.s.close_task(b, 'worker', 'duplicate', duplicate_of=a)['duplicate_of'], a)

    def test_comments_are_notes_only(self):
        tid = self.new()
        t = self.s.comment(tid, 'owner', 'Use oak if they have it')
        self.assertEqual((t['stage'], len(t['comments'])), ('todo', 1))
        self.assertEqual(sum(self.s.counts().values()), 1)
        self.refused('bad', self.s.comment, tid, 'owner', '')
        self.refused('missing', self.s.comment, 42, 'owner', 'x')

    def test_links(self):
        tid = self.new()
        t = self.s.add_link(tid, 'worker', 'pr', 'https://example.invalid/pull/1', 'Pull request')
        self.assertEqual(t['links'][0]['kind'], 'pr')
        self.refused('bad', self.s.add_link, tid, 'worker', 'pr', 'javascript:alert(1)')
        self.refused('bad', self.s.add_link, tid, 'worker', 'result')
        self.refused('bad', self.s.add_link, tid, 'worker', 'photo', '', 'x')

    def test_priority(self):
        tid = self.new()
        self.assertEqual(self.s.get(tid)['priority'], 'normal')
        t = self.s.edit(tid, 'owner', priority='HIGH')
        self.assertEqual(t['priority'], 'high')
        self.assertEqual(self.s.list()[0]['priority'], 'high')
        self.refused('bad', self.s.edit, tid, 'owner', priority='urgent')
        self.assertEqual(self.s.create('Low one', 'worker', priority='low')['task']['priority'], 'low')
        self.refused('bad', self.s.create, 'Bad one', 'worker', priority='asap')

    def test_edit(self):
        tid = self.new()
        t = self.s.edit(tid, 'owner', due='2027-01-05', title='Fit two shelves')
        self.assertEqual((t['title'], t['due']), ('Fit two shelves', '2027-01-05'))
        self.refused('bad', self.s.edit, tid, 'owner')
        self.refused('forbidden', self.s.edit, tid, 'check', title='x')


class QuestionsFlag(Case):
    Q = [{'text': 'Which wall?', 'type': 'choice', 'options': ['North', 'South']}, {'text': 'Paint it?', 'type': 'yesno'}]

    def test_ask_answer_resumes_same_task(self):
        tid = self.new(); self.to_planning(tid)
        t = self.s.ask(tid, 'worker', self.Q, 'I update the plan with your answers, then you approve it.')
        self.assertEqual((t['stage'], t['needs_input'], t['waiting_on']), ('planning', True, 'owner'))
        self.assertEqual(t['question']['after'], 'I update the plan with your answers, then you approve it.')
        self.refused('conflict', self.s.ask, tid, 'worker', self.Q, 'again')                   # at most one open set
        self.refused('conflict', self.s.transition, tid, 'approved', 'owner')                  # answers first, approval last
        self.refused('forbidden', self.s.answer, tid, 'worker', {'q1': 'North', 'q2': True})
        self.refused('bad', self.s.answer, tid, 'owner', {'q1': 'North'})
        t = self.s.answer(tid, 'owner', {'q1': 'North', 'q2': False}, 'Oak please')
        self.assertEqual((t['stage'], t['needs_input'], t['revise'], t['waiting_on']), ('planning', False, True, 'worker'))
        self.assertEqual(sum(self.s.counts().values()), 1)                                    # no new item was made
        self.assertEqual(t['past_questions'][0]['answers'], {'q1': 'North', 'q2': False})
        self.assertEqual(t['history'][-1]['data']['answers']['q1'], 'North')
        self.s.set_plan(tid, 'worker', 'North wall, no paint, oak.')
        self.assertEqual(self.s.transition(tid, 'approved', 'owner')['stage'], 'approved')
        self.s.ask(tid, 'worker', [{'text': 'Second set?', 'type': 'yesno'}], 'I carry on.')    # a later set is fine once the first is answered

    def test_question_in_other_stages_keeps_the_stage(self):
        tid = self.new(); self.to_planning(tid); self.s.transition(tid, 'approved', 'owner'); self.s.transition(tid, 'implementing', 'worker')
        self.s.ask(tid, 'worker', [{'text': 'Is the power off?', 'type': 'yesno'}], 'I fit the bracket.')
        t = self.s.answer(tid, 'owner', {'q1': True})
        self.assertEqual((t['stage'], t['revise'], t['waiting_on']), ('implementing', False, 'worker'))

    def test_withdraw_and_close_clear_the_flag(self):
        a, b = self.new(), self.new('Another very different job')
        self.s.ask(a, 'worker', self.Q, 'x'); self.s.ask(b, 'worker', self.Q, 'x')
        self.refused('bad', self.s.withdraw_question, a, 'worker', '')
        self.assertFalse(self.s.withdraw_question(a, 'worker', 'found the answer in the notes')['needs_input'])
        self.refused('conflict', self.s.withdraw_question, a, 'worker', 'again')
        self.assertFalse(self.s.close_task(b, 'owner', 'dropped')['needs_input'])
        self.refused('conflict', self.s.ask, b, 'worker', self.Q, 'x')
        self.refused('forbidden', self.s.ask, a, 'owner', self.Q, 'x')

    def test_list_filters(self):
        a = self.new(); b = self.new('Clean the gutters'); c = self.new('Renew the car insurance')
        self.to_planning(b); self.s.ask(c, 'worker', self.Q, 'x')
        ids = lambda **kw: [t['id'] for t in self.s.list(**kw)]
        self.assertEqual(ids(), [a, b, c])
        self.assertEqual(ids(stages=['planning']), [b])
        self.assertEqual(ids(needs_input=True), [c])
        self.assertEqual(ids(needs_input=False), [a, b])
        self.assertEqual(ids(waiting='owner'), [b, c])
        self.assertEqual(ids(waiting='worker'), [a])
        self.assertEqual(ids(q='GUTTER'), [b])
        self.assertEqual(ids(q='#3'), [c])
        self.assertNotIn('plan', self.s.list()[0])
        self.refused('bad', self.s.list, stages=['later'])
        board = self.s.board()
        self.assertEqual([col['stage'] for col in board['columns']], list(M.STAGES))
        self.assertEqual((board['needs_input'], board['total']), (1, 3))


class Duplicates(Case):
    def test_duplicate_is_linked_and_closed(self):
        a = self.new('Fix the hall light automation')
        r = self.s.create('IH: Fix the hall light automation', 'worker')
        self.assertEqual(r['duplicate_of'], a)
        self.assertEqual((r['task']['stage'], r['task']['closed_reason'], r['task']['duplicate_of']), ('closed', 'duplicate', a))
        self.assertEqual(self.s.get(a)['history'][-1]['kind'], 'duplicate_linked')
        self.assertEqual(self.s.get(a)['stage'], 'todo')

    def test_allow_duplicate_and_closed_tasks_do_not_count(self):
        a = self.new('Fix the hall light automation')
        self.assertIsNone(self.s.create('Fix the hall light automation', 'worker', allow_duplicate=True)['duplicate_of'])
        b = self.new('Descale the kettle')
        self.s.close_task(b, 'owner', 'dropped')
        self.assertIsNone(self.s.create('Descale the kettle', 'owner')['duplicate_of'])        # only OPEN tasks are compared

    def test_legacy_ids(self):
        r = self.s.create('Fix the hall light automation', 'system', legacy=[{'kind': 'todo_uid', 'value': 'abc-1'}, {'kind': 'ok_number', 'value': '12'}])
        again = self.s.create('Some other title', 'system', legacy=[{'kind': 'todo_uid', 'value': 'abc-1'}])
        self.assertTrue(again['existing'])
        self.assertEqual(again['task']['id'], r['task']['id'])
        self.assertEqual(self.s.by_legacy('ok_number', '12')['id'], r['task']['id'])
        self.assertIsNone(self.s.by_legacy('ok_number', '13'))
        other = self.new('Bleed the radiators')
        self.refused('conflict', self.s.add_legacy, other, 'system', [{'kind': 'ok_number', 'value': '12'}])
        self.assertEqual(len(self.s.add_legacy(other, 'system', [{'kind': 'queue_id', 'value': 'q9'}])['legacy']), 1)


class Import(Case):
    def test_import_stages_and_idempotent(self):
        r = self.s.import_task({'title': 'Old approved job', 'stage': 'approved', 'plan': 'Approved ask #5: do it', 'description': 'long old text',
                                'legacy': [{'kind': 'todo_uid', 'value': 'u-1'}, {'kind': 'ok_number', 'value': '5'}], 'why': 'rule approved-open'})
        t = r['task']
        self.assertEqual((t['stage'], t['history'][0]['kind'], t['history'][0]['actor'], t['history'][0]['text']), ('approved', 'imported', 'system', 'rule approved-open'))
        self.assertTrue(self.s.import_task({'title': 'Old approved job', 'stage': 'todo', 'legacy': [{'kind': 'todo_uid', 'value': 'u-1'}]})['existing'])
        self.assertEqual(sum(self.s.counts().values()), 1)

    def test_import_with_question_and_closed(self):
        t = self.s.import_task({'title': 'Needs a tracking number', 'stage': 'todo', 'question': {'questions': [{'text': 'Is it done?', 'type': 'yesno'}], 'after': 'I carry on.'}})['task']
        self.assertTrue(t['needs_input'])
        c = self.s.import_task({'title': 'Dropped thing', 'stage': 'closed', 'close': {'reason_kind': 'dropped', 'detail': 'old list'}})['task']
        self.assertEqual((c['stage'], c['closed_reason']), ('closed', 'dropped'))

    def test_import_limits(self):
        for stage in ('implementing', 'verifying', 'done', 'nowhere'):
            self.refused('bad', self.s.import_task, {'title': 'x', 'stage': stage, 'plan': 'p'})
        self.refused('bad', self.s.import_task, {'title': 'x', 'stage': 'approved'})                       # needs a plan
        self.refused('bad', self.s.import_task, {'title': 'x', 'stage': 'closed'})                         # needs a reason
        self.refused('bad', self.s.import_task, {'title': 'x', 'stage': 'todo', 'close': {'reason_kind': 'dropped'}})
        self.refused('bad', self.s.import_task, {'title': 'x', 'stage': 'todo', 'colour': 'red'})
        self.assertEqual(sum(self.s.counts().values()), 0)


class Reopen(Case):
    def test_data_survives_reopening_the_file(self):
        tid = self.new(); self.to_planning(tid)
        self.s.close()
        self.s = TaskStore(self.dir)
        self.assertEqual(self.s.get(tid)['stage'], 'planning')
        self.assertEqual(self.new('A second task after the restart'), 2)

    def test_waiting_on(self):
        self.assertEqual(waiting_on({'stage': 'approved'}), 'worker')
        self.assertEqual(waiting_on({'stage': 'approved', 'needs_input': True}), 'owner')
        self.assertEqual(waiting_on({'stage': 'closed', 'needs_input': True}), '')


if __name__ == '__main__':
    unittest.main()
