"""AI Trust Test: every ready AI gets the same questions, forced by model. Fakes only, no network, no clock waits."""
import io, json, os, sys, tempfile, unittest, urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import trustjob, today

SECRET = 'sk-SECRET-TOKEN-123'


def prov(name, model, caps=('general_qa',), **kw):
    d = {'provider': name, 'model': model, 'shadow': False, 'kill_switch': False, 'configured': True, 'free': True, 'capabilities': list(caps)}
    d.update(kw)
    return d


STATUS = {'kill_switch': False, 'providers': [
    prov('groq', 'openai/gpt-oss-120b'), prov('gemini', 'gemini-3.5-flash'), prov('openai', 'gpt-x'), prov('mistral', 'mistral-medium-latest'),
    prov('serper', '', caps=('web_search',)), prov('mcp', 'm', caps=('general_qa',)), prov('grok', 'grok-1', shadow=True),
    prov('openrouter', 'openrouter/free', kill_switch=True), prov('pollinations', 'openai', configured=False)]}


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Ctx:
    def __init__(self, handler, opts=None, data=None, dry=True):
        self.opts = dict({'ai_layer_url': 'http://layer', 'ai_layer_token': SECRET, 'pause_s': '0', 'retry_wait_s': '0'}, **(opts or {}))
        self.handler, self.data_dir, self.dry, self.logs, self.calls, self.todos = handler, data or tempfile.mkdtemp(), dry, [], [], []
    def log(self, m): self.logs.append(m)
    def ha(self, path, body): self.todos.append((path, body))
    def open(self, req, timeout=0):
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.full_url, body, req.get_header('Authorization')))
        out = self.handler(req.full_url, body)
        if isinstance(out, Exception):
            raise out
        return Resp(json.dumps(out).encode())


def http(code):
    return urllib.error.HTTPError('u', code, 'x', {}, None)


GOOD = {'a': 'W, atomic number 74', 'b': 'ANSWER: 295', 'g': 'ANSWER: 6', 'd': 'I am not sure it exists.', 'f': 'That is not true, it was not removed.',
        'e': '- one\n- two\n- three', 'c': '', 'h': 'Bees dance to share food. The dance shows direction and distance.'}


def by_q(text):
    for q, t in trustjob.QUESTIONS.items():
        if t == text:
            return q


def layer(status=STATUS, answer_agent=None, fail=None):
    def h(url, body):
        if url.endswith('/status'):
            return status
        if fail:
            r = fail(body)
            if r is not None:
                return r
        model = body.get('model')
        owner = next(p['provider'] for p in status['providers'] if p['model'] == model)
        return {'ok': True, 'answer': GOOD[by_q(body['text'])] or 'x', 'agent': 'ih_ai_layer_' + (answer_agent(owner) if answer_agent else owner), 'took_ms': 5}
    return h


NOW = datetime(2026, 11, 1, 3, 30, tzinfo=timezone.utc)


def run(ctx):
    return trustjob.run(ctx, now=NOW, sleep=lambda s: None)


class Discover(unittest.TestCase):
    def test_default_eligible_and_exclusions(self):
        provs, why = trustjob.discover(Ctx(layer()))
        self.assertEqual([p['provider'] for p in provs], ['gemini', 'groq', 'mistral'])   # no openai (default exclude), search, mcp, shadow, killed, unconfigured
        self.assertEqual({p['provider']: p['model'] for p in provs}['groq'], 'openai/gpt-oss-120b')
        self.assertEqual(why, '')

    def test_status_uses_bearer(self):
        c = Ctx(layer()); trustjob.discover(c)
        self.assertEqual(c.calls[0][0], 'http://layer/status')
        self.assertEqual(c.calls[0][2], 'Bearer ' + SECRET)

    def test_exclude_none_allows_openai(self):
        provs, _ = trustjob.discover(Ctx(layer(), {'exclude': 'none'}))
        self.assertIn('openai', [p['provider'] for p in provs])

    def test_explicit_providers_override_and_allow_openai(self):
        provs, _ = trustjob.discover(Ctx(layer(), {'providers': 'ih_ai_layer_groq, openai'}))
        self.assertEqual([p['provider'] for p in provs], ['groq', 'openai'])

    def test_search_never_even_if_listed(self):
        provs, _ = trustjob.discover(Ctx(layer(), {'providers': 'serper, mcp, groq'}))
        self.assertEqual([p['provider'] for p in provs], ['groq'])

    def test_status_errors(self):
        self.assertEqual(trustjob.discover(Ctx(lambda u, b: http(401)))[0], [])
        self.assertIn('401', trustjob.discover(Ctx(lambda u, b: http(401)))[1])
        self.assertEqual(trustjob.discover(Ctx(lambda u, b: dict(STATUS, kill_switch=True)))[0], [])


