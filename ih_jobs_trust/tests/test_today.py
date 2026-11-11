import string
"""AI Trust Test 'Last test' card: scores per AI, dates, no results, old results, no secrets or answer text. No network."""
import json, os, sys, tempfile, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import today, trustdb

SECRET = 'sk-SECRET-TOKEN-123'
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)


def res(agent, ok=True, score=10):
    return {'ok': ok, 'agent': agent, 'chars': 5, 'took_ms': 9, 'score': score, 'note': 'ANSWER-TEXT ' + SECRET, 'answer': 'PRIVATE ' + SECRET}


def result(finished='2026-11-01T03:05:00+00:00', **kw):
    r = {'a': res('ih_ai_layer_groq', score=5), 'b': res('ih_ai_layer_groq', score=10), 'c': res('ih_ai_layer_gemini', score=10),
         'd': res('ih_ai_layer_gemini', score=10), 'e': res('ih_ai_layer_openai', score=10), 'f': res('', ok=False, score=0), 'g': res('ih_ai_layer_openai', score=0),
         'h': res('ih_ai_layer_groq', score=10)}
    r.update(kw)
    return {'month': '2026-11', 'dry': True, 'finished': finished, 'results': r, 'summary': {'test': 80}}


class T(unittest.TestCase):
    def test_scores_and_dates(self):
        out = today.build(result(), {'mode': 'dry'}, NOW)
        by = {r['name']: r for r in out['rows']}
        self.assertEqual(set(by), {'Groq', 'Gemini', 'OpenAI'})
        self.assertEqual(by['Gemini']['score'], 100)
        self.assertEqual(by['Groq']['score'], round(100 * (1 * 5 + 1 * 10 + 2 * 10) / (10 * 4)))   # a=1, b=1, h=2 -> 87.5 -> 88
        self.assertEqual(by['OpenAI']['score'], round(100 * (1 * 10 + 2 * 0) / (10 * 3)))
        self.assertEqual([r['name'] for r in out['rows']][0], 'Gemini')
        self.assertIn('1 November', out['subtitle'])
        self.assertIn('Next test: 1 December.', out['footnote'])
        self.assertIn('test mode', out['footnote'])

    def test_live_has_no_test_note(self):
        out = today.build(result(), {'mode': 'live', 'old_loops_disabled': True}, NOW)
        self.assertNotIn('test mode', out['footnote'])

    def test_no_results(self):
        out = today.build(None, {}, NOW)
        self.assertEqual(out['rows'], [{'name': 'Last test', 'plain': True, 'note': 'No results yet'}])
        self.assertIn('Next test: 1 December.', out['footnote'])

    def test_old_results(self):
        out = today.build(result('2026-09-01T03:05:00+00:00'), {}, NOW)
        self.assertIn('over 40 days old', out['footnote'])

    def test_invented_flag(self):
        out = today.build(result(d=res('ih_ai_layer_gemini', score=0)), {}, NOW)
        g = [r for r in out['rows'] if r['name'] == 'Gemini'][0]
        self.assertIn('Made something up', g['note'])

    def test_unknown_agent_label_is_clean(self):
        self.assertEqual(today.label('ih_ai_layer_mcp_x'), 'MCP')
        self.assertTrue(set(today.label('https://evil/' + SECRET)) <= set(string.ascii_letters + string.digits + ' _-'))
        self.assertLessEqual(len(today.label('x' * 100)), 30)
        self.assertEqual(today.label(''), 'AI')

    def test_no_secrets_or_answer_text(self):
        out = json.dumps(today.build(result(), {'mode': 'dry', 'ai_layer_token': SECRET}, NOW))
        self.assertNotIn(SECRET, out)
        self.assertNotIn('ANSWER-TEXT', out)
        self.assertNotIn('PRIVATE', out)

    def test_files(self):
        with tempfile.TemporaryDirectory() as d:
            today._cache.update(t=0.0, v=None)
            self.assertEqual(today.today(d, {}, lambda: NOW.timestamp())['rows'][0]['note'], 'No results yet')
            os.makedirs(os.path.join(d, 'trust_runs'))
            open(os.path.join(d, 'trust_runs', '2026-10.json'), 'w').write(json.dumps(result('2026-10-01T03:05:00+00:00')))
            open(os.path.join(d, 'trust_runs', '2026-11.json'), 'w').write('{not json')
            today._cache.update(t=0.0, v=None)
            out = today.today(d, {}, lambda: NOW.timestamp())
            self.assertIn('1 October', out['subtitle'])          # broken newest file skipped
            today._cache.update(t=0.0, v=None)
            self.assertIsNone(today.today(os.path.join(d, 'missing'), {}, lambda: NOW.timestamp()))


if __name__ == '__main__':
    unittest.main()
