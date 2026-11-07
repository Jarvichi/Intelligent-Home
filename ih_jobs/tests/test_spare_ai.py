"""spare_ai plugin: fake layer and fake GitHub, no real network."""
import datetime
import json
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import ihlib.http
import ihlib.state
from plugins import spare_ai as S

IP = '.'.join(['10', '9', '8', '7'])
MACX = ':'.join(['aa'] * 6)
TOKEN = 'tok-secret-123456'
NOW = datetime.datetime(2026, 11, 5, 22, 30, tzinfo=datetime.timezone.utc)


def prov(name, used=0, cap=50, **kw):
    d = {'name': 'ih_ai_layer_' + name, 'provider': name, 'model': name + '-model', 'configured': True, 'shadow': False, 'kill_switch': False, 'free': True,
         'used_today': used, 'daily_cap': cap}
    d.update(kw)
    return d


class Resp:
    def __init__(self, obj):
        self.b = json.dumps(obj).encode()
    def read(self, n=-1): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Fake:
    """Opener serving the layer and GitHub."""
    def __init__(self, providers, fail=(), commits=None, answer=None):
        self.providers, self.fail, self.commits = providers, set(fail), commits or []
        self.requests, self.answer = [], answer
        self.tree, self.files = [], {}

    def __call__(self, req, timeout=0):
        url = req.full_url
        if url.endswith('/status'):
            assert req.get_header('Authorization') == 'Bearer ' + TOKEN
            return Resp({'providers': self.providers})
        if url.endswith('/request'):
            b = json.loads(req.data)
            self.requests.append(b)
            model = b['model']
            if model.split('-')[0] in self.fail:
                return Resp({'ok': False, 'error': 'x'})
            return Resp({'ok': True, 'answer': (self.answer or 'ans from ') + model, 'agent': 'a', 'mode': 'live'})
        if '/git/trees/' in url:
            return Resp({'tree': [{'type': 'blob', 'path': p, 'size': z} for p, z in self.tree]})
        if '/contents/' in url:
            import base64, urllib.parse
            path = urllib.parse.unquote(url.split('/contents/', 1)[1])
            return Resp({'encoding': 'base64', 'content': base64.b64encode(self.files.get(path, 'x = 1\n').encode()).decode()})
        if '/commits?' in url:
            return Resp(self.commits)
        if '/commits/' in url:
            sha = url.rsplit('/', 1)[1]
            return Resp({'commit': {'message': 'Fix thing\n\nbody'}, 'files': [{'filename': 'a.py', 'patch': '+' + 'x' * 5000}]})
        raise AssertionError(url)


class Ctx:
    def __init__(self, tmp, opener, dry=False, **settings):
        self.settings = dict(S.DEFAULTS, folder=os.path.join(tmp, 'sp'), **settings)
        self.dry, self.now = dry, NOW
        self.logs = []
        self.http = ihlib.http.Http(opener, sleep=lambda s: None)
        self.state = ihlib.state.Store(os.path.join(tmp, 'data'))
        self.opts = {'ai_layer_token': TOKEN, 'ai_layer_url': 'http://layer'}

    def log(self, m): self.logs.append(m)
    def option(self, n): return self.opts.get(n, '')


def task(folder, name, **kw):
    o = {'id': name, 'kind': 'research', 'text': 'What is a heat pump?', 'guard': 'passed'}
    o.update(kw)
    os.makedirs(os.path.join(folder, 'queue'), exist_ok=True)
    with open(os.path.join(folder, 'queue', name + '.json'), 'w') as f:
        json.dump(o, f)


