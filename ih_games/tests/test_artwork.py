"""artwork.find_and_store: fake AI Layer and image host, no network."""
import json, os, sys, tempfile, unittest, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import artwork

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 20


class R:
    def __init__(self, body, ctype='application/json'):
        self.body, self.headers = body, {'Content-Type': ctype}
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n=-1): return self.body
    def geturl(self): return 'https://img.example/x'


def make_opener(layer_reply, sent):
    def opener(req, timeout=None):
        if req.full_url.endswith('/request'):
            sent.append(json.loads(req.data))
            return R(json.dumps(layer_reply).encode())
        if 'bad' in req.full_url:
            raise urllib.error.URLError('nope')
        return R(PNG, 'image/png')
    return opener


class T(unittest.TestCase):
    def setUp(self):
        artwork._public_https = lambda u: True
        self.opts = {'ai_layer_url': 'http://layer', 'ai_layer_art_token': 't'}
        self.game = {'id': 'g1', 'title': 'Doom', 'platform': 'xbox'}

    def run_it(self, reply, sent=None):
        return artwork.find_and_store(self.opts, tempfile.mkdtemp(), self.game, opener=make_opener(reply, sent if sent is not None else []))

    def test_sends_title_and_platform_and_tries_next_image(self):
        rows = [{'image_url': 'https://img.example/bad.jpg', 'source_url': 'https://store.steampowered.com/app/1/'},
                {'image_url': 'https://img.example/ok.png', 'source_url': 'https://store.steampowered.com/app/1/'}]
        sent = []
        art = self.run_it({'ok': True, 'answer': json.dumps(rows)}, sent)
        self.assertEqual(sent[0]['text'], 'Doom')
        self.assertEqual(sent[0]['context']['title'], 'Doom')
        self.assertEqual(sent[0]['context']['platform'], 'xbox')
        self.assertIn('store.steampowered.com', sent[0]['context']['domains'])
        self.assertTrue(art)

    def test_reasons(self):
        cases = {'cap': 'daily limit', 'not_configured': 'no key', 'upstream_error': 'did not answer'}
        for why, text in cases.items():
            with self.assertRaises(artwork.ArtError) as cm:
                self.run_it({'ok': False, 'answer': "The agents for 'game_art_search' did not answer (2 tried).", 'error': why})
            self.assertIn(text, str(cm.exception))


if __name__ == '__main__':
    unittest.main()
