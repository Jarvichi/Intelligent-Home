"""publicapis provider: fake opener, no real network."""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from providers.publicapis import PublicApis, MCP_URL
import providers as provmod
import ailayer


class Resp:
    def __init__(self, body, headers=None):
        self.b = body if isinstance(body, bytes) else body.encode()
        self.headers = headers or {}

    def read(self, n=-1):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def rpc(result, i=1):
    return json.dumps({'jsonrpc': '2.0', 'id': i, 'result': result})


def tool_ok(text):
    return {'content': [{'type': 'text', 'text': text}]}


class Fake:
    """Plays the server. sse=True answers as text/event-stream. expire_once: first tools/call with the first session gets HTTP 404."""
    def __init__(self, sse=False, expire_once=False, result=None):
        self.sse, self.expire_once, self.result = sse, expire_once, result or tool_ok('RESULT TEXT')
        self.reqs, self.sessions = [], 0

    def __call__(self, req, timeout=None):
        assert req.full_url == MCP_URL
        body = json.loads(req.data)
        h = {k.lower(): v for k, v in req.header_items()}
        self.reqs.append((body, h, timeout))
        m = body['method']
        if m == 'initialize':
            self.sessions += 1
            return Resp(self.wrap(rpc({'protocolVersion': '2025-06-18', 'serverInfo': {'name': 'publicapis-io'}}, body['id'])), {'mcp-session-id': 'sess%d' % self.sessions})
        if m == 'notifications/initialized':
            return Resp('', {})
        if self.expire_once and h.get('mcp-session-id') == 'sess1':
            raise urllib.error.HTTPError(MCP_URL, 404, 'gone', {}, io.BytesIO(b'x'))
        return Resp(self.wrap(rpc(self.result, body['id'])))

    def wrap(self, s):
        return 'event: message\ndata: %s\n\n' % s if self.sse else s

    def calls(self):
        return [r[0]['params'] for r in self.reqs if r[0]['method'] == 'tools/call']


