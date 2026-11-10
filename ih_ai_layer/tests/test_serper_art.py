"""serper provider game_art_search: fake opener, no real network."""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import urllib.request
from providers.serper import Serper, parse_images, SERPER_IMAGES_URL, SERPER_URL

DATA = {'images': [
    {'title': 'Cover', 'imageUrl': 'https://store.example/c.jpg', 'link': 'https://store.example/game', 'source': 'Store', 'imageWidth': 600, 'imageHeight': 900},
    {'title': 'bad', 'imageUrl': 'javascript:x', 'link': 'https://x.example'},
    'junk']}


class T(unittest.TestCase):
    def test_parse_images(self):
        rows = parse_images(DATA)
        self.assertEqual(rows, [{'title': 'Cover', 'image_url': 'https://store.example/c.jpg', 'source_url': 'https://store.example/game', 'source': 'Store', 'width': 600, 'height': 900}])
        self.assertEqual(parse_images(None), [])
        self.assertEqual(parse_images({}), [])

    def test_urls(self):
        self.assertTrue(SERPER_IMAGES_URL.endswith('/images'))
        self.assertTrue(SERPER_URL.endswith('/search'))

    def test_ask_art_uses_images_endpoint(self):
        seen = []

        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, n=-1): return json.dumps(DATA).encode()

        def opener(req, timeout=None):
            seen.append(req.full_url)
            return R()
        o = Serper.make_opts({'name': 'serper', 'enabled': True, 'key': 'k', 'settings': {'shadow': False}})
        p = Serper(o, tempfile.mkdtemp(), opener=opener)
        r = p.ask({'request_id': 'a', 'capability': 'game_art_search', 'text': 'Find cover for X'})
        self.assertTrue(r['ok'], r)
        self.assertEqual(seen, [SERPER_IMAGES_URL])
        self.assertEqual(json.loads(r['answer'])[0]['image_url'], 'https://store.example/c.jpg')

    def test_not_accepted_site_fails_over(self):
        def opener(req, timeout=None):
            class R:
                def __enter__(s): return s
                def __exit__(s, *a): return False
                def read(s, n=-1): return json.dumps(DATA).encode()
            return R()
        o = Serper.make_opts({'name': 'serper', 'enabled': True, 'key': 'k', 'settings': {'shadow': False}})
        p = Serper(o, tempfile.mkdtemp(), opener=opener)
        r = p.ask({'request_id': 'a', 'capability': 'game_art_search', 'text': 'X', 'context': {'title': 'X', 'domains': ['nowhere.example']}})
        self.assertFalse(r['ok'])
        self.assertEqual(r['why'], 'empty')


if __name__ == '__main__':
    unittest.main()
