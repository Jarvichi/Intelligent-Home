"""Routing metadata on the layer's trace spans: typed attrs only, never request or answer text. No network."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import ailayer

TID, PID = 'a' * 32, 'b' * 16


class FakeAgent:
    CAPABILITIES = ['general_qa']
    VERSION = '1'

    def __init__(self, ok, model, answer='some answer text'):
        self.ok, self.model, self.answer = ok, model, answer

    def ask(self, obj, timeout=None):
        if not self.ok:
            return {'ok': False, 'request_id': obj['request_id'], 'why': 'upstream'}
        return {'ok': True, 'request_id': obj['request_id'], 'answer': self.answer, 'model': self.model}


def make(agents):
    d = tempfile.mkdtemp()
    layer = ailayer.Layer({'shadow': False, 'rate_per_min': 100, 'max_tries': 2, 'agent_timeout_s': 5, 'providers': [], 'provider_keys': {}}, data=d)
    for n, a in agents.items():
        layer.agents[n] = {'models': [a.model] if a.model else [], 'url': None, 'capabilities': ['general_qa'], 'version': '1', 'healthy': True,
                           'seen': layer.clock(), 'local': a}
    return layer


def spans(r):
    req = [s for s in r['trace_spans'] if s['operation'] == 'request'][0]
    asks = [s for s in r['trace_spans'] if s['operation'] == 'ask']
    return req, asks


class RoutingTrace(unittest.TestCase):
    def test_classified_request_two_agents(self):
        layer = make({'ih_ai_layer_groq': FakeAgent(True, 'openai/gpt-oss-120b'), 'ih_ai_layer_gemini': FakeAgent(True, 'gemini-2.5-flash')})
        layer.scores = {'general_qa': {'ih_ai_layer_groq': {'n': 10, 'ok': 10, 'ms': 5000, 'up': 5, 'down': 0}}}
        expected = int(round(layer.score('general_qa', 'ih_ai_layer_groq') * 1000))   # at decision time, before the outcome is recorded
        code, r = layer.request({'text': 'tell me a story about a cat', 'request_id': 'r1', 'caller': 'ih_discord_ai_bot', 'trace_id': TID, 'parent_span_id': PID})
        self.assertEqual(code, 200)
        self.assertEqual(r['agent'], 'ih_ai_layer_groq')
        self.assertEqual(r['model'], 'openai/gpt-oss-120b')   # scored route reports the model too
        req, asks = spans(r)
        a = req['attrs']
        self.assertEqual((a['capability'], a['classified'], a['agent'], a['model']), ('general_qa', 1, 'groq', 'openai/gpt-oss-120b'))
        self.assertEqual((a['candidates'], a['tries']), (2, 1))
        self.assertEqual(a['score'], expected)
        self.assertGreater(a['score'], 700)
        self.assertLessEqual(len(a), 8)
        self.assertEqual(len(asks), 1)
        self.assertEqual(asks[0]['attrs']['agent'], 'groq')
        self.assertEqual(asks[0]['attrs']['model'], 'openai/gpt-oss-120b')
        self.assertEqual(asks[0]['attrs']['score'], a['score'])
        self.assertEqual(asks[0]['attrs']['chars'], len('some answer text'))

    def test_fallback_counts_tries(self):
        layer = make({'ih_ai_layer_groq': FakeAgent(False, None), 'ih_ai_layer_gemini': FakeAgent(True, 'gemini-2.5-flash')})
        layer.scores = {'general_qa': {'ih_ai_layer_groq': {'n': 10, 'ok': 10, 'ms': 5000, 'up': 5, 'down': 0}}}
        code, r = layer.request({'text': 'hello there', 'request_id': 'r2', 'trace_id': TID, 'parent_span_id': PID})
        req, asks = spans(r)
        self.assertEqual((req['attrs']['tries'], req['attrs']['agent'], req['attrs']['candidates']), (2, 'gemini', 2))
        self.assertEqual(len(asks), 2)
        self.assertNotIn('chars', asks[0]['attrs'])

    def test_forced_model_has_no_score_and_not_classified(self):
        layer = make({'ih_ai_layer_groq': FakeAgent(True, 'openai/gpt-oss-120b')})
        code, r = layer.request({'text': 'hi', 'request_id': 'r3', 'model': 'openai/gpt-oss-120b', 'capability': 'general_qa', 'trace_id': TID, 'parent_span_id': PID})
        req, asks = spans(r)
        self.assertNotIn('score', req['attrs'])
        self.assertNotIn('score', asks[0]['attrs'])
        self.assertEqual(req['attrs']['classified'], 0)
        self.assertEqual(req['attrs']['model'], 'openai/gpt-oss-120b')
        self.assertEqual(req['attrs']['tries'], 1)

    def test_no_text_in_any_attr(self):
        layer = make({'ih_ai_layer_groq': FakeAgent(True, 'm-1', answer='SECRETANSWER words')})
        code, r = layer.request({'text': 'SECRETQUESTION words', 'request_id': 'r4', 'trace_id': TID, 'parent_span_id': PID})
        blob = repr(r['trace_spans'])
        self.assertNotIn('SECRET', blob)
        for s in r['trace_spans']:
            for k, v in s['attrs'].items():
                self.assertTrue(isinstance(v, int) or ' ' not in v)

    def test_caller_reaches_usage(self):
        layer = make({'ih_ai_layer_groq': FakeAgent(True, 'm-1')})
        layer.request({'text': 'hello', 'request_id': 'r5', 'caller': 'ih_discord_ai_bot'})
        self.assertEqual(layer.usage_lines()[-1]['by'], 'ih_discord_ai_bot')


if __name__ == '__main__':
    unittest.main()
