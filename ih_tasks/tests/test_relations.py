"""Task links: blocked by / depends on / relates to. Rules, inverse view, loops, merge, HTTP. Made-up tasks, temp folders only."""
import os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as APP
from test_api import OWNER, WORKER, Server, call
from test_store import Case


class Relations(Case):
    def mk(self, title):
        return self.s.create(title, 'worker', allow_duplicate=True)['task']['id']

    def test_blocked_by_shows_both_ends_and_clears_when_done(self):
        a, b = self.mk('Paint the shed'), self.mk('Buy the paint')
        t = self.s.relate(a, 'worker', 'blocked_by', b)
        self.assertEqual([(r['kind'], r['task'], r['waiting']) for r in t['relations']], [('blocked_by', b, True)])
        self.assertTrue(t['blocked'])
        other = self.s.get(b)
        self.assertEqual([(r['kind'], r['task'], r['waiting']) for r in other['relations']], [('blocks', a, False)])
        self.assertFalse(other['blocked'])
        self.assertIn('related', [h['kind'] for h in other['history']])
        self.s.close_task(b, 'owner', 'wont_do', 'not needed')
        self.assertFalse(self.s.get(a)['blocked'])          # the other task is no longer open

    def test_depends_on_inverse_and_relates_to_is_symmetric(self):
        a, b, c = self.mk('One'), self.mk('Two'), self.mk('Three')
        self.s.relate(a, 'owner', 'depends_on', b)
        self.assertEqual(self.s.get(b)['relations'][0]['kind'], 'needed_by')
        self.s.relate(c, 'check', 'relates_to', a)
        self.assertEqual([r['kind'] for r in self.s.get(a)['relations'] if r['task'] == c], ['relates_to'])
        self.assertEqual([r['kind'] for r in self.s.get(c)['relations']], ['relates_to'])
        self.assertFalse(self.s.get(c)['blocked'])
        self.refused('conflict', self.s.relate, a, 'worker', 'relates_to', c)       # same link the other way round
        self.refused('conflict', self.s.relate, c, 'worker', 'relates_to', a)

    def test_refusals(self):
        a, b, c = self.mk('One'), self.mk('Two'), self.mk('Three')
        self.refused('bad', self.s.relate, a, 'worker', 'blocked_by', a)
        self.refused('bad', self.s.relate, a, 'worker', 'friends_with', b)
        self.refused('bad', self.s.relate, a, 'worker', 'blocked_by', '2')
        self.refused('bad', self.s.relate, a, 'worker', 'blocked_by', True)
        self.refused('missing', self.s.relate, a, 'worker', 'blocked_by', 99)
        self.refused('missing', self.s.relate, 99, 'worker', 'blocked_by', a)
        self.s.relate(a, 'worker', 'blocked_by', b)
        self.refused('conflict', self.s.relate, a, 'worker', 'blocked_by', b)
        self.refused('conflict', self.s.relate, b, 'worker', 'depends_on', a)       # loop of two
        self.s.relate(b, 'worker', 'depends_on', c)
        self.refused('conflict', self.s.relate, c, 'worker', 'blocked_by', a)       # loop through another task
        self.s.relate(a, 'worker', 'relates_to', c)                          # relates_to never makes a loop
        self.s.relate(c, 'worker', 'relates_to', b)

    def test_unrelate(self):
        a, b = self.mk('One'), self.mk('Two')
        self.refused('missing', self.s.unrelate, a, 'worker', 'relates_to', b)
        self.s.relate(a, 'worker', 'relates_to', b)
        t = self.s.unrelate(b, 'worker', 'relates_to', a)                    # either end may remove it
        self.assertEqual((t['relations'], self.s.get(a)['relations']), ([], []))
        self.assertIn('unrelated', [h['kind'] for h in self.s.get(a)['history']])
        self.s.relate(a, 'worker', 'blocked_by', b)
        self.refused('missing', self.s.unrelate, b, 'worker', 'blocked_by', a)     # direction matters
        self.s.unrelate(a, 'worker', 'blocked_by', b)
        self.s.relate(b, 'worker', 'blocked_by', a)                          # no loop left after removing it

    def test_merge_moves_links_to_the_kept_task(self):
        keep, dup, x, y = self.mk('Handoff: fix gate'), self.mk('Handoff from robin: fix gate'), self.mk('Buy hinges'), self.mk('Gate notes')
        self.s.relate(dup, 'worker', 'blocked_by', x)
        self.s.relate(y, 'worker', 'relates_to', dup)
        self.s.relate(keep, 'worker', 'relates_to', dup)                     # between the merged tasks: disappears
        self.s.merge(keep, [dup], 'owner')
        got = sorted((r['kind'], r['task']) for r in self.s.get(keep)['relations'])
        self.assertEqual(got, [('blocked_by', x), ('relates_to', y)])
        self.assertEqual(self.s.get(dup)['relations'], [])
        self.assertEqual([r['kind'] for r in self.s.get(x)['relations']], ['blocks'])


class RelationsApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tokens = {'api_token': WORKER, 'owner_token': OWNER}
        self.srv = Server(self.dir, lambda s: APP.make_api_handler(s, lambda: {}, lambda: self.tokens))
        self.p = self.srv.port
        for title in ('Wire the porch light', 'Buy cable'):
            call(self.p, 'POST', '/tasks', {'title': title}, WORKER)

    def tearDown(self):
        self.srv.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_over_http(self):
        body = {'kind': 'blocked_by', 'task': 2}
        self.assertEqual(call(self.p, 'POST', '/tasks/1/relate', body)[0], 401)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/relate', dict(body, actor='owner'), WORKER)[0], 403)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/relate', {'kind': 'blocked_by'}, WORKER)[0], 400)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/relate', dict(body, extra=1), WORKER)[0], 400)
        code, t = call(self.p, 'POST', '/tasks/1/relate', body, WORKER)
        self.assertEqual((code, t['blocked'], t['relations'][0]['task']), (200, True, 2))
        self.assertEqual(call(self.p, 'POST', '/tasks/2/relate', {'kind': 'blocked_by', 'task': 1}, OWNER)[0], 409)
        self.assertEqual(call(self.p, 'GET', '/tasks/2', token=WORKER)[1]['relations'][0]['kind'], 'blocks')
        self.assertEqual(call(self.p, 'POST', '/tasks/1/unrelate', body, OWNER)[1]['relations'], [])


if __name__ == '__main__':
    unittest.main()
