"""Merge and likely duplicates: what moves to the kept task, what is refused, all-or-nothing, grouping, who may call it. Made-up tasks, temp folders only."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as APP
import model as M
from test_api import OWNER, WORKER, Server, call
from test_store import Case

import shutil, tempfile


def kinds(task):
    return [h['kind'] for h in task['history']]


class Merge(Case):
    def new(self, title, **kw):
        return self.s.create(title, 'worker', allow_duplicate=True, **kw)['task']['id']

    def test_comments_links_old_ids_and_due_move_to_the_kept_task(self):
        a = self.new('Handoff: descale the kettle', due='2026-12-20', legacy=[{'kind': 'todo_uid', 'value': 'uid-a'}])
        b = self.new('Handoff from robin: descale the kettle', due='2026-12-05',
                     legacy=[{'kind': 'todo_uid', 'value': 'uid-b'}, {'kind': 'queue_id', 'value': 'q-7'}, {'kind': 'ok_number', 'value': '31'}, {'kind': 'wy_id', 'value': 'wy-2'}])
        self.s.comment(a, 'owner', 'Use the citric acid')
        self.s.comment(b, 'worker', 'Kettle is in the utility room')
        self.s.comment(b, 'owner', 'Before the weekend please')
        self.s.add_link(a, 'worker', 'note', text='same note')
        self.s.add_link(b, 'worker', 'note', text='same note')
        self.s.add_link(b, 'worker', 'pr', url='https://example.invalid/pull/9')
        r = self.s.merge(a, [b], 'owner')
        k, m = r['task'], self.s.get(b)
        self.assertEqual(r['merged'], [b])
        self.assertEqual([(c['actor'], c['text']) for c in k['comments']],
                         [('owner', 'Use the citric acid'), ('worker', 'From #2: Kettle is in the utility room'), ('owner', 'From #2: Before the weekend please')])
        self.assertEqual(k['comments'][1]['data']['merged_from'], b)
        self.assertEqual([(l['kind'], l['url'], l['text']) for l in k['links']], [('note', '', 'same note'), ('pr', 'https://example.invalid/pull/9', '')])
        self.assertEqual(k['due'], '2026-12-05')                                    # the earliest date
        self.assertEqual((k['stage'], m['stage'], m['closed_reason'], m['duplicate_of']), ('todo', 'closed', 'duplicate', a))
        for kind, value in (('todo_uid', 'uid-a'), ('todo_uid', 'uid-b'), ('queue_id', 'q-7'), ('ok_number', '31'), ('wy_id', 'wy-2')):
            self.assertEqual(self.s.by_legacy(kind, value)['id'], a, kind)           # every old id finds the kept task
        self.assertEqual(m['legacy'], [])
        self.assertEqual(len(m['comments']), 2)                                     # the merged task keeps its own history
        self.assertEqual(kinds(m)[-2:], ['closed', 'merged_into'])
        self.assertIn('merged', kinds(k))
        merged = [h for h in k['history'] if h['kind'] == 'merged'][0]
        self.assertEqual((merged['data']['task'], merged['data']['comments'], merged['data']['links'], len(merged['data']['old_ids'])), (b, 2, 1, 4))
        # each task stays tied to its own mirrored list item
        self.assertEqual({i: r['uid'] for i, r in self.s.mirror_rows().items()}, {a: 'uid-a', b: 'uid-b'})

    def test_due_date_stays_when_the_kept_task_is_earliest_or_the_only_one(self):
        a, b, c = self.new('Task one', due='2026-11-20'), self.new('Task two', due='2026-12-01'), self.new('Task three')
        self.assertEqual(self.s.merge(a, [b, c], 'owner')['task']['due'], '2026-11-20')
        d, e = self.new('Task four'), self.new('Task five', due='2026-12-09')
        self.assertEqual(self.s.merge(d, [e], 'owner')['task']['due'], '2026-12-09')

    def test_open_question_moves_when_the_kept_task_has_none(self):
        a, b = self.new('Pick a paint colour'), self.new('Choose the paint colour')
        self.s.ask(b, 'worker', [{'text': 'Which colour?', 'type': 'choice', 'options': ['Sage', 'Slate']}], 'I order the paint.')
        k = self.s.merge(a, [b], 'owner')['task']
        self.assertTrue(k['needs_input'])
        self.assertEqual((k['question']['questions'][0]['text'], k['question']['after']), ('Which colour?', 'I order the paint.'))
        self.assertIsNone(self.s.get(b)['question'])
        self.assertEqual(self.s.answer(a, 'owner', {'q1': 'Sage'})['needs_input'], False)   # answered on the kept task

    def test_open_question_becomes_a_comment_when_the_kept_task_has_one(self):
        a, b, c = self.new('Pick a paint colour'), self.new('Choose the paint colour'), self.new('Paint colour choice')
        self.s.ask(a, 'worker', [{'text': 'Gloss or matt?', 'type': 'text'}], 'I order it.')
        self.s.ask(b, 'worker', [{'text': 'Which colour?', 'type': 'choice', 'options': ['Sage', 'Slate']}], 'I order the paint.')
        self.s.ask(c, 'worker', [{'text': 'How many tins?', 'type': 'text'}], 'I order them.')
        k = self.s.merge(a, [b, c], 'owner')['task']
        self.assertEqual(k['question']['questions'][0]['text'], 'Gloss or matt?')
        self.assertEqual([c_['text'] for c_ in k['comments']], ['From #2: open question: Which colour? (Sage / Slate)', 'From #3: open question: How many tins?'])
        self.assertEqual([q['status'] for q in self.s.get(b)['past_questions']], ['withdrawn'])

    def test_one_transaction_all_or_nothing(self):
        a, b, c = self.new('Task one', due='2026-12-20'), self.new('Task two', due='2026-12-01', legacy=[{'kind': 'queue_id', 'value': 'q-1'}]), self.new('Task three')
        self.s.comment(b, 'owner', 'note on two')
        self.s.close_task(c, 'owner', 'dropped')
        before = (self.s.get(a), self.s.get(b))
        self.refused('conflict', self.s.merge, a, [b, c], 'owner')                  # c is closed: nothing happens to b either
        self.refused('missing', self.s.merge, a, [b, 99], 'owner')
        self.assertEqual((self.s.get(a), self.s.get(b)), before)
        # a failure half way (after task two was folded in) rolls everything back
        real, seen = self.s._close, []

        def failing(con, t, actor, c_):
            real(con, t, actor, c_)
            seen.append(t['id'])
            raise RuntimeError('disk full')
        self.s._close = failing
        with self.assertRaises(RuntimeError):
            self.s.merge(a, [b], 'owner')
        self.s._close = real
        self.assertEqual(seen, [b])
        self.assertEqual((self.s.get(a), self.s.get(b)), before)
        self.assertEqual(self.s.by_legacy('queue_id', 'q-1')['id'], b)
        self.assertEqual(self.s.mirror_rows(), {})

    def test_refusals(self):
        a, b, c = self.new('Task one'), self.new('Task two'), self.new('Task three')
        self.refused('forbidden', self.s.merge, a, [b], 'check')
        self.refused('bad', self.s.merge, a, [], 'owner')
        self.refused('bad', self.s.merge, a, [a], 'owner')
        self.refused('bad', self.s.merge, a, [b, b], 'owner')
        self.refused('bad', self.s.merge, a, 'b', 'owner')
        self.refused('bad', self.s.merge, a, [True], 'owner')
        self.refused('bad', self.s.merge, '1', [b], 'owner')
        self.refused('bad', self.s.merge, a, list(range(2, 2 + M.MERGE_MAX + 1)), 'owner')
        self.refused('missing', self.s.merge, 99, [b], 'owner')
        self.s.close_task(c, 'owner', 'dropped')
        self.refused('conflict', self.s.merge, c, [a], 'owner')                     # a closed task cannot be the kept one
        self.refused('conflict', self.s.merge, a, [c], 'owner')
        d = self.new('Task four')
        self.to_planning(d); self.s.transition(d, 'approved', 'owner'); self.s.transition(d, 'implementing', 'worker'); self.s.transition(d, 'verifying', 'worker')
        self.s.transition(d, 'done', 'check', result='ok')
        self.refused('conflict', self.s.merge, a, [d], 'owner')                     # nor a finished one
        self.assertEqual(self.s.merge(a, [b], 'worker')['merged'], [b])             # a worker may merge, as it may close

    def test_an_approval_is_never_lost(self):
        todo, plan, appr, impl = self.new('Task one'), self.new('Task two'), self.new('Task three'), self.new('Task four')
        self.to_planning(plan)
        for t in (appr, impl):
            self.to_planning(t); self.s.transition(t, 'approved', 'owner')
        self.s.transition(impl, 'implementing', 'worker')
        self.refused('conflict', self.s.merge, todo, [appr], 'owner')
        self.refused('conflict', self.s.merge, plan, [todo, appr], 'owner')
        self.refused('conflict', self.s.merge, appr, [impl], 'owner')               # work in progress is not folded into an earlier stage
        self.assertEqual(self.s.merge(todo, [plan], 'owner')['task']['stage'], 'todo')     # open stages before Approved may be merged either way
        k = self.s.merge(impl, [appr, todo], 'owner')['task']
        self.assertEqual((k['stage'], k['plan']), ('implementing', 'Buy brackets. Fit them.'))

    def test_default_task_to_keep(self):
        t = lambda i, s: {'id': i, 'stage': s}
        self.assertEqual(M.default_keeper([t(1, 'todo'), t(2, 'todo')]), 1)                         # same stage: the lowest number
        self.assertEqual(M.default_keeper([t(1, 'todo'), t(2, 'planning'), t(3, 'planning')]), 2)   # the most advanced stage
        self.assertEqual(M.default_keeper([t(1, 'planning'), t(2, 'todo'), t(3, 'approved')]), 3)
        self.assertEqual(M.default_keeper([t(4, 'verifying'), t(1, 'approved')]), 4)

    def test_undo_is_a_reopen_by_the_owner(self):
        a, b = self.new('Task one'), self.new('Task two', legacy=[{'kind': 'ok_number', 'value': '4'}])
        self.s.merge(a, [b], 'owner')
        self.refused('forbidden', self.s.transition, b, 'todo', 'worker', reason='not the same')
        t = self.s.transition(b, 'todo', 'owner', reason='not the same after all')
        self.assertEqual((t['stage'], t['duplicate_of'], t['closed_reason']), ('todo', None, ''))
        self.assertEqual(self.s.by_legacy('ok_number', '4')['id'], a)               # documented: old ids stay with the kept task


class Duplicates(Case):
    def new(self, title, **kw):
        return self.s.create(title, 'worker', allow_duplicate=True, **kw)['task']['id']

    def test_source_labels_are_ignored(self):
        text = 'ask the plumber about the outside tap'
        for other in ('Handoff from robin: ' + text, 'Handoff from Robin Smith: ' + text, 'OK: ' + text, 'OK #12: ' + text, 'Reopened: ' + text, 'Reopened: Handoff from robin: ' + text):
            self.assertEqual(M.similar('Handoff: ' + text, other), 1.0, other)
        self.assertLess(M.similar('Handoff: ' + text, 'Handoff from robin: book the car service'), M.DUPLICATE_THRESHOLD)
        self.assertLess(M.similar('OK: order printer paper', 'OK: descale the kettle'), M.DUPLICATE_THRESHOLD)

    def test_groups(self):
        a = self.new('Handoff: ask the plumber about the outside tap')
        b = self.new('Order printer paper')
        c = self.new('Handoff from robin: ask the plumber about the outside tap')
        d = self.new('Reopened: order printer paper')
        e = self.new('Book the car service')
        f = self.new('OK: ask the plumber about the outside tap')
        self.to_planning(c)
        g = self.s.duplicates()['groups']
        self.assertEqual([x['ids'] for x in g], [[a, c, f], [b, d]])
        self.assertEqual((g[0]['keep'], g[1]['keep'], g[0]['score']), (c, b, 1.0))    # c is in Planning: the most advanced
        self.assertEqual([t['stage'] for t in g[0]['tasks']], ['todo', 'planning', 'todo'])
        self.assertNotIn('plan', g[0]['tasks'][1])                                  # summaries only
        self.s.close_task(d, 'owner', 'dropped')                                    # only OPEN tasks are compared
        self.assertEqual([x['ids'] for x in self.s.duplicates()['groups']], [[a, c, f]])
        self.assertNotIn(e, [i for x in g for i in x['ids']])
        self.s.merge(c, [a, f], 'owner')
        self.assertEqual(self.s.duplicates()['groups'], [])

    def test_not_duplicates_is_remembered(self):
        a, b, c = self.new('Fix the hall light automation'), self.new('Fix hall light automation'), self.new('Fix the hall light automations')
        self.assertEqual([x['ids'] for x in self.s.duplicates()['groups']], [[a, b, c]])
        self.assertEqual(self.s.not_duplicates([b, a], 'owner'), {'ids': [a, b]})
        self.assertEqual([x['ids'] for x in self.s.duplicates()['groups']], [[a, c]])      # a and b are never offered together again
        self.s.not_duplicates([a, c], 'owner')
        self.assertEqual([x['ids'] for x in self.s.duplicates()['groups']], [[b, c]])
        self.s.not_duplicates([a, b, c], 'worker')                                  # saying it twice is fine
        self.assertEqual(self.s.duplicates()['groups'], [])
        self.assertEqual(self.s.get(a)['history'][-1]['kind'], 'not_duplicate')
        self.s.close(); self.s = type(self.s)(self.dir, clock=lambda: self.t[0])     # kept in the database
        self.assertEqual(self.s.duplicates()['groups'], [])
        self.refused('bad', self.s.not_duplicates, [a], 'owner')
        self.refused('bad', self.s.not_duplicates, [a, a], 'owner')
        self.refused('missing', self.s.not_duplicates, [a, 99], 'owner')
        self.refused('forbidden', self.s.not_duplicates, [a, b], 'check')

    def test_a_group_does_not_drift_along_a_chain(self):
        g = M.duplicate_groups([(1, 'alpha beta gamma delta'), (2, 'alpha beta gamma delta epsilon'), (3, 'gamma delta epsilon zeta eta theta')])
        self.assertEqual([x['ids'] for x in g], [[1, 2]])
        self.assertEqual(M.duplicate_groups([(1, ''), (2, '!!'), (3, 'Only one')]), [])


class MergeApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tokens = {'api_token': WORKER, 'owner_token': OWNER}
        self.srv = Server(self.dir, lambda s: APP.make_api_handler(s, lambda: {}, lambda: self.tokens))
        self.p = self.srv.port
        for title in ('Handoff: water the tomato plants', 'Handoff from robin: water the tomato plants', 'Renew the library card'):
            call(self.p, 'POST', '/tasks', {'title': title, 'allow_duplicate': True}, WORKER)

    def tearDown(self):
        self.srv.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_tokens(self):
        for method, path, body in (('POST', '/tasks/merge', {'keep': 1, 'merge': [2]}), ('GET', '/tasks/duplicates', None), ('POST', '/tasks/duplicates/dismiss', {'ids': [1, 2]})):
            self.assertEqual(call(self.p, method, path, body)[0], 401, path)
            self.assertEqual(call(self.p, method, path, body, 'wrong')[0], 401, path)
        self.assertEqual(call(self.p, 'POST', '/tasks/merge', {'keep': 1, 'merge': [2], 'actor': 'check'}, WORKER)[0], 403)
        self.assertEqual(call(self.p, 'POST', '/tasks/merge', {'keep': 1, 'merge': [2], 'actor': 'owner'}, WORKER)[0], 403)
        self.assertEqual(call(self.p, 'POST', '/tasks/duplicates/dismiss', {'ids': [1, 2], 'actor': 'check'}, WORKER)[0], 403)
        self.assertEqual(call(self.p, 'GET', '/tasks/1', token=WORKER)[1]['stage'], 'todo')

    def test_find_merge_and_dismiss_over_http(self):
        code, d = call(self.p, 'GET', '/tasks/duplicates', token=WORKER)
        self.assertEqual((code, [g['ids'] for g in d['groups']], d['groups'][0]['keep']), (200, [[1, 2]], 1))
        self.assertEqual(call(self.p, 'POST', '/tasks/merge', {'keep': 1}, OWNER)[0], 400)
        self.assertEqual(call(self.p, 'POST', '/tasks/merge', {'keep': 1, 'merge': [2], 'force': True}, OWNER)[0], 400)
        self.assertEqual(call(self.p, 'POST', '/tasks/merge', {'keep': 1, 'merge': [9]}, OWNER)[0], 404)
        self.assertEqual(call(self.p, 'POST', '/tasks/merge', {'keep': 1, 'merge': [1]}, OWNER)[0], 400)
        code, r = call(self.p, 'POST', '/tasks/merge', {'keep': 1, 'merge': [2]}, OWNER)
        self.assertEqual((code, r['merged'], r['task']['id']), (200, [2], 1))
        self.assertEqual(call(self.p, 'GET', '/tasks/2', token=WORKER)[1]['duplicate_of'], 1)
        self.assertEqual(call(self.p, 'POST', '/tasks/merge', {'keep': 1, 'merge': [2]}, OWNER)[0], 409)      # already closed
        self.assertEqual(call(self.p, 'POST', '/tasks/merge', {'keep': 3, 'merge': [1]}, WORKER)[0], 200)     # the worker token may merge
        self.assertEqual(call(self.p, 'POST', '/tasks/duplicates/dismiss', {'ids': [1]}, OWNER)[0], 400)
        self.assertEqual(call(self.p, 'POST', '/tasks/duplicates/dismiss', {'ids': [1, 3]}, OWNER)[1], {'ids': [1, 3]})
        self.assertEqual(call(self.p, 'GET', '/tasks/duplicates', token=OWNER)[1], {'groups': []})

    def test_the_page_can_merge(self):
        srv = Server(tempfile.mkdtemp(dir=self.dir), lambda s: APP.make_ui_handler(s, lambda: {}, peer='127.0.0.1'))
        try:
            for title in ('Handoff: water the tomato plants', 'Handoff from robin: water the tomato plants'):
                call(srv.port, 'POST', '/api/tasks', {'title': title, 'allow_duplicate': True})
            self.assertEqual(call(srv.port, 'GET', '/api/tasks/duplicates')[1]['groups'][0]['ids'], [1, 2])
            self.assertEqual(call(srv.port, 'POST', '/api/tasks/merge', {'keep': 2, 'merge': [1]})[1]['task']['id'], 2)
            html = call(srv.port, 'GET', '/')[1]
            for needle in (b'id="merge-toggle"', b'id="dup-toggle"', b'id="merge-bar"'):
                self.assertIn(needle, html)
        finally:
            srv.stop()


if __name__ == '__main__':
    unittest.main()
