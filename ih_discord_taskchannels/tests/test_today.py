"""Task Channels 'Today' card: counts only, test mode, missing/corrupt state, no names. No network."""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import today

NAME = 'SECRET-TASK-NAME'
STATE = {'tasks': {'a': {'name': NAME, 'state': 'wip', 'id': '123', 'summary': 'PRIVATE'}, 'b': {'name': 'x', 'state': 'wip'}, 'c': {'name': 'y', 'state': 'done'}, 'd': 'bad'}}


class T(unittest.TestCase):
    def test_counts_live(self):
        out = today.build(STATE, {'shadow': False, 'cleanup_days': 30})
        r = {x['name']: x for x in out['rows']}
        self.assertEqual((r['Open tasks']['value'], r['Done tasks']['value']), ('2', '1'))
        self.assertEqual(r['Open tasks']['state'], 'live')
        self.assertEqual(r['Done tasks']['note'], 'Tidied up 30 days after they are done')
        self.assertNotIn('footnote', out)

    def test_shadow(self):
        out = today.build({'tasks': {}}, {})
        self.assertEqual([x['value'] for x in out['rows']], ['0', '0'])
        self.assertEqual(out['rows'][0]['state'], 'test')
        self.assertIn('Test mode', out['footnote'])

    def test_bad_input(self):
        self.assertIsNone(today.build(None, {}))
        self.assertEqual(today.build({'tasks': 'x'}, {'cleanup_days': 'zz'})['rows'][1]['note'], 'Tidied up 30 days after they are done')

    def test_no_names(self):
        out = json.dumps(today.build(STATE, {'shadow': False}))
        for bad in (NAME, 'PRIVATE', '123'):
            self.assertNotIn(bad, out)

    def test_files(self):
        with tempfile.TemporaryDirectory() as d:
            clock = lambda: 100.0
            today._cache.update(t=0.0, v=None)
            self.assertEqual(today.today(d, clock)['rows'][0]['value'], '0')          # no state file yet: zero tasks
            open(os.path.join(d, 'state.json'), 'w').write(json.dumps(STATE))
            today._cache.update(t=0.0, v=None)
            self.assertEqual(today.today(d, clock)['rows'][0]['value'], '2')
            open(os.path.join(d, 'state.json'), 'w').write('{broken')
            today._cache.update(t=0.0, v=None)
            self.assertIsNone(today.today(d, clock))


if __name__ == '__main__':
    unittest.main()
