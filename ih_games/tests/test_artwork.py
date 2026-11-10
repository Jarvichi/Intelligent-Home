"""artwork.find_and_store: fake AI Layer and image host, no network."""
import json, os, struct, sys, tempfile, unittest, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import artwork

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 20


def png(w, h):
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', w, h) + b'\x08\x06\x00\x00\x00'


def jpeg(w, h):
    return b'\xff\xd8\xff\xe0\x00\x04ab\xff\xc0\x00\x0b\x08' + struct.pack('>HH', h, w) + b'\x01\x01\x11\x00'


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

    def test_store_uploaded(self):
        d = tempfile.mkdtemp()
        meta = artwork.store_uploaded(d, self.game, PNG)
        self.assertEqual(meta['content_type'], 'image/png')
        self.assertTrue(os.path.exists(os.path.join(d, 'artwork', 'g1.png')))
        jpg = b'\xff\xd8\xff' + b'0' * 10
        artwork.store_uploaded(d, self.game, jpg)      # replaces the png
        self.assertFalse(os.path.exists(os.path.join(d, 'artwork', 'g1.png')))
        self.assertTrue(os.path.exists(os.path.join(d, 'artwork', 'g1.jpg')))

    def test_store_uploaded_rejects(self):
        d = tempfile.mkdtemp()
        for bad, text in ((b'', 'empty'), (b'GIF89a....', 'not a JPEG'), (b'\x89PNG\r\n\x1a\n' + b'0' * artwork.MAX_IMAGE_BYTES, 'larger')):
            with self.assertRaises(artwork.ArtError) as cm:
                artwork.store_uploaded(d, self.game, bad)
            self.assertIn(text, str(cm.exception))

    def test_image_size(self):
        self.assertEqual(artwork.image_size(png(600, 900)), (600, 900))
        self.assertEqual(artwork.image_size(jpeg(640, 480)), (640, 480))
        self.assertIsNone(artwork.image_size(b'junk'))

    def test_portrait_preferred_and_required(self):
        def run(images, require):
            def opener(req, timeout=None):
                if req.full_url.endswith('/request'):
                    rows = [{'image_url': 'https://img.example/%d.png' % i, 'source_url': 'https://store.steampowered.com/app/1/'} for i in range(len(images))]
                    return R(json.dumps({'ok': True, 'answer': json.dumps(rows)}).encode())
                return R(images[int(req.full_url.rsplit('/', 1)[1].split('.')[0])], 'image/png')
            return artwork.find_and_store(self.opts, tempfile.mkdtemp(), self.game, opener=opener, require_portrait=require)
        land, tall = png(1920, 1080), png(600, 900)
        self.assertEqual(run([land, tall], False)['content_type'], 'image/png')      # picks the portrait one
        with self.assertRaises(artwork.ArtError) as cm:
            run([land, land], True)
        self.assertIn('portrait', str(cm.exception))
        self.assertTrue(run([land], False))      # landscape accepted when nothing is saved yet

    def test_portrait_stage_then_any_shape(self):
        sent = []
        row = {'image_url': 'https://img.example/0.png', 'source_url': 'https://store.steampowered.com/app/1/'}

        def opener(req, timeout=None):
            if req.full_url.endswith('/request'):
                body = json.loads(req.data)
                sent.append(body['context']['portrait_only'])
                if body['context']['portrait_only']:
                    return R(json.dumps({'ok': False, 'answer': 'did not answer', 'error': 'empty'}).encode())
                return R(json.dumps({'ok': True, 'answer': json.dumps([row])}).encode())
            return R(png(1920, 1080), 'image/png')
        art = artwork.find_and_store(self.opts, tempfile.mkdtemp(), self.game, opener=opener)
        self.assertTrue(art)
        self.assertEqual(sent, [True, False])
        sent.clear()
        with self.assertRaises(artwork.ArtError):
            artwork.find_and_store(self.opts, tempfile.mkdtemp(), self.game, opener=opener, require_portrait=True)
        self.assertEqual(sent, [True])      # a refresh never relaxes the portrait rule

    def test_layer_down_is_not_retried(self):
        calls = []

        def opener(req, timeout=None):
            calls.append(1)
            raise urllib.error.URLError('down')
        with self.assertRaises(artwork.LayerDown):
            artwork.find_and_store(self.opts, tempfile.mkdtemp(), self.game, opener=opener)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