class Ask(unittest.TestCase):
    def test_model_forced_in_body(self):
        c = Ctx(layer()); trustjob.ask(c, 'a', trustjob.QUESTIONS['a'], lambda s: None, 'gemini-3.5-flash')
        self.assertEqual(c.calls[0][1]['model'], 'gemini-3.5-flash')
        self.assertEqual(c.calls[0][1]['capability'], 'general_qa')

    def test_429_retry_once(self):
        n = {'n': 0}
        def h(u, b):
            n['n'] += 1
            return http(429) if n['n'] == 1 else {'ok': True, 'answer': 'x', 'agent': 'ih_ai_layer_groq', 'took_ms': 1}
        waits = []
        r = trustjob.ask(Ctx(h), 'a', 't', waits.append, 'm')
        self.assertTrue(r[0]); self.assertEqual(n['n'], 2); self.assertEqual(len(waits), 1)

    def test_429_twice_gives_up(self):
        r = trustjob.ask(Ctx(lambda u, b: http(429)), 'a', 't', lambda s: None, 'm')
        self.assertEqual((r[0], r[4], r[5]), (False, False, 429))


class Run(unittest.TestCase):
    has_yaml = trustjob.score_c('x')[0] is not None   # question c needs PyYAML; without it c is left unscored
    def test_every_ai_gets_every_question_forced(self):
        c = Ctx(layer()); self.assertTrue(run(c))
        reqs = [b for u, b, a in c.calls if u.endswith('/request')]
        self.assertEqual(len(reqs), 3 * 8)
        for p in ('openai/gpt-oss-120b', 'gemini-3.5-flash', 'mistral-medium-latest'):
            self.assertEqual(sum(1 for b in reqs if b['model'] == p), 8)
        self.assertFalse(any(b['model'] == 'gpt-x' for b in reqs))
        d = json.load(open(os.path.join(c.data_dir, 'trust_runs', '2026-11.json')))
        self.assertEqual(d['format'], 2)
        self.assertEqual(sorted(d['results']), ['ih_ai_layer_gemini', 'ih_ai_layer_groq', 'ih_ai_layer_mistral'])
        self.assertEqual(set(d['results']['ih_ai_layer_groq']), set('abcdefgh'))
        self.assertEqual(d['summary']['agents']['ih_ai_layer_groq']['scored'], 7 + (1 if self.has_yaml else 0))

    def test_misroute_not_scored(self):
        c = Ctx(layer(answer_agent=lambda o: 'groq' if o == 'gemini' else o)); run(c)
        d = json.load(open(os.path.join(c.data_dir, 'trust_runs', '2026-11.json')))
        g = d['results']['ih_ai_layer_gemini']
        self.assertTrue(all(r.get('misrouted') and r['score'] is None and not r['ok'] for r in g.values()))
        self.assertEqual(d['summary']['agents']['ih_ai_layer_gemini']['test'], None)
        self.assertEqual(d['summary']['agents']['ih_ai_layer_groq']['scored'], 7 + (1 if self.has_yaml else 0))

    def test_repeated_429_stops_with_partial(self):
        c = Ctx(layer(fail=lambda b: http(429) if b['model'] == 'mistral-medium-latest' else None))
        self.assertFalse(run(c))
        d = json.load(open(os.path.join(c.data_dir, 'trust_runs', '2026-11.json')))
        self.assertTrue(d['summary']['stopped_early'])
        self.assertEqual(len(d['results']['ih_ai_layer_gemini']), 8)
        self.assertEqual(len(d['results']['ih_ai_layer_mistral']), 3)
        self.assertFalse(os.path.exists(os.path.join(c.data_dir, 'trust_state.json')))

    def test_404_skips_that_ai(self):
        c = Ctx(layer(fail=lambda b: http(404) if b['model'] == 'gemini-3.5-flash' else None)); self.assertTrue(run(c))
        d = json.load(open(os.path.join(c.data_dir, 'trust_runs', '2026-11.json')))
        self.assertEqual(d['summary']['skipped'], ['gemini'])
        self.assertEqual(len(d['results']['ih_ai_layer_gemini']), 1)

    def test_401_halts(self):
        c = Ctx(layer(fail=lambda b: http(401))); self.assertFalse(run(c))

    def test_no_text_in_logs_and_todo_lists_scores(self):
        c = Ctx(layer(), dry=False); run(c)
        blob = '\n'.join(c.logs)
        for t in list(trustjob.QUESTIONS.values()) + list(GOOD.values()) + [SECRET]:
            if t:
                self.assertNotIn(t, blob)
        self.assertEqual(len(c.todos), 1)
        self.assertIn('Gemini', c.todos[0][1]['description'])

    def test_keep_answers_off_by_default(self):
        c = Ctx(layer()); run(c)
        self.assertNotIn('answer', open(os.path.join(c.data_dir, 'trust_runs', '2026-11.json')).read().replace('answered', ''))

    def test_no_eligible_ai(self):
        c = Ctx(lambda u, b: {'providers': []}); self.assertFalse(run(c))


