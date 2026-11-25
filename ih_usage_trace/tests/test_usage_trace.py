"""ih_usage_trace: database, endpoint auth, shadow/live, jump flag, retention, page files."""
import json, os, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / 'ih_usage_trace'
sys.path.insert(0, str(ADDON / 'app'))
import main as M
import store as S

NOW = 1_800_000_000


class Clock:
    def __init__(self, t=NOW): self.t = t
    def __call__(self): return self.t


def mk(clock=None):
    d = tempfile.mkdtemp()
    return S.UsageStore(d, clock=clock or Clock())


def samples(vals, step=300, start=NOW - 3000):
    return [{'ts': start + i * step, 'session': v, 'week': 10.0, 'pace': 0} for i, v in enumerate(vals)]


class Db(unittest.TestCase):
    def test_schema_and_samples(self):
        st = mk()
        self.assertEqual(st.db.schema_version(), len(S.MIGRATIONS))
        self.assertTrue(st.add_sample('3.0', '20.5', '-1', ts=NOW - 10))
        self.assertFalse(st.add_sample('unavailable', 'unknown', None))
        self.assertEqual(st.series(), [{'ts': NOW - 10, 'session': 3.0, 'week': 20.5, 'pace': -1.0}])

    def test_event_open_then_close_by_ref(self):
        st = mk()
        a = st.add_event('Worker', 'task 5', start=NOW - 100, ref='r1')
        self.assertEqual(a['source'], 'worker'); self.assertIsNone(a['end']); self.assertTrue(a['open'])
        b = st.add_event('worker', 'task 5', start=NOW - 100, end=NOW - 40, ref='r1')
        self.assertEqual(b['id'], a['id']); self.assertEqual(b['end'], NOW - 40)
        self.assertEqual(len(st.events()), 1)

    def test_event_validation_and_text_is_trimmed(self):
        st = mk()
        with self.assertRaises(S.BadInput): st.add_event('', 'x')
        with self.assertRaises(S.BadInput): st.add_event('a', 'x', start=NOW, end=NOW - 5)
        with self.assertRaises(S.BadInput): st.add_event('a', 'x', start='not a time')
        e = st.add_event('a', 'y' * 500, start='2026-11-25T10:00:00Z')
        self.assertEqual(len(e['label']), S.MAX_TEXT)
        self.assertEqual(e['start_iso'], '2026-11-25T10:00:00Z')

    def test_retention(self):
        c = Clock(); st = mk(c)
        old = NOW - 61 * 86400
        st.add_sample(1, 1, 0, ts=old); st.add_sample(2, 2, 0, ts=NOW - 5)
        st.add_event('a', 'old', start=old, end=old + 60); st.add_event('a', 'new', start=NOW - 5, end=NOW)
        self.assertEqual(st.purge(60), (1, 1))
        self.assertEqual(st.counts(), {'series': 1, 'events': 1})


class Jumps(unittest.TestCase):
    def ev(self, s, e): return {'start': s, 'end': e}

    def test_unattributed_flagged_and_event_clears_it(self):
        sm = samples([1, 1.5, 4, 4])            # +2.5 between samples 1 and 2
        j = S.find_jumps(sm, [], 2.0, NOW)
        self.assertEqual([(x['metric'], x['rise']) for x in j], [('session', 2.5)])
        step = (sm[1]['ts'], sm[2]['ts'])
        self.assertEqual(S.find_jumps(sm, [self.ev(step[0] + 10, step[0] + 20)], 2.0, NOW), [])
        self.assertEqual(len(S.find_jumps(sm, [self.ev(step[0] - 600, step[0] - 300)], 2.0, NOW)), 1)   # an event before the step does not explain it

    def test_open_event_covers_and_threshold_and_resets(self):
        sm = samples([1, 3.9, 0, 5])
        self.assertEqual(S.find_jumps(sm, [self.ev(sm[0]['ts'], None)], 2.0, NOW), [])
        self.assertEqual(len(S.find_jumps(sm, [], 3.0, NOW)), 1)   # 3.9-1=2.9 no; 0->5 yes
        self.assertEqual([x['rise'] for x in S.find_jumps(sm, [], 2.0, NOW)], [2.9, 5.0])

    def test_gap_not_flagged_and_week_measured(self):
        sm = [{'ts': NOW - 5000, 'session': 1, 'week': 1, 'pace': 0}, {'ts': NOW, 'session': 9, 'week': 9, 'pace': 0}]
        self.assertEqual(S.find_jumps(sm, [], 2.0, NOW), [])
        sm = [{'ts': NOW - 300, 'session': 1, 'week': 1, 'pace': 0}, {'ts': NOW, 'session': 1, 'week': 4, 'pace': 0}]
        self.assertEqual([x['metric'] for x in S.find_jumps(sm, [], 2.0, NOW)], ['week'])


