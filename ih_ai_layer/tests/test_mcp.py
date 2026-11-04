"""Generic mcp provider: fake opener, no real network."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import providers as provmod
from providers.mcp import Mcp, NotAllowed, parse_spec
import ailayer

SECRET = 'sk-SECRETKEY123'


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


class Server:
    """Fake MCP server keyed by URL. offered: {tool: annotations}."""
    def __init__(self, url, offered, sse=False, reply='ANSWER'):
        self.url, self.offered, self.sse, self.reply = url, offered, sse, reply
        self.reqs = []

    def wrap(self, s):
        return 'event: message\ndata: %s\n\n' % s if self.sse else s

    def handle(self, req):
        body = json.loads(req.data)
        h = {k.lower(): v for k, v in req.header_items()}
        self.reqs.append((body, h))
        m = body['method']
        r = lambda res: Resp(self.wrap(json.dumps({'jsonrpc': '2.0', 'id': body.get('id'), 'result': res})))
        if m == 'initialize':
            return Resp(self.wrap(json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': {}})), {'mcp-session-id': 's1'})
        if m == 'notifications/initialized':
            return Resp('')
        if m == 'tools/list':
            return r({'tools': [{'name': n, 'annotations': a} for n, a in self.offered.items()]})
        return r({'content': [{'type': 'text', 'text': self.reply}]})

    def calls(self):
        return [b['params'] for b, h in self.reqs if b['method'] == 'tools/call']


class Net:
    def __init__(self, *servers):
        self.servers = {s.url: s for s in servers}
        self.n = 0

    def __call__(self, req, timeout=None):
        self.n += 1
        return self.servers[req.full_url].handle(req)


def entry(sid='aa', url='https://a.example.com/mcp', tools=None, key='', **extra):
    s = {'id': sid, 'url': url, 'shadow': False, 'tools': tools if tools is not None else {'search': {'capability': 'a_search', 'arg': 'query', 'context_args': ['limit']}}}
    s.update(extra)
    return {'name': 'mcp', 'enabled': True, 'key': key, 'settings': json.dumps(s)}


def build(entries, net, logs=None):
    return provmod.build(entries, {}, tempfile.mkdtemp(), (logs.append if logs is not None else (lambda m: None)), net, lambda: 1000.0)


class T(unittest.TestCase):
    def test_two_instances_side_by_side(self):
        a = Server('https://a.example.com/mcp', {'search': {'readOnlyHint': True}}, reply='FROM A')
        b = Server('https://b.example.com/mcp', {'lookup': {'readOnlyHint': True}}, sse=True, reply='FROM B')
        net = Net(a, b)
        provs = build([entry('aa'), entry('bb', 'https://b.example.com/mcp', {'lookup': {'capability': 'b_lookup', 'arg': 'q'}})], net)
        self.assertEqual(sorted(provs), ['ih_ai_layer_mcp_aa', 'ih_ai_layer_mcp_bb'])
        ra = provs['ih_ai_layer_mcp_aa'].ask({'text': 'hello', 'capability': 'a_search', 'request_id': 'r1', 'context': {'limit': 3, 'junk': 1}})
        rb = provs['ih_ai_layer_mcp_bb'].ask({'text': 'hello', 'capability': 'b_lookup', 'request_id': 'r2'})
        self.assertEqual((ra['answer'], rb['answer']), ('FROM A', 'FROM B'))
        self.assertEqual(a.calls(), [{'name': 'search', 'arguments': {'query': 'hello', 'limit': 3}}])
        self.assertEqual(b.calls(), [{'name': 'lookup', 'arguments': {'q': 'hello'}}])
        # an instance refuses another instance's capability
        self.assertFalse(provs['ih_ai_layer_mcp_aa'].ask({'text': 'x', 'capability': 'b_lookup', 'request_id': 'r3'})['ok'])
        st = provs['ih_ai_layer_mcp_aa'].status()
        self.assertEqual((st['id'], st['host'], st['capabilities'], st['tools_available'], st['write_tools']), ('aa', 'a.example.com', ['a_search'], ['search'], []))
        self.assertNotIn('/mcp', json.dumps(st))

    def test_allowlist_enforced(self):
        s = Server('https://a.example.com/mcp', {'search': {}, 'start_deal': {}})
        net = Net(s)
        p = build([entry()], net)['ih_ai_layer_mcp_aa']
        n = net.n
        with self.assertRaises(NotAllowed):
            p.invoke('start_deal', {}, 5)
        self.assertEqual(net.n, n)          # no network call
        # a caller cannot name a tool; an unknown capability is refused
        self.assertFalse(p.ask({'text': 'x', 'capability': 'start_deal', 'request_id': 'r', 'context': {'tool': 'start_deal'}})['ok'])
        p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r', 'context': {'tool': 'start_deal', 'name': 'start_deal'}})
        self.assertEqual([c['name'] for c in s.calls()], ['search'])
        self.assertEqual(s.calls()[0]['arguments'], {'query': 'x'})

    def test_tools_list_filtering_and_write_tool(self):
        s = Server('https://a.example.com/mcp', {'search': {'readOnlyHint': False}, 'other': {}})
        logs = []
        p = build([entry(tools={'search': {'capability': 'a_search', 'arg': 'q'}, 'gone': {'capability': 'a_gone', 'arg': 'q'}})], Net(s), logs)['ih_ai_layer_mcp_aa']
        self.assertTrue(p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})['ok'])
        r = p.ask({'text': 'x', 'capability': 'a_gone', 'request_id': 'r2'})
        self.assertFalse(r['ok'])
        self.assertEqual(r['why'], 'tool_unavailable')
        self.assertEqual([c['name'] for c in s.calls()], ['search'])
        st = p.status()
        self.assertEqual((st['tools_allowed'], st['tools_available'], st['write_tools']), (['gone', 'search'], ['search'], ['search']))
        self.assertTrue(any('missing=gone' in l for l in logs))
        self.assertTrue(any('write_tool' in l for l in logs))
        # tools/list is read once, not per ask
        self.assertEqual(len([b for b, h in s.reqs if b['method'] == 'tools/list']), 1)

    def test_auth_header(self):
        s = Server('https://a.example.com/mcp', {'search': {}})
        p = build([entry(key=SECRET, auth_header='Authorization')], Net(s))['ih_ai_layer_mcp_aa']
        p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})
        self.assertTrue(all(h.get('authorization') == 'Bearer ' + SECRET for b, h in s.reqs))
        s2 = Server('https://a.example.com/mcp', {'search': {}})
        p = build([entry(key='Basic abc', auth_header='Authorization')], Net(s2))['ih_ai_layer_mcp_aa']
        p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})
        self.assertEqual(s2.reqs[0][1]['authorization'], 'Basic abc')
        s3 = Server('https://a.example.com/mcp', {'search': {}})
        p = build([entry(key=SECRET, auth_header='X-Api-Key')], Net(s3))['ih_ai_layer_mcp_aa']
        p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})
        self.assertEqual(s3.reqs[0][1]['x-api-key'], SECRET)
        self.assertNotIn('authorization', s3.reqs[0][1])
        # no header configured: the key is not sent
        s4 = Server('https://a.example.com/mcp', {'search': {}})
        p = build([entry(key=SECRET)], Net(s4))['ih_ai_layer_mcp_aa']
        p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})
        self.assertNotIn(SECRET, json.dumps(s4.reqs))
        # header set but no key: not configured, nothing sent
        s5 = Server('https://a.example.com/mcp', {'search': {}})
        p = build([entry(auth_header='Authorization')], Net(s5))['ih_ai_layer_mcp_aa']
        r = p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})
        self.assertEqual((r['ok'], r['why'], s5.reqs), (False, 'not_configured', []))

    def test_bad_settings_skipped(self):
        logs = []
        bad = [entry(sid=''), entry(sid='Bad-Id'), entry(url='http://a.example.com/mcp'), entry(tools={}),
               entry(tools={'t': {'capability': 'general_qa'}}), entry(auth_header='bad header'),
               {'name': 'mcp', 'enabled': True, 'settings': 'not json'}]
        provs = build(bad + [entry('ok1')], Net(), logs)
        self.assertEqual(list(provs), ['ih_ai_layer_mcp_ok1'])
        self.assertEqual(len([l for l in logs if l.startswith('mcp entry skipped')]), len(bad))
        self.assertIsNone(parse_spec({'settings': '{}'})[0])

    def test_duplicate_id_first_wins_logged_once(self):
        logs = []
        provs = build([entry('dd'), entry('dd', 'https://b.example.com/mcp'), entry('dd', 'https://c.example.com/mcp')], Net(), logs)
        self.assertEqual(list(provs), ['ih_ai_layer_mcp_dd'])
        self.assertEqual(provs['ih_ai_layer_mcp_dd'].spec['url'], 'https://a.example.com/mcp')
        self.assertEqual(len([l for l in logs if 'listed twice' in l]), 1)

    def test_routing_explicit_capability_and_keywords(self):
        s = Server('https://a.example.com/mcp', {'search': {'readOnlyHint': True}}, reply='ROUTED')
        entries = [entry(keywords={'a_search': ['zorblax', 'blue widget'], 'current_facts': ['bogus'], 'ghost': ['x']})]
        layer = ailayer.Layer({'shadow': False, 'rate_per_min': 100, 'max_tries': 2, 'agent_timeout_s': 5, 'providers': entries, 'provider_keys': {}},
                              data=tempfile.mkdtemp(), provider_opener=Net(s))
        try:
            self.assertEqual(ailayer.classify('tell me about a zorblax'), 'a_search')
            self.assertEqual(ailayer.classify('the blue widget please'), 'a_search')
            self.assertNotEqual(ailayer.classify('xzorblaxy'), 'a_search')            # whole words only
            self.assertEqual(ailayer.classify('latest news'), 'current_facts')       # built-ins untouched
            self.assertNotIn('bogus', ailayer.KEYWORDS['current_facts'])
            self.assertNotIn('ghost', ailayer.KEYWORDS)
            code, r = layer.request({'text': 'anything', 'capability': 'a_search', 'request_id': 'q1', 'trace_id': 'a' * 32})
            self.assertEqual((r['ok'], r['answer'], r['agent']), (True, 'ROUTED', 'ih_ai_layer_mcp_aa'))
            ask = [x for x in r['trace_spans'] if x['operation'] == 'ask'][0]
            self.assertEqual((ask['component'], ask['attrs']['agent']), ('ih_ai_layer_mcp_aa', 'mcp_aa'))
            self.assertNotIn('model', ask['attrs'])
            self.assertEqual(ask['attrs']['chars'], 6)
            code, r = layer.request({'text': 'a zorblax thing', 'request_id': 'q2'})
            self.assertEqual((r['capability'], r['classified'], r['ok']), ('a_search', True, True))
            # a layer without mcp entries restores the built-in table
            ailayer.Layer({'shadow': False, 'providers': [], 'provider_keys': {}}, data=tempfile.mkdtemp())
            self.assertNotIn('a_search', ailayer.KEYWORDS)
            self.assertEqual(list(ailayer.KEYWORDS), list(ailayer.BASE_KEYWORDS))
        finally:
            ailayer.merge_keywords({})

    def test_text_and_secrets_never_logged(self):
        s = Server('https://a.example.com/mcp', {'search': {}}, reply='ANSWERTEXT-xyz')
        logs = []
        p = build([entry(key=SECRET, auth_header='Authorization')], Net(s), logs)['ih_ai_layer_mcp_aa']
        p.ask({'text': 'very private question text', 'capability': 'a_search', 'request_id': 'r', 'context': {'limit': 'ctxvalue99'}})
        blob = '\n'.join(logs)
        for bad in ('very private', 'ctxvalue99', SECRET, 'ANSWERTEXT'):
            self.assertNotIn(bad, blob)
        self.assertTrue(any(l.startswith('ask ') for l in logs))
        self.assertNotIn(SECRET, json.dumps(p.status()))

    def test_shadow_cap_cache_and_errors(self):
        s = Server('https://a.example.com/mcp', {'search': {'readOnlyHint': True}})
        net = Net(s)
        p = build([entry(shadow=True)], net)['ih_ai_layer_mcp_aa']
        r = p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})
        self.assertEqual((r['ok'], r.get('mode'), net.n), (True, 'dry', 0))
        p = build([entry(daily_cap=1, cache_hours=1)], Net(s))['ih_ai_layer_mcp_aa']
        self.assertTrue(p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})['ok'])
        self.assertTrue(p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'}).get('cached'))
        self.assertEqual(p.ask({'text': 'y', 'capability': 'a_search', 'request_id': 'r'})['why'], 'cap')
        # not read-only: never cached
        s2 = Server('https://a.example.com/mcp', {'search': {}})
        p = build([entry(cache_hours=1)], Net(s2))['ih_ai_layer_mcp_aa']
        p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})
        self.assertFalse(p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'}).get('cached'))
        # isError
        s3 = Server('https://a.example.com/mcp', {'search': {}})
        s3.handle_orig = s3.handle
        def h(req):
            r = s3.handle_orig(req)
            if json.loads(req.data)['method'] == 'tools/call':
                return Resp(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'isError': True, 'content': [{'type': 'text', 'text': 'boom'}]}}))
            return r
        s3.handle = h
        p = build([entry()], Net(s3))['ih_ai_layer_mcp_aa']
        r = p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})
        self.assertEqual((r['ok'], r['why']), (False, 'upstream_error'))

    def test_max_chars(self):
        s = Server('https://a.example.com/mcp', {'search': {}}, reply='z' * 500)
        p = build([entry(max_chars=100)], Net(s))['ih_ai_layer_mcp_aa']
        self.assertEqual(len(p.ask({'text': 'x', 'capability': 'a_search', 'request_id': 'r'})['answer']), 100)


if __name__ == '__main__':
    unittest.main()