class OldFiles(unittest.TestCase):
    OLD = {q: {'ok': True, 'agent': 'ih_ai_layer_groq', 'chars': 3, 'took_ms': 1, 'score': 10, 'note': 'x'} for q in 'abcdefgh'}

    def test_summarise_all_old(self):
        s = trustjob.summarise_all(self.OLD)
        self.assertEqual(s['agents']['ih_ai_layer_groq']['test'], 100)
        self.assertEqual(s['test'], 100)
        self.assertTrue(trustjob.is_old_shape(self.OLD))

    def test_summarise_unchanged_for_single_agent(self):
        self.assertEqual(trustjob.summarise(self.OLD)['test'], 100)

    def test_today_old_and_new(self):
        old = {'month': '2026-10', 'finished': '2026-10-01T03:05:00+00:00', 'results': self.OLD, 'summary': {'test': 100}}
        new = {'month': '2026-11', 'finished': '2026-11-01T03:05:00+00:00', 'summary': {},
               'results': {'ih_ai_layer_groq': {q: dict(r, score=5) for q, r in self.OLD.items()},
                           'ih_ai_layer_gemini': dict(self.OLD, a=dict(self.OLD['a'], score=0)),
                           'ih_ai_layer_mistral': {'a': {'ok': False, 'agent': 'x', 'misrouted': True, 'score': None}}}}
        now = datetime(2026, 11, 5, tzinfo=timezone.utc)
        o = today.build(old, {}, now)
        self.assertEqual([(r['name'], r['score']) for r in o['rows']], [('Groq', 100)])
        n = today.build(new, {}, now)
        self.assertEqual([(r['name'], r.get('score')) for r in n['rows']], [('Gemini', 93), ('Groq', 50), ('Mistral', None)])
        self.assertEqual(n['rows'][0]['note'], 'Marks for 8 of 8 questions')
        self.assertIn('Next test: 1 December.', n['footnote'])
        self.assertIn('1 November', n['subtitle'])


if __name__ == '__main__':
    unittest.main()
