"""The ingress 'Today' card payload: sorting, missing scores, test/off states, and no secrets or free text. No network."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import today

SECRET = 'sk-SECRET-TOKEN-123'


def prov(kind, model=None, used=0, cap=50, configured=True, shadow=False, kill=False, name=None):
    return {'name': name or 'ih_ai_layer_' + kind, 'provider': kind, 'version': '1', 'capabilities': ['general_qa'], 'configured': configured, 'model': model,
            'shadow': shadow, 'kill_switch': kill, 'used_today': used, 'daily_cap': cap, 'spend_cap': 0, 'free': True, 'paid_today': 0, 'served': 1, 'live_calls': 1,
            'refused_by_cap': 0, 'key': SECRET, 'url': 'https://example.invalid/' + SECRET}


def status(providers, scores=None, mode='live', kill=False):
    return {'name': 'ih_ai_layer', 'mode': mode, 'kill_switch': kill, 'providers': providers, 'scores': {'general_qa': scores or {}}, 'api_token': SECRET}


class BuildTests(unittest.TestCase):
    def test_sorted_by_score_missing_last(self):
        s = status([prov('gemini', 'g', 22), prov('serper', None, 21, 60), prov('groq', 'oss', 190, 900), prov('mistral', 'm', 15)],
                   {'ih_ai_layer_groq': 0.95, 'ih_ai_layer_mistral': 0.76, 'ih_ai_layer_gemini': 0.57})
        out = today.build(s)
        self.assertEqual([r['name'] for r in out['rows']], ['Groq', 'Mistral', 'Gemini', 'Serper (web search)'])
        self.assertEqual([r.get('score') for r in out['rows']], [95, 76, 57, None])
        self.assertEqual(out['title'], 'Today')

    def test_usage_units_and_labels(self):
        out = today.build(status([prov('serper', None, 21, 60), prov('serpapi', None, 1, 5), prov('groq', 'x', 190, 900), prov('publicapis', None, 0, 10)]))
        by = {r['name']: r for r in out['rows']}
        self.assertEqual((by['Serper (web search)']['used'], by['Serper (web search)']['cap'], by['Serper (web search)']['unit']), (21, 60, 'searches'))
        self.assertEqual(by['SerpApi (showtimes and images)']['unit'], 'searches')
        self.assertEqual(by['Groq']['unit'], 'calls')
        self.assertIn('PublicAPIs (API directory)', by)
        self.assertNotIn('model', by['Serper (web search)'])

    def test_states(self):
        s = status([prov('groq', shadow=True), prov('gemini', configured=False), prov('mistral', kill=True), prov('openai')])
        by = {r['name']: r['state'] for r in today.build(s)['rows']}
        self.assertEqual(by, {'Groq': 'test', 'Gemini': 'off', 'Mistral': 'off', 'OpenAI': 'live'})
        self.assertEqual({r['state'] for r in today.build(status([prov('groq')], mode='dry'))['rows']}, {'test'})
        self.assertEqual({r['state'] for r in today.build(status([prov('groq')], kill=True))['rows']}, {'off'})

    def test_no_secrets_or_extra_fields(self):
        out = json.dumps(today.build(status([prov('groq', 'oss')], {'ih_ai_layer_groq': 0.9})))
        self.assertNotIn(SECRET, out)
        self.assertNotIn('http', out)
        for k in ('key', 'url', 'token', 'api_token'):
            self.assertNotIn('"%s"' % k, out)

    def test_bad_input(self):
        self.assertIsNone(today.build(None))
        self.assertEqual(today.build({})['rows'], [])
        self.assertEqual(today.build(status([prov('groq', used='x', cap=None)]))['rows'][0].get('used'), None)

    def test_mcp_label(self):
        out = today.build(status([prov('mcp', None, name='ih_ai_layer_mcp_weather')]))
        self.assertEqual(out['rows'][0]['name'], 'MCP weather')


class FetchTests(unittest.TestCase):
    def setUp(self):
        today._cache.update(t=0.0, v=None)

    def test_cache_five_seconds(self):
        calls, now = [], [100.0]

        def fetch():
            calls.append(1)
            return status([prov('groq')])
        today.today(fetch, lambda: now[0]); today.today(fetch, lambda: now[0] + 4)
        self.assertEqual(len(calls), 1)
        today.today(fetch, lambda: now[0] + 6)
        self.assertEqual(len(calls), 2)

    def test_failure_gives_none(self):
        def boom():
            raise OSError('x')
        self.assertIsNone(today.today(boom, lambda: 1.0))

    def test_token_read_from_options_and_sent_only_as_header(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'options.json')
        with open(p, 'w') as f:
            json.dump({'api_token': SECRET}, f)
        seen = {}

        class R:
            def read(self, n): return json.dumps(status([])).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def opener(req, timeout):
            seen['url'], seen['auth'] = req.full_url, req.get_header('Authorization'); return R()
        self.assertEqual(today.fetch_status(8801, p, opener)['name'], 'ih_ai_layer')
        self.assertEqual(seen['url'], 'http://127.0.0.1:8801/status')
        self.assertEqual(seen['auth'], 'Bearer ' + SECRET)
        self.assertIsNone(today.fetch_status(8801, os.path.join(d, 'missing.json'), opener))


if __name__ == '__main__':
    unittest.main()
