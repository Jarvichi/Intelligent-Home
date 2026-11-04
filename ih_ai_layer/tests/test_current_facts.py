"""current_facts: classifier, search-first grounded flow, link post-check, Sources, fallback, Serper provider, trace attrs. No network."""
import json
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import ailayer
from providers.serper import Serper, parse_results

TID, PID = 'c' * 32, 'd' * 16
ROWS = [{'title': 'Game X price', 'link': 'https://store.example.com/app/42/Game_X', 'snippet': 'Game X costs 29.99 GBP.', 'date': '1 day ago'},
        {'title': 'Game X release', 'link': 'https://news.example.org/gamex-release', 'snippet': 'Game X releases on 5 March 2027.', 'date': ''}]


class FakeSearch:
    CAPABILITIES = ['web_search']
    VERSION = '1'

    def __init__(self, rows=ROWS, ok=True):
        self.rows, self.ok, self.queries = rows, ok, []

    def ask(self, obj, timeout=None):
        self.queries.append((obj['text'], obj.get('context')))
        if not self.ok:
            return {'ok': False, 'request_id': obj['request_id'], 'why': 'upstream_error'}
        return {'ok': True, 'request_id': obj['request_id'], 'answer': json.dumps(self.rows), 'model': None}


class FakeLLM:
    CAPABILITIES = ['general_qa']
    VERSION = '1'

    def __init__(self, answer, ok=True, model='m-1'):
        self.answer, self.ok, self.model, self.prompts = answer, ok, model, []

    def ask(self, obj, timeout=None):
        self.prompts.append(obj['text'])
        if not self.ok:
            return {'ok': False, 'request_id': obj['request_id'], 'why': 'upstream'}
        return {'ok': True, 'request_id': obj['request_id'], 'answer': self.answer, 'model': self.model}


def make(search=None, llm=None, shadow=False):
    layer = ailayer.Layer({'shadow': shadow, 'rate_per_min': 100, 'max_tries': 2, 'agent_timeout_s': 5, 'providers': [], 'provider_keys': {}}, data=tempfile.mkdtemp())
    for name, a in (('ih_ai_layer_serper', search), ('ih_ai_layer_groq', llm)):
        if a is not None:
            layer.agents[name] = {'models': [], 'url': None, 'capabilities': list(a.CAPABILITIES), 'version': '1', 'healthy': True, 'seen': layer.clock(), 'local': a}
    return layer


Q = 'when is the release date of Game X and how much is it'


class Classifier(unittest.TestCase):
    def test_current_facts(self):
        for t in (Q, 'what is the price of Game X', 'who won the score last night', 'weather tomorrow', 'is it still on sale in 2026'):
            self.assertEqual(ailayer.classify(t), 'current_facts', t)

    def test_others_unchanged(self):
        self.assertEqual(ailayer.classify('showtimes at odeon today'), 'showtimes')
        self.assertEqual(ailayer.classify('is there a free api for weather'), 'api_search')
        self.assertEqual(ailayer.classify('search for a pasta recipe'), 'web_search')
        self.assertEqual(ailayer.classify('who is Ada Lovelace'), 'web_search')       # no time-sensitive word
        self.assertIsNone(ailayer.classify('tell me a story about a cat'))

    def test_whole_words_only(self):
        self.assertIsNone(ailayer.classify('I know a snowman'))     # 'now' inside know / snowman
        self.assertIsNone(ailayer.classify('a costume party'))      # 'cost' inside costume