class Tests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.tmp = self.d.name

    def tearDown(self):
        self.d.cleanup()

    def test_budget_reserve_and_max_calls(self):
        st = {'providers': [prov('gemini', used=10, cap=50), prov('groq', used=48, cap=50), prov('mistral', cap=30)]}
        b, total = S.budget(st, ['gemini', 'groq', 'mistral'], 5, 120)
        self.assertEqual({k: v['spare'] for k, v in b.items()}, {'gemini': 35, 'groq': 0, 'mistral': 25})
        self.assertEqual(total, 60)
        self.assertEqual(S.budget(st, ['gemini', 'mistral'], 5, 20)[1], 20)

    def test_excluded_and_unusable_providers(self):
        st = {'providers': [prov('gemini'), prov('openai'), prov('groq', free=False), prov('mistral', shadow=True), prov('openrouter', configured=False)]}
        b, _ = S.budget(st, ['gemini', 'groq', 'mistral', 'openrouter'], 5, 100)
        self.assertEqual(list(b), ['gemini'])
        fake = Fake(st['providers'])
        task(os.path.join(self.tmp, 'sp'), 't1')
        ctx = Ctx(self.tmp, fake)
        S.run(ctx)
        self.assertTrue(all(r['model'] != 'openai-model' for r in fake.requests))

    def test_queue_refusals(self):
        f = os.path.join(self.tmp, 'sp')
        task(f, 'noguard', guard='no')
        task(f, 'long', text='x' * 3501)
        task(f, 'badkind', kind='evil')
        task(f, 'ok1')
        os.makedirs(os.path.join(f, 'queue'), exist_ok=True)
        open(os.path.join(f, 'queue', 'broken.json'), 'w').write('{nope')
        fake = Fake([prov('gemini'), prov('groq')])
        S.run(Ctx(self.tmp, fake, fanout=2))
        self.assertEqual(sorted(os.listdir(os.path.join(f, 'queue', 'refused'))), ['badkind.json', 'broken.json', 'long.json', 'noguard.json'])
        self.assertTrue(os.path.exists(os.path.join(f, 'queue', 'processed', 'ok1.json')))
        rep = json.load(open(os.path.join(f, 'reports', '20261105.json')))
        self.assertEqual((rep['tasks_done'], rep['tasks_refused']), (1, 4))

    def test_fanout_compare_with_one_failing(self):
        f = os.path.join(self.tmp, 'sp')
        task(f, 'q1')
        fake = Fake([prov('gemini'), prov('mistral'), prov('groq'), prov('openrouter')], fail=['mistral'])
        ctx = Ctx(self.tmp, fake)
        self.assertTrue(S.run(ctx))
        asked = [r['model'] for r in fake.requests]
        self.assertEqual(asked[:3].count('mistral-model'), 1)
        self.assertEqual(asked[3], 'openrouter-model')   # compared by a provider that was not asked
        self.assertIn('You compare answers from 2 different AIs', fake.requests[3]['text'])
        self.assertLessEqual(len(fake.requests[3]['text']), 3800)
        md = open(os.path.join(f, 'done', '20261105-q1.md')).read()
        self.assertIn('UNCHECKED: AI output, review before use', md)
        self.assertIn('failed: mistral', md)
        self.assertIn('compared by: openrouter', md)
        self.assertTrue(all(r['request_id'] and r['text'] for r in fake.requests))

    def test_dry_shadow_answer_is_failure(self):
        task(os.path.join(self.tmp, 'sp'), 'q1')
        fake = Fake([prov('gemini'), prov('groq')], answer='[dry] shadow ')
        S.run(Ctx(self.tmp, fake, fanout=2))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'sp', 'queue', 'failed', 'q1.json')))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'sp', 'done')))

    def test_dry_mode_makes_no_requests_and_writes_nothing(self):
        f = os.path.join(self.tmp, 'sp')
        task(f, 'q1')
        task(f, 'bad', guard='x')
        fake = Fake([prov('gemini')], commits=[{'sha': 'a' * 40, 'parents': [1]}])
        ctx = Ctx(self.tmp, fake, dry=True, repo='o/n')
        self.assertTrue(S.run(ctx))
        self.assertEqual(fake.requests, [])
        self.assertTrue(os.path.exists(os.path.join(f, 'queue', 'q1.json')))
        self.assertTrue(os.path.exists(os.path.join(f, 'queue', 'bad.json')))
        self.assertFalse(os.path.exists(os.path.join(f, 'reports')))
        self.assertTrue(any('q1' in l and 'aaaaaaa' in l for l in ctx.logs))

    def test_repo_review_skips_merges_and_seen(self):
        commits = [{'sha': 'c' * 40, 'parents': [1, 2]}, {'sha': 'b' * 40, 'parents': [1]}, {'sha': 'a' * 40, 'parents': [1]}]
        ctx = Ctx(self.tmp, None, repo='o/n')
        ctx.state.save('state', {'reviewed': ['b' * 40]})
        fake = Fake([prov('gemini'), prov('groq'), prov('mistral')], commits=commits)
        ctx.http = ihlib.http.Http(fake, sleep=lambda s: None)
        S.run(ctx)
        self.assertEqual(len(fake.requests), 3)   # 2 asked + 1 compare, only commit a
        self.assertIn('Commit aaaaaaa: Fix thing', fake.requests[0]['text'])
        self.assertIn('public open-source Home Assistant add-on', fake.requests[0]['text'])
        self.assertLessEqual(len(fake.requests[0]['text']), 3500)
        self.assertEqual(ctx.state.load('state')['reviewed'], ['b' * 40, 'a' * 40])
        S.run(ctx)   # second run: nothing new to review
        self.assertEqual(len(fake.requests), 3)

    def test_github_rate_limit_is_graceful(self):
        def op(req, timeout=0):
            if 'github' in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 403, 'rate', {}, None)
            return Fake([prov('gemini')])(req, timeout)
        ctx = Ctx(self.tmp, op, repo='o/n')
        self.assertTrue(S.run(ctx))
        self.assertTrue(any('rate limited' in l for l in ctx.logs))

    def test_empty_token_skips_and_token_not_logged(self):
        ctx = Ctx(self.tmp, Fake([prov('gemini')]))
        ctx.opts['ai_layer_token'] = ''
        self.assertTrue(S.run(ctx))
        self.assertTrue(any('skipped' in l for l in ctx.logs))
        f = os.path.join(self.tmp, 'sp')
        task(f, 'q1')
        ctx = Ctx(self.tmp, Fake([prov('gemini'), prov('groq')]), fanout=2)
        S.run(ctx)
        blob = '\n'.join(ctx.logs)
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn('heat pump', blob)
        self.assertNotIn('ans from', blob)

    def test_max_calls_stops_run(self):
        f = os.path.join(self.tmp, 'sp')
        for i in range(5):
            task(f, 't%d' % i)
        fake = Fake([prov('gemini'), prov('groq'), prov('mistral')])
        S.run(Ctx(self.tmp, fake, max_calls=4))
        self.assertLessEqual(len(fake.requests), 4)

    def _bl_fake(self):
        fake = Fake([prov('gemini'), prov('groq', cap=900), prov('mistral'), prov('openrouter'), prov('pollinations')])
        fake.tree = [('ih_a/config.yaml', 90), ('ih_a/DOCS.md', 90), ('ih_a/README.md', 90), ('ih_a/CHANGELOG.md', 90), ('ih_a/app/main.py', 5000), ('ih_a/app/b.py', 900),
                     ('ih_a/tests/t.py', 9000), ('ih_b/config.yaml', 90), ('ih_b/DOCS.md', 90), ('ih_b/app/main.py', 800)]
        fake.files = {'ih_a/app/main.py': 'def f():\n    ip = "' + IP + '"\n' + 'y = 2\n' * 900, 'ih_a/DOCS.md': 'docs a', 'ih_a/config.yaml': 'version: 1'}
        return fake

    def test_backlog_one_addon_per_night_rotates_and_no_repeat(self):
        fake = self._bl_fake()
        ctx = Ctx(self.tmp, fake, repo='o/n', backlog_share=100)
        ctx.http = ihlib.http.Http(fake, sleep=lambda s: None)
        S.run(ctx)
        text = ' '.join(r['text'] for r in fake.requests)
        self.assertIn('=== ih_a/app/main.py ===', text)
        self.assertNotIn('ih_b/', text)
        self.assertNotIn('tests/t.py', text)
        self.assertNotIn(IP, text)          # scrubbed
        self.assertTrue(all(len(r['text']) <= 3800 for r in fake.requests))
        self.assertTrue(all(r['model'].split('-')[0] in ('gemini', 'groq', 'mistral', 'openrouter', 'pollinations') for r in fake.requests))
        done = os.listdir(os.path.join(self.tmp, 'sp', 'done'))
        self.assertTrue(any('bl-docscheck-ih_a' in n for n in done) and any('bl-docsdraft-ih_a' in n for n in done) and any('bl-code-ih_a' in n for n in done))
        st = ctx.state.load('state')['backlog']
        self.assertEqual(st['ptr'], 'ih_a')
        n1 = len(fake.requests)
        S.run(ctx)                                      # night 2: next add-on
        text2 = ' '.join(r['text'] for r in fake.requests[n1:])
        self.assertIn('ih_b/', text2)
        self.assertNotIn('ih_a/', text2)
        n2 = len(fake.requests)
        S.run(ctx)                                      # night 3: both done within 14 days
        self.assertEqual(len(fake.requests), n2)
        ctx.now = NOW + datetime.timedelta(days=15)     # after 14 days it repeats
        S.run(ctx)
        self.assertGreater(len(fake.requests), n2)

    def test_backlog_off_share_cap_and_dry(self):
        fake = self._bl_fake()
        ctx = Ctx(self.tmp, fake, repo='o/n', backlog=False)
        ctx.http = ihlib.http.Http(fake, sleep=lambda s: None)
        S.run(ctx)
        self.assertEqual(fake.requests, [])
        fake = self._bl_fake()
        ctx = Ctx(self.tmp, fake, repo='o/n', max_calls=20, backlog_share=25)   # cap 5 calls
        ctx.http = ihlib.http.Http(fake, sleep=lambda s: None)
        S.run(ctx)
        self.assertLessEqual(len(fake.requests), 5)
        self.assertGreater(len(fake.requests), 0)
        fake = self._bl_fake()
        ctx = Ctx(self.tmp + '/d', fake, dry=True, repo='o/n')
        ctx.http = ihlib.http.Http(fake, sleep=lambda s: None)
        S.run(ctx)
        self.assertEqual(fake.requests, [])
        self.assertTrue(any('backlog: add-on ih_a' in l for l in ctx.logs))

    def test_scrub(self):
        t = S.scrub('ip ' + IP + ' mac ' + MACX + ' token = abcdef123 ok')
        self.assertNotIn(IP, t)
        self.assertNotIn(MACX, t)
        self.assertNotIn('abcdef123', t)


if __name__ == '__main__':
    unittest.main()
