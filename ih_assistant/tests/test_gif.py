import io, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
from main import Assistant

GIF = b'GIF89a' + b'\x00' * 20


class FakeDiscord:
    def __init__(self): self.files = []
    def send_file(self, ch, path, name='x.gif'): self.files.append(path); return True


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class GifTests(unittest.TestCase):
    def make(self, level=10, key='k', data='default', rolls=None):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory(); self.now = [100000.0]
        a.data_dir = self.tmp.name
        a.opts = {'giphy_api_key': key, 'gif_gap_minutes': 10}; a.log = lambda *x: None; a.clock = lambda: self.now[0]
        self.rolls = list(rolls or [])
        a.rng = lambda: self.rolls.pop(0) if self.rolls else 0.9   # default: no reuse, pick the last candidate
        a.cfg = {'pp': {'user_id': '1', 'levels': {'gif_level': level}}}
        a.stores = {'pp': PersonaStore(self.tmp.name, 'pp', a.clock)}
        a.discord = {'pp': FakeDiscord()}; a._channel = lambda k: 'ch'
        if data == 'default':
            data = [{'id': 'abc123', 'images': {'downsized': {'url': 'https://media.giphy.com/media/abc123/x.gif'}}}]
        self.urls = []
        def op(url, timeout=0):
            self.urls.append(url)
            return Resp(json.dumps({'data': data}).encode() if 'api.giphy.com' in url else GIF)
        a.gif_opener = op
        return a

    def test_new_gif_is_attached_cached_and_gap_applies(self):
        a = self.make()
        self.assertEqual(a.send_gif('pp', 'well done'), 'sent (new)')
        self.assertTrue(a.discord['pp'].files[0].endswith('abc123.gif'))
        self.assertTrue(os.path.exists(a.discord['pp'].files[0]))
        self.assertIn('rating=pg', self.urls[0])
        self.assertEqual(a.send_gif('pp', 'well done'), 'skipped: too soon')
        self.now[0] += 601
        self.rolls = [0.0, 0.0, 0.0]   # level roll passes; reuse roll 0.0 < 0.5 -> reuse
        n = len(self.urls)
        self.assertEqual(a.send_gif('pp', 'well done'), 'sent (reused)')
        self.assertEqual(len(self.urls), n)   # no extra GIPHY call

    def test_reuse_needs_a_fitting_picture(self):
        a = self.make()
        a.send_gif('pp', 'well done'); self.now[0] += 601
        self.rolls = [0.0, 0.0]
        a.opts['giphy_api_key'] = ''
        self.assertEqual(a.send_gif('pp', 'monday morning coffee'), 'no key')   # nothing fits, no key

    def test_asked_gif_ignores_level_roll_and_gap(self):
        a = self.make(level=0)
        self.assertEqual(a.send_gif('pp', 'celebrate', asked=True), 'sent (new)')
        self.assertEqual(a.send_gif('pp', 'celebrate more', asked=True), 'sent (new)')

    def test_gates_and_failures(self):
        self.assertEqual(self.make(level=0).send_gif('pp', 'celebrate'), 'skipped: level')
        self.assertEqual(self.make(key='').send_gif('pp', 'celebrate'), 'no key')
        self.assertEqual(self.make(data=[]).send_gif('pp', 'celebrate'), 'none found')
        self.assertEqual(self.make().send_gif('pp', ' a '), 'no words')
        bad = self.make(data=[{'id': 'x1y2z3', 'images': {'downsized': {'url': 'http://evil.example/x.gif'}}}])
        self.assertEqual(bad.send_gif('pp', 'celebrate'), 'none found')

    def test_giphy_down_falls_back_to_cache_and_errors_never_raise(self):
        a = self.make()
        a.send_gif('pp', 'celebrate party'); self.now[0] += 601
        a.gif_opener = lambda *x, **y: (_ for _ in ()).throw(OSError('down'))
        self.rolls = [0.0, 0.9]   # level passes, no reuse roll -> try GIPHY -> down -> cached one
        self.assertEqual(a.send_gif('pp', 'celebrate party'), 'sent (reused)')


if __name__ == '__main__':
    unittest.main()
