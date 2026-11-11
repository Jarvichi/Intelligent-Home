import json, os, sys, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import main
from test_usage_autoreply import UsageBase, SeqRng, RESET

TOK = 'test-token'


class OwnTextsTests(UsageBase):
    def setUp(self):
        self.a = self.make(self.out_states())
        self.a.opts['api_token'] = TOK
        self.a.rng = SeqRng([0] * 50)

    def text(self, key='aa', reset=RESET):
        return self.a.usage_reply_text(key, reset)

    def put(self, key='aa', **body):
        return self.a.usage_texts_set(key, body)

    def test_store_beats_options(self):
        self.a.cfg['aa']['usage_texts'] = ['opt {time}']
        self.assertEqual(self.put(texts=['own {time}'])[0], 200)
        self.assertEqual(self.text(), 'own 13:05')

    def test_store_beats_options_notime(self):
        self.a.cfg['aa']['usage_texts_notime'] = ['opt']
        self.put(notime=['own {time}'])
        self.assertEqual(self.text(reset=None), 'own it resets')

    def test_one_list_in_store_other_falls_to_options(self):
        self.a.cfg['aa']['usage_texts_notime'] = ['opt']
        self.put(texts=['own'])
        self.assertEqual(self.text(), 'own')
        self.assertEqual(self.text(reset=None), 'opt')

    def test_fallback_when_cleared(self):
        self.a.cfg['aa']['usage_texts'] = ['opt {time}']
        self.put(texts=['own'])
        self.assertEqual(self.put(texts=[], notime=[])[0], 200)
        self.assertEqual(self.text(), 'opt 13:05')
        self.assertNotIn(main.USAGE_OWN_KEY, self.a.stores['aa'].state())

    def test_fallback_to_single_and_builtin(self):
        self.a.cfg['aa']['usage_text'] = 'single {t}'
        self.assertEqual(self.text(), 'single 13:05')
        self.a.cfg['aa']['usage_text'] = ''
        self.assertTrue(self.text().startswith('I cannot read'))

    def test_bad_store_entries_are_skipped_at_send(self):
        self.a.stores['aa'].update_state(lambda s: s.__setitem__(main.USAGE_OWN_KEY, {'texts': ['bad {x}'], 'notime': []}))
        self.a.cfg['aa']['usage_text'] = 'single {t}'
        self.assertEqual(self.text(), 'single 13:05')

    def test_cap(self):
        n = main.USAGE_VARIANTS_MAX
        self.assertEqual(self.put(texts=['t'] * n)[0], 200)
        code, r = self.put(texts=['t'] * (n + 1))
        self.assertEqual(code, 400); self.assertIn('at most', r['why'])
        code, r = self.put(notime=['t'] * (n + 1))
        self.assertEqual(code, 400)

    def test_bad_placeholder(self):
        for bad in ('hi {name}', 'stray } brace', 'open { brace', '{}', '{time'):
            code, r = self.put(texts=[bad])
            self.assertEqual(code, 400, bad); self.assertIn('placeholder', r['why'])
        self.assertEqual(self.put(texts=['ok {time} and {time}'])[0], 200)

    def test_too_long_empty_and_wrong_type(self):
        m = main.USAGE_VARIANT_MAX_CHARS
        self.assertEqual(self.put(texts=['x' * (m + 1)])[0], 400)
        self.assertEqual(self.put(texts=['x' * m])[0], 200)
        for bad in ([''], ['   '], [5], 'text', {'a': 1}):
            self.assertEqual(self.put(texts=bad)[0], 400, bad)

    def test_rejected_write_keeps_old_lists_and_does_not_count(self):
        self.put(texts=['keep'])
        self.put(texts=['bad {x}'])
        self.assertEqual(self.a.usage_texts_view('aa')[1]['texts'], ['keep'])
        self.assertEqual(self.a.stores['aa'].state()['usage_own_writes']['n'], 1)

    def test_replace_not_merge(self):
        self.put(texts=['one'], notime=['n1'])
        self.put(texts=['two'])
        v = self.a.usage_texts_view('aa')[1]
        self.assertEqual((v['texts'], v['notime']), (['two'], []))

    def test_isolation(self):
        self.put('aa', texts=['mine'])
        self.assertEqual(self.text('bb'), self.a.usage_reply_text('bb', RESET))
        self.assertTrue(self.text('bb').startswith('I cannot read'))
        self.assertEqual(self.a.usage_texts_view('bb')[1]['source'], 'builtin')
        self.assertEqual(self.a.usage_texts_view('aa')[1]['source'], 'store')

    def test_unknown_persona_404(self):
        self.assertEqual(self.a.usage_texts_set('zz', {'texts': ['a']})[0], 404)
        self.assertEqual(self.a.usage_texts_view('zz')[0], 404)

    def test_rate_limit_and_next_day(self):
        for i in range(main.USAGE_OWN_WRITES_PER_DAY):
            self.assertEqual(self.put(texts=['t%d' % i])[0], 200)
        code, _ = self.put(texts=['over'])
        self.assertEqual(code, 429)
        self.assertEqual(self.a.usage_texts_view('aa')[1]['texts'], ['t%d' % (main.USAGE_OWN_WRITES_PER_DAY - 1)])
        self.assertEqual(self.put('bb', texts=['other'])[0], 200)   # per persona
        self.now[0] += 86400
        self.assertEqual(self.put(texts=['next day'])[0], 200)

    def test_view_sources(self):
        v = self.a.usage_texts_view('aa')[1]
        self.assertEqual((v['source'], v['texts'], v['notime']), ('builtin', [], []))
        self.a.cfg['aa']['usage_texts'] = ['o']
        v = self.a.usage_texts_view('aa')[1]
        self.assertEqual((v['source'], v['source_texts'], v['source_notime'], v['texts']), ('options', 'options', 'builtin', ['o']))
        self.put(notime=['n'])
        v = self.a.usage_texts_view('aa')[1]
        self.assertEqual((v['source'], v['source_texts'], v['source_notime']), ('store', 'options', 'store'))

    def test_text_is_never_logged(self):
        logs = []
        self.a.log = lambda *x: logs.append(' '.join(str(i) for i in x))
        self.put(texts=['secret wording'])
        self.assertTrue(logs); self.assertFalse(any('secret' in l for l in logs))


