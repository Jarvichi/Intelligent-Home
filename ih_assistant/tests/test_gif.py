import io, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
from main import Assistant


class FakeDiscord:
    def __init__(self): self.sent = []
    def send(self, ch, text, rt): self.sent.append(text); return True


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class GifTests(unittest.TestCase):
    def make(self, level=10, key='k', data=None):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory(); self.now = [100000.0]
        a.opts = {'giphy_api_key': key}; a.log = lambda *x: None; a.clock = lambda: self.now[0]; a.rng = lambda: 0.0
        a.cfg = {'pp': {'user_id': '1', 'levels': {'gif_level': level}}}
        a.stores = {'pp': PersonaStore(self.tmp.name, 'pp', a.clock)}
        a.discord = {'pp': FakeDiscord()}; a._channel = lambda k: 'ch'
        body = json.dumps({'data': data if data is not None else [{'url': 'https://giphy.com/gifs/a'}]}).encode()
        self.urls = []
        a.gif_opener = lambda url, timeout=0: (self.urls.append(url), Resp(body))[1]
        return a

    def test_sends_one_then_blocks_for_six_hours(self):
        a = self.make()
        self.assertEqual(a.send_gif('pp', 'well done'), 'sent')
        self.assertEqual(a.discord['pp'].sent, ['https://giphy.com/gifs/a'])
        self.assertIn('rating=pg', self.urls[0])
        self.assertEqual(a.send_gif('pp', 'again'), 'skipped: one per chat')
        self.now[0] += 6 * 3600 + 1
        self.assertEqual(a.send_gif('pp', 'later'), 'sent')

    def test_level_zero_no_key_and_nothing_found(self):
        self.assertEqual(self.make(level=0).send_gif('pp', 'x'), 'skipped: level')
        self.assertEqual(self.make(key='').send_gif('pp', 'x'), 'no key')
        self.assertEqual(self.make(data=[]).send_gif('pp', 'x'), 'none found')
        self.assertEqual(self.make().send_gif('pp', '   '), 'no words')

    def test_bad_link_is_not_sent_and_errors_never_raise(self):
        a = self.make(data=[{'url': 'http://evil.example/x'}])
        self.assertEqual(a.send_gif('pp', 'x'), 'none found')
        a.gif_opener = lambda *x, **y: (_ for _ in ()).throw(OSError('down'))
        self.assertEqual(a.send_gif('pp', 'x'), 'failed')


if __name__ == '__main__':
    unittest.main()
