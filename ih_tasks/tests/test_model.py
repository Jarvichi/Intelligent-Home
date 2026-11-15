"""Rules: who may move a task where, close reasons, question sets, duplicate titles. Run: python3 -m unittest discover -s ih_tasks/tests"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import model as M


def task(stage, **kw):
    return dict({'id': 1, 'stage': stage, 'plan': '', 'verify_by': 'check'}, **kw)


class Transitions(unittest.TestCase):
    def refused(self, code, *a, **kw):
        with self.assertRaises(M.RuleError) as c:
            M.check_transition(*a, **kw)
        self.assertEqual(c.exception.code, code)

    def test_worker_plans_and_needs_a_plan(self):
        self.refused('bad', task('todo'), 'planning', 'worker')
        self.assertEqual(M.check_transition(task('todo'), 'planning', 'worker', plan='Do X')['plan'], 'Do X')
        M.check_transition(task('todo', plan='old'), 'planning', 'worker')
        self.refused('forbidden', task('todo'), 'planning', 'owner', plan='p')

    def test_only_the_owner_approves(self):
        for actor in ('worker', 'check', 'system'):
            self.refused('forbidden', task('planning', plan='p'), 'approved', actor)
        M.check_transition(task('planning', plan='p'), 'approved', 'owner')

    def test_approve_waits_for_answers(self):
        self.refused('conflict', task('planning', plan='p'), 'approved', 'owner', has_open_question=True)

    def test_no_skipping_stages(self):
        self.refused('conflict', task('todo'), 'approved', 'owner')
        self.refused('conflict', task('todo'), 'implementing', 'worker')
        self.refused('conflict', task('planning'), 'implementing', 'worker')
        self.refused('conflict', task('approved'), 'verifying', 'worker')
        self.refused('conflict', task('implementing'), 'done', 'owner')
        self.refused('conflict', task('approved'), 'approved', 'owner')

    def test_worker_does_the_work(self):
        M.check_transition(task('approved'), 'implementing', 'worker')
        M.check_transition(task('implementing'), 'verifying', 'worker')
        self.refused('forbidden', task('approved'), 'implementing', 'owner')

    def test_verifying_to_done(self):
        self.refused('forbidden', task('verifying'), 'done', 'worker')
        self.refused('bad', task('verifying'), 'done', 'check')                       # a check must give its result
        M.check_transition(task('verifying'), 'done', 'check', result='3 tests passed')
        M.check_transition(task('verifying'), 'done', 'owner')
        self.refused('forbidden', task('verifying', verify_by='owner'), 'done', 'check', result='ok')
        M.check_transition(task('verifying', verify_by='owner'), 'done', 'owner')

    def test_going_back_needs_a_reason(self):
        for frm in ('approved', 'implementing', 'verifying'):
            self.refused('bad', task(frm), 'planning', 'worker')
            M.check_transition(task(frm), 'planning', 'worker', reason='plan was wrong')
        self.refused('bad', task('verifying'), 'implementing', 'check')
        M.check_transition(task('verifying'), 'implementing', 'check', reason='test failed')

    def test_reopen_is_the_owners(self):
        self.refused('forbidden', task('closed'), 'todo', 'worker', reason='x')
        self.refused('bad', task('closed'), 'todo', 'owner')
        M.check_transition(task('closed'), 'todo', 'owner', reason='still wanted')
        M.check_transition(task('done'), 'todo', 'owner', reason='came back')

    def test_closed_is_not_a_move(self):
        self.refused('bad', task('todo'), 'closed', 'owner', reason='x')
        self.refused('bad', task('todo'), 'nowhere', 'owner')
        self.refused('bad', task('todo'), 'planning', 'somebody', plan='p')

    def test_every_listed_transition_names_known_stages_and_actors(self):
        for (frm, to), (actors, needs) in M.TRANSITIONS.items():
            self.assertIn(frm, M.STAGES); self.assertIn(to, M.STAGES)
            self.assertTrue(set(actors) <= set(M.ACTORS))
            self.assertTrue(set(needs) <= {'plan', 'reason', 'no_question'})
        self.assertEqual([k for k, v in M.TRANSITIONS.items() if k[1] == 'approved'], [('planning', 'approved')])
        self.assertEqual(M.TRANSITIONS[('planning', 'approved')][0], ('owner',))


class Closing(unittest.TestCase):
    def refused(self, code, *a, **kw):
        with self.assertRaises(M.RuleError) as c:
            M.check_close(*a, **kw)
        self.assertEqual(c.exception.code, code)

    def test_reason_is_required(self):
        self.refused('bad', task('todo'), 'owner', '')
        self.refused('bad', task('todo'), 'owner', 'because')
        for stage in M.OPEN_STAGES:
            self.assertEqual(M.check_close(task(stage), 'owner', 'wont_do')['reason_kind'], 'wont_do')

    def test_duplicate_needs_the_other_task(self):
        self.refused('bad', task('todo'), 'owner', 'duplicate')
        self.refused('bad', task('todo'), 'owner', 'duplicate', duplicate_of=1)
        self.refused('bad', task('todo'), 'owner', 'duplicate', duplicate_of=True)
        self.assertEqual(M.check_close(task('todo'), 'owner', 'duplicate', duplicate_of=7)['duplicate_of'], 7)
        self.assertIsNone(M.check_close(task('todo'), 'owner', 'dropped', duplicate_of=7)['duplicate_of'])

    def test_who_may_close(self):
        self.refused('forbidden', task('todo'), 'check', 'dropped')
        self.refused('bad', task('todo'), 'worker', 'wont_do')                         # a worker must say why
        M.check_close(task('todo'), 'worker', 'wont_do', 'superseded by the new board')
        M.check_close(task('implementing'), 'worker', 'already_done')
        self.refused('conflict', task('closed'), 'owner', 'dropped')
        self.refused('conflict', task('done'), 'owner', 'dropped')


class Questions(unittest.TestCase):
    def bad(self, fn, *a):
        with self.assertRaises(M.RuleError) as c:
            fn(*a)
        self.assertEqual(c.exception.code, 'bad')

    def test_types(self):
        qs = M.clean_questions([{'text': 'Which?', 'type': 'choice', 'options': ['A', 'B']}, {'text': 'Any of?', 'type': 'multi', 'options': ['x', 'y', 'z']},
                                {'text': 'OK?', 'type': 'yesno'}, {'text': 'Say more'}])
        self.assertEqual([q['id'] for q in qs], ['q1', 'q2', 'q3', 'q4'])
        self.assertEqual([q['type'] for q in qs], ['choice', 'multi', 'yesno', 'text'])

    def test_bad_sets(self):
        self.bad(M.clean_questions, [])
        self.bad(M.clean_questions, 'what?')
        self.bad(M.clean_questions, [{'text': 'Which?', 'type': 'choice', 'options': ['only one']}])
        self.bad(M.clean_questions, [{'text': 'Which?', 'type': 'choice', 'options': ['a', 'a']}])
        self.bad(M.clean_questions, [{'text': 'OK?', 'type': 'yesno', 'options': ['y', 'n']}])
        self.bad(M.clean_questions, [{'text': '', 'type': 'text'}])
        self.bad(M.clean_questions, [{'text': 'x', 'type': 'slider'}])
        self.bad(M.clean_questions, [{'id': 'a', 'text': 'x'}, {'id': 'a', 'text': 'y'}])
        self.bad(M.clean_questions, [{'text': 'x', 'default': 'y'}])
        self.bad(M.clean_questions, [{'text': 'x'}] * (M.MAX_QUESTIONS + 1))

    def test_answers(self):
        qs = M.clean_questions([{'text': 'Which?', 'type': 'choice', 'options': ['A', 'B']}, {'text': 'Any?', 'type': 'multi', 'options': ['x', 'y']},
                                {'text': 'OK?', 'type': 'yesno'}, {'text': 'More'}])
        good = {'q1': 'B', 'q2': ['x'], 'q3': False, 'q4': ' seven days '}
        self.assertEqual(M.clean_answers(qs, good), {'q1': 'B', 'q2': ['x'], 'q3': False, 'q4': 'seven days'})
        self.assertEqual(M.clean_answers(qs, dict(good, q2=[]))['q2'], [])
        for wrong in (dict(good, q1='C'), dict(good, q2=['z']), dict(good, q2='x'), dict(good, q3='yes'), dict(good, q4=''), dict(good, q9='x'),
                      {k: v for k, v in good.items() if k != 'q3'}, 'B'):
            self.bad(M.clean_answers, qs, wrong)


class Small(unittest.TestCase):
    def test_due(self):
        self.assertEqual(M.check_due('2026-11-20'), '2026-11-20')
        self.assertEqual(M.check_due('2026-11-20T09:00:00+00:00'), '2026-11-20')
        self.assertEqual(M.check_due(None), '')
        with self.assertRaises(M.RuleError):
            M.check_due('next week')

    def test_legacy(self):
        self.assertEqual(M.check_legacy([{'kind': 'ok_number', 'value': '12'}, {'kind': 'ok_number', 'value': '12'}]), [{'kind': 'ok_number', 'value': '12'}])
        for bad in ([{'kind': 'ticket', 'value': '1'}], [{'kind': 'ok_number', 'value': 12}], [{'kind': 'ok_number', 'value': ''}], 'x'):
            with self.assertRaises(M.RuleError):
                M.check_legacy(bad)

    def test_duplicates(self):
        self.assertGreaterEqual(M.similar('Fix the hall light automation', 'IH: fix the hall light automation'), 0.99)
        self.assertLess(M.similar('Fix the hall light automation', 'Order printer paper'), 0.4)
        self.assertEqual(M.similar('', 'x'), 0.0)
        hits = M.find_duplicates('Fix hall light automation', [(1, 'Order printer paper'), (2, 'Fix the hall light automation'), (3, 'Fix hall light automations')])
        self.assertEqual([h[0] for h in hits][:1], [3])
        self.assertEqual({h[0] for h in hits}, {2, 3})
        self.assertEqual(M.find_duplicates('Something new entirely', [(1, 'Order printer paper')]), [])


if __name__ == '__main__':
    unittest.main()