class HttpTests(UsageBase):
    def setUp(self):
        self.a = self.make(self.out_states())
        self.a.opts['api_token'] = TOK
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), main.make_handler(self.a))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:%d' % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()

    def call(self, method, path, body=None, token=TOK):
        h = {'Content-Type': 'application/json'}
        if token:
            h['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(self.base + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'null')

    def test_post_then_get(self):
        code, r = self.call('POST', '/persona/aa/usage_texts', {'texts': ['a {time}'], 'notime': ['b']})
        self.assertEqual(code, 200); self.assertEqual(r['source'], 'store')
        code, r = self.call('GET', '/persona/aa/usage_texts')
        self.assertEqual((code, r['texts'], r['notime']), (200, ['a {time}'], ['b']))
        self.assertEqual(self.call('GET', '/persona/bb/usage_texts')[1]['source'], 'builtin')

    def test_bad_body_400(self):
        code, r = self.call('POST', '/persona/aa/usage_texts', {'texts': ['x {no}']})
        self.assertEqual(code, 400); self.assertFalse(r['ok'])

    def test_auth_and_404(self):
        self.assertEqual(self.call('GET', '/persona/aa/usage_texts', token=None)[0], 401)
        self.assertEqual(self.call('POST', '/persona/aa/usage_texts', {'texts': ['a']}, token='wrong')[0], 401)
        self.assertEqual(self.call('GET', '/persona/zz/usage_texts')[0], 404)
        self.assertEqual(self.call('POST', '/persona/zz/usage_texts', {'texts': ['a']})[0], 404)

    def test_rate_limit_429(self):
        for i in range(main.USAGE_OWN_WRITES_PER_DAY):
            self.assertEqual(self.call('POST', '/persona/aa/usage_texts', {'texts': ['t']})[0], 200)
        self.assertEqual(self.call('POST', '/persona/aa/usage_texts', {'texts': ['t']})[0], 429)


if __name__ == '__main__':
    unittest.main()
