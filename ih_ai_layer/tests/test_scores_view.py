"""AI scores page data: IQ maths, live join, fallback message, no secrets or text leak. No network."""
import json, os, sys, tempfile, unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import scores_view

TRUST = {'ok': True, 'month': '2026-11', 'date': '2026-11-01', 'dry': False,
         'rows': [{'agent': 'groq', 'name': 'Groq', 'test': 55, 'scored': 8, 'unscored': 0, 'invented': True},
                  {'agent': 'gemini', 'name': 'Gemini', 'test': 99, 'scored': 8, 'unscored': 0, 'invented': False},
                  {'agent': 'mistral', 'name': 'Mistral', 'test': None, 'scored': 0, 'unscored': 8, 'invented': False}]}
STATUS = {'scores': {'general_qa': {'ih_ai_layer_gemini': 0.79, 'ih_ai_layer_groq': 1.0}}, 'api_token': 'SECRET-TOKEN'}


class T(unittest.TestCase):
    def test_iq_and_sort(self):
        out = scores_view.build(TRUST, STATUS)
        names = [r['name'] for r in out['rows']]
        self.assertEqual(names, ['Gemini', 'Groq', 'Mistral'])
        g = out['rows'][0]
        self.assertEqual((g['test'], g['live'], g['iq']), (99, 79, round(0.8 * 99 + 0.2 * 79)))
        self.assertTrue(out['rows'][1]['invented'])
        self.assertIsNone(out['rows'][2]['iq'])
        self.assertEqual(out['rows'][0]['date'], '2026-11-01')
        self.assertNotIn('SECRET', json.dumps(out))

    def test_no_live_uses_test_alone(self):
        out = scores_view.build(TRUST, None)
        self.assertEqual(out['rows'][0]['iq'], 99)
        self.assertIsNone(out['rows'][0]['live'])

    def test_unreachable_trust(self):
        for bad in (None, {'ok': False}, 'x'):
            out = scores_view.build(bad, STATUS)
            self.assertEqual(out['rows'], [])
            self.assertIn('could not be reached', out['message'])

    def test_empty_and_dry_messages(self):
        self.assertIn('No test results', scores_view.build({'ok': True, 'rows': []}, None)['message'])
        self.assertIn('dry run', scores_view.build(dict(TRUST, dry=True), None)['message'])

    def test_fetch_needs_key_and_sends_bearer(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'o.json')
        json.dump({'register_key': ''}, open(p, 'w'))
        with self.assertRaises(RuntimeError):
            scores_view.fetch_trust(p)
        json.dump({'register_key': 'KEY1'}, open(p, 'w'))
        seen = {}

        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, n): return json.dumps(TRUST).encode()

        def opener(req, timeout=None):
            seen['auth'] = req.get_header('Authorization'); return R()
        self.assertEqual(scores_view.fetch_trust(p, opener=opener)['date'], '2026-11-01')
        self.assertEqual(seen['auth'], 'Bearer KEY1')

    def test_cached_and_never_raises(self):
        scores_view._cache.update(t=0.0, v=None)
        calls = []
        def ft(): calls.append(1); raise OSError('down')
        out = scores_view.scores(ft, lambda: None, clock=lambda: 100.0)
        self.assertEqual(out['rows'], [])
        scores_view.scores(ft, lambda: None, clock=lambda: 101.0)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