class Helpers(unittest.TestCase):
    def test_query(self):
        q = ailayer.search_query('Hey <@1234> can you tell me what is the release date of Game X? https://x.example/y')
        self.assertEqual(q, 'the release date of Game X')
        self.assertLessEqual(len(ailayer.search_query('word ' * 100)), 120)

    def test_prompt_fits(self):
        rows = [{'title': 't' * 100, 'link': 'https://a.example/' + str(i), 'snippet': 's' * 300, 'date': 'd'} for i in range(8)]
        p = ailayer.build_grounded('q' * 3000, rows, '2026-11-05')
        self.assertLessEqual(len(p), 3900)
        self.assertIn('ONLY the search results', p)
        self.assertIn('Question:', p)

    def test_check_links(self):
        links = [r['link'] for r in ROWS]
        out, n, cited = ailayer.check_links('See https://store.example.com/app/42/Game_X?x=1, and (https://evil.example.net/fake). Root: https://www.news.example.org/', links)
        self.assertEqual(n, 1)
        self.assertIn('https://store.example.com/app/42/Game_X?x=1,', out)
        self.assertNotIn('evil', out)
        self.assertIn('https://www.news.example.org/', out)       # shorter path of a result host is fine
        self.assertEqual(len(cited), 2)
        out, n, _ = ailayer.check_links('Deeper https://store.example.com/app/99/other', links)
        self.assertEqual(n, 1)


class Flow(unittest.TestCase):
    def go(self, layer, text=Q, **kw):
        return layer.request(dict({'text': text, 'request_id': 'r1', 'caller': 'tester', 'trace_id': TID, 'parent_span_id': PID}, **kw))

    def test_grounded_answer_sources_and_trace(self):
        s, l = FakeSearch(), FakeLLM('It costs 29.99 GBP (https://store.example.com/app/42/Game_X) and is out 5 March 2027 (https://invented.example.com/x).')
        layer = make(s, l)
        code, r = self.go(layer)
        self.assertEqual((code, r['ok'], r['capability'], r['classified']), (200, True, 'current_facts', True))
        a = r['answer']
        self.assertIn('https://store.example.com/app/42/Game_X', a)
        self.assertNotIn('invented', a)
        self.assertEqual(a.count('(link removed: not in search results)'), 1)
        self.assertTrue(a.endswith('Sources:\n- https://store.example.com/app/42/Game_X'))
        # search query is short and asked as json; the LLM prompt holds the results and the question
        self.assertEqual(s.queries[0][1], {'format': 'json'})
        self.assertNotIn('can you', s.queries[0][0])
        p = l.prompts[0]
        self.assertIn('ONLY the search results', p)
        self.assertIn('Game X releases on 5 March 2027', p)
        self.assertIn('Question: ' + Q, p)
        self.assertLessEqual(len(p), 4000)
        req = [x for x in r['trace_spans'] if x['operation'] == 'request'][0]['attrs']
        self.assertEqual((req['capability'], req['agent'], req['model'], req['results'], req['tries'], req['candidates']), ('current_facts', 'groq', 'm-1', 2, 1, 1))
        self.assertLessEqual(len(req), 8)
        sp = [x for x in r['trace_spans'] if x['operation'] == 'search'][0]
        self.assertEqual((sp['component'], sp['attrs']), ('ih_ai_layer_serper', {'agent': 'serper', 'results': 2}))
        self.assertNotIn('Game X', repr(r['trace_spans']))
        self.assertEqual(layer.recent['r1'], ('general_qa', 'ih_ai_layer_groq'))      # /feedback scores the answering AI
        what = [u['what'] for u in layer.usage_lines()]
        self.assertEqual(sorted(what), ['current_facts', 'web_search'])

    def test_sources_top_three_when_none_cited(self):
        rows = [dict(ROWS[0], link='https://a.example/%d' % i) for i in range(5)]
        layer = make(FakeSearch(rows), FakeLLM('I could not find it.'))
        _, r = self.go(layer)
        self.assertEqual(r['answer'].split('Sources:\n')[1].count('- https://a.example/'), 3)

    def test_search_failure_falls_back(self):
        layer = make(FakeSearch(ok=False), FakeLLM('Probably 30 pounds.'))
        _, r = self.go(layer)
        self.assertTrue(r['ok'])
        self.assertTrue(r['answer'].startswith('(No web search available — this may be out of date.)\nProbably'))
        self.assertNotIn('Sources:', r['answer'])
        req = [x for x in r['trace_spans'] if x['operation'] == 'request'][0]['attrs']
        self.assertEqual(req['results'], 0)

    def test_no_search_agent_falls_back(self):
        layer = make(None, FakeLLM('Probably 30 pounds.'))
        _, r = self.go(layer)
        self.assertTrue(r['answer'].startswith('(No web search available'))

    def test_llm_fallback_and_failure(self):
        layer = make(FakeSearch(), FakeLLM('x', ok=False))
        _, r = self.go(layer)
        self.assertFalse(r['ok'])

    def test_shadow(self):
        s = FakeSearch()
        _, r = self.go(make(s, FakeLLM('x'), shadow=True))
        self.assertEqual(r['mode'], 'dry')
        self.assertEqual(s.queries, [])

    def test_serpapi_text_fallback_rows(self):
        rows = ailayer.parse_search('1. Title - snippet text https://a.example/p\n2. Other - more https://b.example/q')
        self.assertEqual([r['link'] for r in rows], ['https://a.example/p', 'https://b.example/q'])
        self.assertEqual(ailayer.parse_search('No results found.'), [])


