import io, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import local_sort


class FakeOllama:
    """Stands in for ih_ollama: returns a canned /api/generate reply, or fails."""
    def __init__(self, response=None, fail=None):
        self.response, self.fail, self.requests = response, fail, []

    def __call__(self, req, timeout=0):
        self.requests.append((req.full_url, json.loads(req.data)))
        if self.fail:
            raise self.fail
        body = json.dumps({"response": self.response}).encode()
        return _Resp(body)


class _Resp:
    def __init__(self, b): self.b = b
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def make_brain():
    d = tempfile.mkdtemp()
    for rel in ('lifestyle/cars/a.md', 'lifestyle/food/b.md', 'technology/network/c.md', 'inbox/x.md', 'attachments/y/z.md', 'people/family/rob/p.md'):
        p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, 'w').write('x')
    return d


class LocalSort(unittest.TestCase):
    def setUp(self):
        self.brain, self.data = make_brain(), tempfile.mkdtemp()

    def shadow(self, fake, model='m'):
        return local_sort.Shadow(self.brain, self.data, 'http://ollama:11434', model, opener=fake)

    def test_areas_skip_inbox_and_attachments_and_limit_depth(self):
        a = local_sort.areas(self.brain)
        self.assertIn('lifestyle/cars', a); self.assertIn('technology/network', a)
        self.assertFalse([x for x in a if x.startswith(('inbox', 'attachments'))])
        self.assertNotIn('people/family/rob', a)   # depth 2 only

    def test_parse_plain_fenced_and_bad(self):
        a = ['lifestyle/cars']
        self.assertEqual(local_sort.parse_suggestion('{"folder": "lifestyle/cars", "title": "T-Roc  service"}', a),
                         {'folder': 'lifestyle/cars', 'title': 'T-Roc service', 'known': True})
        s = local_sort.parse_suggestion('```json\n{"folder": "brain/other/place.md", "title": "x"}\n```', a)
        self.assertEqual(s['folder'], 'other/place'); self.assertFalse(s['known'])
        for bad in ('', 'no json', '{"folder": ""}', '[1]', '{broken'):
            self.assertIsNone(local_sort.parse_suggestion(bad, a))

    def test_suggest_logs_and_sends_only_to_ollama(self):
        fake = FakeOllama('{"folder": "lifestyle/cars", "title": "Service"}')
        r = self.shadow(fake).suggest('n1', 'T-Roc MOT due in May')
        self.assertTrue(r['ok']); self.assertEqual(r['suggestion']['folder'], 'lifestyle/cars')
        self.assertEqual(fake.requests[0][0], 'http://ollama:11434/api/generate')
        self.assertIn('T-Roc MOT', fake.requests[0][1]['prompt']); self.assertIn('lifestyle/cars', fake.requests[0][1]['prompt'])
        rows = [json.loads(x) for x in open(os.path.join(self.data, 'local_sort.jsonl'))]
        self.assertEqual(rows[0]['kind'], 'suggest'); self.assertEqual(rows[0]['folder'], 'lifestyle/cars')

    def test_agreement_count(self):
        fake = FakeOllama('{"folder": "lifestyle/cars", "title": "t"}')
        s = self.shadow(fake)
        s.suggest('n1', 'a'); s.suggest('n2', 'b'); s.suggest('n3', 'c')
        s.filed('n1', 'brain/lifestyle/cars/mot.md')        # agrees
        s.filed('n2', 'lifestyle/food/recipe.md')           # differs
        s.filed('n4', 'technology/x.md')                    # never suggested: not counted
        sm = s.summary()
        self.assertEqual((sm['compared'], sm['agreed'], sm['text']), (2, 1, 'agreed 1 of 2'))
        self.assertEqual(sm['same_area'], 2)   # cars and food are both under lifestyle
        self.assertEqual(sm['suggested'], 3)

    def test_same_area_counts_top_level(self):
        s = self.shadow(FakeOllama('{"folder": "lifestyle/cars", "title": "t"}'))
        s.suggest('n1', 'a'); s.filed('n1', 'lifestyle/food/r.md')
        sm = s.summary()
        self.assertEqual((sm['agreed'], sm['same_area']), (0, 1))

    def test_ollama_down_falls_back_without_error(self):
        s = self.shadow(FakeOllama(fail=OSError('refused')))
        r = s.suggest('n1', 'text')
        self.assertFalse(r['ok']); self.assertIn('unavailable', r['why'])
        s.filed('n1', 'lifestyle/cars/a.md')
        sm = s.summary()
        self.assertEqual((sm['compared'], sm['failed'], sm['suggested']), (0, 1, 1))

    def test_off_when_no_model_and_no_log(self):
        fake = FakeOllama('{"folder": "x"}')
        r = self.shadow(fake, model='').suggest('n1', 'text')
        self.assertFalse(r['ok']); self.assertEqual(fake.requests, [])
        self.assertFalse(os.path.exists(os.path.join(self.data, 'local_sort.jsonl')))

    def test_filed_rejects_bad_paths_and_nothing_written_to_brain(self):
        before = sorted(os.walk(self.brain))
        s = self.shadow(FakeOllama('{"folder": "lifestyle/cars", "title": "t"}'))
        s.suggest('n1', 'a')
        self.assertFalse(s.filed('n1', '../etc/passwd')['ok']); self.assertFalse(s.filed('', 'a/b.md')['ok'])
        self.assertEqual(before, sorted(os.walk(self.brain)))

    def test_log_is_trimmed(self):
        s = self.shadow(FakeOllama('{"folder": "a"}'))
        old = local_sort.MAX_LOG_BYTES; local_sort.MAX_LOG_BYTES = 500
        try:
            for i in range(30): s.filed('n%d' % i, 'a/b.md')
        finally:
            local_sort.MAX_LOG_BYTES = old
        self.assertLess(len(open(s.path).readlines()), 30)


if __name__ == '__main__':
    unittest.main()