class Http(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = mk(self.clock)
        self.opts = {'api_token': 'secret-token', 'mode': 'shadow', 'retention_days': 60, 'jump_threshold': 2.0}
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), M.make_api_handler(self.store, self.opts))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:%d' % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); M.DBTHREAD.submit(self.store.db.close).result()

    def call(self, method, path, body=None, token='secret-token'):
        h = {'Content-Type': 'application/json'}
        if token: h['Authorization'] = 'Bearer ' + token
        r = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(r, timeout=5) as x: return x.status, json.loads(x.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def test_health_open_everything_else_needs_token(self):
        with urllib.request.urlopen(self.base + '/healthz', timeout=5) as x: self.assertEqual(x.read(), b'ok')
        for m, p in (('GET', '/events'), ('GET', '/series'), ('GET', '/status'), ('POST', '/events')):
            self.assertEqual(self.call(m, p, {} if m == 'POST' else None, token=None)[0], 401)
            self.assertEqual(self.call(m, p, {} if m == 'POST' else None, token='wrong')[0], 401)

    def test_empty_token_refuses_all(self):
        self.opts['api_token'] = ''
        self.assertEqual(self.call('GET', '/events', token='')[0], 401)
        self.assertEqual(self.call('GET', '/events', token='x')[0], 401)

    def test_shadow_stores_nothing_live_stores(self):
        code, r = self.call('POST', '/events', {'source': 'worker', 'label': 'task 1'})
        self.assertEqual((code, r), (200, {'stored': False, 'shadow': True}))
        self.assertEqual(self.call('GET', '/events')[1]['events'], [])
        self.opts['mode'] = 'live'
        code, r = self.call('POST', '/events', {'source': 'worker', 'label': 'task 1', 'ref': 'a'})
        self.assertTrue(r['stored'])
        self.assertEqual(len(self.call('GET', '/events')[1]['events']), 1)
        self.assertEqual(len(self.call('GET', '/events?source=assistant')[1]['events']), 0)

    def test_bad_posts(self):
        self.opts['mode'] = 'live'
        self.assertEqual(self.call('POST', '/events', {'source': 'a'})[0], 400)
        self.assertEqual(self.call('POST', '/events', {'source': 'a', 'label': 'b', 'text': 'hello'})[0], 400)   # no message text field exists
        self.assertEqual(self.call('POST', '/events', [1])[0], 400)

    def test_series_has_jumps(self):
        self.store.add_sample(1, 1, 0, ts=NOW - 300); self.store.add_sample(5, 1, 0, ts=NOW)
        r = self.call('GET', '/series')[1]
        self.assertEqual(len(r['series']), 2); self.assertEqual(r['jumps'][0]['rise'], 4.0)


class Options(unittest.TestCase):
    def test_defaults_and_bounds(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'o.json')
        o = M.load_options(p)
        self.assertEqual((o['mode'], o['retention_days'], o['jump_threshold']), ('shadow', 60, 2.0))
        self.assertEqual(o['session_entity'], 'sensor.claude_usage_session_usage')
        open(p, 'w').write(json.dumps({'mode': 'bogus', 'retention_days': 9999, 'jump_threshold': 'x', 'api_token': ' t '}))
        o = M.load_options(p)
        self.assertEqual((o['mode'], o['retention_days'], o['jump_threshold'], o['api_token']), ('shadow', 365, 2.0, 't'))


class Sampler(unittest.TestCase):
    def test_sample_once_and_failure(self):
        st = mk()
        class HA:
            def state(self, e): return {'a': '4.0', 'b': '9.0', 'c': '-1.0'}[e]
        opts = {'session_entity': 'a', 'week_entity': 'b', 'pace_entity': 'c'}
        self.assertTrue(M.sample_once(st, HA(), opts))
        class Bad:
            def state(self, e): raise RuntimeError('down')
        self.assertFalse(M.sample_once(st, Bad(), opts))


class Files(unittest.TestCase):
    UI = ADDON / 'app' / 'ui'

    def test_shared_copies_nav_filter_and_safe_script(self):
        page = (self.UI / 'index.html').read_text(); css = (self.UI / 'ui.css').read_text(); js = (self.UI / 'ui.js').read_text()
        self.assertIn('<ih-nav data-home-href="/app/32c8114c_ih_orchestrator"', page)
        self.assertEqual((self.UI / 'ih-ui.js').read_text(), (ROOT / 'ih_ui' / 'components.js').read_text())
        self.assertEqual((self.UI / 'ih-ui.css').read_text(), (ROOT / 'ih_ui' / 'tokens.css').read_text())
        self.assertEqual((self.UI / 'ih-patterns.css').read_text(), (ROOT / 'ih_ui' / 'patterns.css').read_text())
        self.assertIn('type="search"', page); self.assertIn('id="f-count"', page)
        self.assertRegex(page, r'<button id="f-clear"[^>]*>Clear</button>')
        for sel in ('f-source', 'f-period'): self.assertRegex(page, r'<label for="%s">[^<]+</label>' % sel)
        self.assertNotIn('chip', page + css)
        for bad in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'confirm(', 'alert('): self.assertNotIn(bad, js)

    def test_versions_in_step_and_meta(self):
        cfg = (ADDON / 'config.yaml').read_text()
        ver = [l.split('"')[1] for l in cfg.splitlines() if l.startswith('version:')][0]
        self.assertEqual(json.loads((ADDON / 'app' / 'manifest.json').read_text())['version'], ver)
        self.assertEqual(M.VERSION, ver)
        self.assertIn('## ' + ver, (ADDON / 'CHANGELOG.md').read_text())
        for flag in ('boot: auto', 'watchdog:', 'ingress: true', 'mode: "shadow"'): self.assertIn(flag, cfg)
        self.assertTrue((ROOT / 'ih_meta' / 'ih_usage_trace.json').exists())


if __name__ == '__main__':
    unittest.main()
