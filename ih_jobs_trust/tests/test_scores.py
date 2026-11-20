"""GET /scores payload and auth: fixed fields only, no answers or notes, bearer check. No network."""
import json, os, sys, tempfile, unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import scores, trustdb

SECRET = 'sk-SECRET-123'


def res(agent, score=10):
    return {'ok': True, 'agent': agent, 'chars': 5, 'took_ms': 9, 'score': score, 'note': 'NOTE ' + SECRET, 'answer': 'ANSWER ' + SECRET}


RESULT = {'month': '2026-11', 'dry': True, 'finished': '2026-11-01T03:05:00+00:00',
          'results': {'a': res('ih_ai_layer_groq', 5), 'b': res('ih_ai_layer_groq', 10), 'c': res('ih_ai_layer_gemini', 10), 'd': res('ih_ai_layer_gemini', 10)}}


class T(unittest.TestCase):
    def test_build(self):
        out = scores.build(RESULT)
        self.assertTrue(out['ok'])
        self.assertEqual((out['month'], out['date'], out['dry']), ('2026-11', '2026-11-01', True))
        self.assertEqual([r['agent'] for r in out['rows']][0], 'gemini')
        self.assertEqual({r['agent'] for r in out['rows']}, {'gemini', 'groq'})
        self.assertNotIn(SECRET, json.dumps(out))
        for r in out['rows']:
            self.assertEqual(set(r), {'agent', 'name', 'test', 'scored', 'unscored', 'invented'})

    def test_none(self):
        self.assertEqual(scores.build(None)['rows'], [])

    def test_auth(self):
        self.assertTrue(scores.authorised('k', 'Bearer k'))
        self.assertFalse(scores.authorised('k', 'Bearer x'))
        self.assertFalse(scores.authorised('', 'Bearer '))
        self.assertFalse(scores.authorised('k', None))
        self.assertFalse(scores.authorised('k', 'k'))

    def test_payload_empty_dir_and_bad_dir(self):
        d = tempfile.mkdtemp()
        self.assertEqual(scores.payload(d)['rows'], [])

    def test_payload_from_plain_file(self):
        d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, 'trust_runs'))
        json.dump(RESULT, open(os.path.join(d, 'trust_runs', '2026-11.json'), 'w'))
        out = scores.payload(d)
        self.assertEqual(len(out['rows']), 2)


if __name__ == '__main__':
    unittest.main()