class T(unittest.TestCase):
    def make(self, fake, settings=None, logs=None):
        d = tempfile.mkdtemp()
        o = PublicApis.make_opts({'name': 'publicapis', 'enabled': True, 'settings': dict({'shadow': False}, **(settings or {}))})
        return PublicApis(o, d, log=(logs.append if logs is not None else None), opener=fake)

    def ask(self, p, cap, text='', ctx=None, **kw):
        return p.ask(dict({'text': text, 'capability': cap, 'context': ctx or {}, 'request_id': 'r1'}, **kw))

    def test_configured_without_key(self):
        p = self.make(Fake())
        self.assertTrue(p.configured())
        self.assertTrue(p.healthy())
        self.assertEqual(p.models(), [])
        self.assertTrue(p.is_free())
        self.assertEqual(p.opts['daily_cap'], 200)
        self.assertEqual(p.opts['cache_hours'], 24)
        self.assertIn('publicapis', provmod.REGISTRY)

    def test_happy_json_and_session_flow(self):
        f = Fake()
        p = self.make(f)
        r = self.ask(p, 'api_search', 'weather forecast', {'auth': 'none'})
        self.assertTrue(r['ok'])
        self.assertEqual(r['answer'], 'RESULT TEXT')
        self.assertEqual([x[0]['method'] for x in f.reqs], ['initialize', 'notifications/initialized', 'tools/call'])
        self.assertEqual(f.reqs[0][0]['params']['protocolVersion'], '2025-06-18')
        self.assertNotIn('id', f.reqs[1][0])
        self.assertEqual(f.reqs[1][1]['mcp-session-id'], 'sess1')
        self.assertEqual(f.reqs[2][1]['mcp-session-id'], 'sess1')
        self.assertIn('text/event-stream', f.reqs[2][1]['accept'])
        self.assertTrue(f.reqs[2][1]['user-agent'].startswith('intelligent-home-ai-layer/'))
        self.assertEqual(f.calls()[0], {'name': 'search_apis', 'arguments': {'query': 'weather forecast', 'auth': 'none'}})
        # one session reused
        self.ask(p, 'api_info', 'open-meteo')
        self.assertEqual(f.sessions, 1)

    def test_happy_sse(self):
        p = self.make(Fake(sse=True))
        r = self.ask(p, 'api_offerings')
        self.assertTrue(r['ok'])
        self.assertEqual(r['answer'], 'RESULT TEXT')

    def test_structured_content_fallback_and_cap(self):
        p = self.make(Fake(result={'content': [], 'structuredContent': {'a': 1}}))
        self.assertEqual(self.ask(p, 'api_offerings')['answer'], '{"a": 1}')
        p = self.make(Fake(result={'content': [{'type': 'text', 'text': 'x' * 9000}]}))
        self.assertEqual(len(self.ask(p, 'api_offerings')['answer']), 6000)
        p = self.make(Fake(result={'content': [{'type': 'text', 'text': 'a'}, {'type': 'text', 'text': 'b'}]}))
        self.assertEqual(self.ask(p, 'api_offerings')['answer'], 'a\nb')

    def test_argument_mapping(self):
        f = Fake()
        p = self.make(f, {'cache_hours': 0})
        self.ask(p, 'api_search', 'maps', {'category': 'Geo', 'auth': 'bogus', 'free': True, 'https': 'yes', 'limit': 99})
        self.ask(p, 'api_search', 'maps', {'auth': 'oauth', 'https': False, 'limit': 5})
        self.ask(p, 'api_info', 'Open-Meteo')
        self.ask(p, 'api_info', 'ignored', {'slug': 'cat-facts'})
        self.ask(p, 'api_quickstart', 'open-meteo')
        self.ask(p, 'api_quickstart', 'x', {'slug': 'open-meteo', 'language': 'curl'})
        self.ask(p, 'api_quickstart', 'open-meteo', {'language': 'cobol'})
        self.ask(p, 'api_compare', 'a, b  c')
        self.ask(p, 'api_compare', 'ignored', {'slugs': ['a', 'b']})
        self.ask(p, 'api_offerings')
        got = [(c['name'], c['arguments']) for c in f.calls()]
        self.assertEqual(got, [
            ('search_apis', {'query': 'maps', 'category': 'Geo', 'free': True}),
            ('search_apis', {'query': 'maps', 'auth': 'oauth', 'https': False, 'limit': 5}),
            ('get_api', {'slug': 'open-meteo'}),
            ('get_api', {'slug': 'cat-facts'}),
            ('get_quickstart', {'slug': 'open-meteo', 'language': 'python'}),
            ('get_quickstart', {'slug': 'open-meteo', 'language': 'curl'}),
            ('get_quickstart', {'slug': 'open-meteo', 'language': 'python'}),
            ('compare_apis', {'slugs': ['a', 'b', 'c']}),
            ('compare_apis', {'slugs': ['a', 'b']}),
            ('list_offerings', {}),
        ])

    def test_invalid_compare_and_slug(self):
        f = Fake()
        p = self.make(f)
        for text, ctx in (('only-one', {}), ('a b c d e f', {}), ('x', {'slugs': ['a']}), ('a, b', {'slugs': 'nope'})):
            r = self.ask(p, 'api_compare', text, ctx)
            if text == 'a, b':
                self.assertTrue(r['ok'])      # slugs not a list: falls back to the text
                continue
            self.assertFalse(r['ok'])
            self.assertEqual(r['why'], 'bad_request')
        self.assertEqual(self.ask(p, 'api_info', 'not a slug!')['why'], 'bad_request')
        self.assertEqual(self.ask(p, 'bogus_cap', 'x')['why'], 'bad_request')
        self.assertEqual(self.ask(p, 'api_search', '')['why'], 'bad_request')
        self.assertEqual(self.ask(p, 'api_search', 'x', model='m')['why'], 'model_not_available')

    def test_is_error_not_ok_and_not_cached(self):
        f = Fake(result={'isError': True, 'content': [{'type': 'text', 'text': 'boom'}]})
        p = self.make(f)
        r = self.ask(p, 'api_info', 'nope')
        self.assertFalse(r['ok'])
        self.assertEqual(r['why'], 'upstream_error')
        self.ask(p, 'api_info', 'nope')
        self.assertEqual(len(f.calls()), 2)
        self.assertEqual(p._read('cache.json'), {})

    def test_cache_hit(self):
        f = Fake()
        now = [1000.0]
        d = tempfile.mkdtemp()
        o = PublicApis.make_opts({'name': 'publicapis', 'enabled': True, 'settings': {'shadow': False}})
        p = PublicApis(o, d, opener=f, clock=lambda: now[0])
        a = self.ask(p, 'api_search', 'Weather', {'auth': 'none'})
        b = self.ask(p, 'api_search', 'Weather', {'auth': 'none'})
        self.assertFalse(a['cached'])
        self.assertTrue(b['cached'])
        self.assertEqual(len(f.calls()), 1)
        self.assertEqual(p.used_today(), 1)
        self.ask(p, 'api_search', 'Weather', {'auth': 'oauth'})      # different arguments: new call
        self.assertEqual(len(f.calls()), 2)
        now[0] += 25 * 3600                                          # expired
        self.ask(p, 'api_search', 'Weather', {'auth': 'none'})
        self.assertEqual(len(f.calls()), 3)

    def test_session_reinit_once(self):
        f = Fake(expire_once=True)
        p = self.make(f)
        r = self.ask(p, 'api_info', 'x1')
        self.assertTrue(r['ok'])
        self.assertEqual(f.sessions, 2)
        self.assertEqual(len(f.calls()), 2)
        self.assertEqual(f.reqs[-1][1]['mcp-session-id'], 'sess2')

        class Always404(Fake):
            def __call__(s, req, timeout=None):
                body = json.loads(req.data)
                if body['method'] == 'tools/call':
                    raise urllib.error.HTTPError(MCP_URL, 404, 'gone', {}, io.BytesIO(b'x'))
                return Fake.__call__(s, req, timeout)
        f2 = Always404()
        r = self.make(f2).ask({'text': 'x1', 'capability': 'api_info', 'context': {}})
        self.assertFalse(r['ok'])
        self.assertEqual(r['why'], 'upstream_error')
        self.assertEqual(f2.sessions, 2)      # exactly one retry

    def test_text_never_logged(self):
        logs = []
        f = Fake()
        p = self.make(f, logs=logs)
        self.ask(p, 'api_search', 'SECRETQUERY123 weather', {'category': 'SECRETCAT'})
        self.ask(p, 'api_compare', 'SECRETONE')
        self.ask(p, 'api_info', 'SECRETSLUG')
        self.assertTrue(logs)
        self.assertFalse([l for l in logs if 'SECRET' in l])
        self.assertTrue(any('agent=ih_ai_layer_publicapis' in l for l in logs))

    def test_shadow_and_cap_and_stop(self):
        f = Fake()
        o = PublicApis.make_opts({'name': 'publicapis', 'enabled': True, 'settings': {'shadow': True}})
        self.assertTrue(o['shadow'])
        p = PublicApis(o, tempfile.mkdtemp(), opener=f)
        self.assertEqual(self.ask(p, 'api_offerings')['why'], 'shadow')
        self.assertEqual(f.reqs, [])
        p = self.make(f, {'daily_cap': 1})
        self.assertTrue(self.ask(p, 'api_info', 'a1')['ok'])
        self.assertEqual(self.ask(p, 'api_info', 'a2')['why'], 'cap')
        open(os.path.join(p.data, 'STOP'), 'w').close()
        self.assertEqual(self.ask(p, 'api_info', 'a3')['why'], 'stopped')

    def test_classifier(self):
        self.assertEqual(ailayer.classify('find a public API for weather data'), 'api_search')
        self.assertEqual(ailayer.classify('is there a free API to get cat facts'), 'api_search')
        self.assertEqual(ailayer.classify('what is the latest news'), 'web_search')
        self.assertEqual(ailayer.classify('cinema showtimes tonight'), 'showtimes')

    def test_gemini_default(self):
        self.assertEqual(provmod.REGISTRY['gemini'].DEFAULT_MODEL, 'gemini-3.5-flash')


if __name__ == '__main__':
    unittest.main()