class FakeResp:
    def __init__(self, data):
        self.b = json.dumps(data).encode()

    def read(self, n=-1):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SerperProvider(unittest.TestCase):
    DATA = {'answerBox': {'answer': '29.99', 'title': 'Price', 'link': 'https://a.example/price'},
            'organic': [{'title': 'T%d' % i, 'link': 'https://o.example/%d' % i, 'snippet': 'S', 'date': '2 days ago'} for i in range(10)]}

    def make(self, data=None, fail=False):
        self.seen = []

        def opener(req, timeout=None):
            self.seen.append(req)
            if fail:
                raise OSError('boom')
            return FakeResp(data or self.DATA)
        o = Serper.make_opts({'name': 'serper', 'enabled': True}, 'KEY123456')
        o['shadow'] = False
        return Serper(o, tempfile.mkdtemp(), opener=opener, today=lambda: '2026-11-05')

    def ask(self, p, text='price of game x', fmt=None):
        return p.ask({'text': text, 'capability': 'web_search', 'request_id': 'r', 'context': {'format': fmt} if fmt else {}})

    def test_key_header_body_and_json(self):
        p = self.make()
        r = self.ask(p, fmt='json')
        self.assertTrue(r['ok'])
        rows = json.loads(r['answer'])
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]['link'], 'https://a.example/price')
        req = self.seen[0]
        self.assertEqual(req.headers['X-api-key'], 'KEY123456')
        self.assertEqual(json.loads(req.data)['q'], 'price of game x')
        self.assertNotIn('KEY123456', repr(r) + repr(p.status()))

    def test_text_format_and_cache(self):
        p = self.make()
        a = self.ask(p)
        b = self.ask(p)
        self.assertEqual(len(self.seen), 1)
        self.assertTrue(b['cached'])
        self.assertEqual(a['answer'], b['answer'])
        self.assertTrue(a['answer'].startswith('1. Price'))
        self.ask(p, fmt='json')                     # other format = other cache entry
        self.assertEqual(len(self.seen), 2)
        self.assertEqual(p.used_today(), 2)

    def test_query_not_scrubbed(self):
        p = self.make()
        self.ask(p, text='game 1234567 price')
        self.assertIn('1234567', json.loads(self.seen[0].data)['q'])

    def test_errors_and_empty_not_cached(self):
        p = self.make(fail=True)
        self.assertFalse(self.ask(p)['ok'])
        self.assertFalse(self.ask(p)['ok'])
        self.assertEqual(len(self.seen), 2)
        q = self.make(data={'organic': []})
        self.assertEqual(self.ask(q, fmt='json')['answer'], '[]')
        self.ask(q, fmt='json')
        self.assertEqual(len(self.seen), 2)

    def test_daily_cap_and_no_key(self):
        p = self.make()
        p.opts['daily_cap'] = 1
        self.ask(p, text='one')
        self.assertEqual(self.ask(p, text='two')['why'], 'cap')
        p2 = self.make()
        p2.opts['key'] = ''
        self.assertEqual(self.ask(p2)['why'], 'not_configured')

    def test_parse_results_limits(self):
        self.assertEqual(len(parse_results(self.DATA)), 8)
        self.assertEqual(parse_results({'organic': [{'title': 'x', 'link': 'javascript:1'}]}), [])


if __name__ == '__main__':
    unittest.main()
