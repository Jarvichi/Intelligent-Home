import importlib.util
import io
import pathlib
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import device_http
import pressed

HAVE_PIL = importlib.util.find_spec('PIL') is not None
TOKEN = 't' * 40
FRAME, ZONE = 'a' * 32, 'b' * 32


def make_png():
    from PIL import Image
    image = Image.new('L', (200, 100), 255)
    for x in range(20, 60):
        for y in range(10, 30):
            image.putpixel((x, y), 0)
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


class FakeFrames:
    def __init__(self, image): self.image = image
    def bundle(self, key):
        return {'frame': key, 'image': self.image, 'zones': [{'id': ZONE, 'box': [10, 5, 110, 45], 'power': 'mains', 'preview': 'none'}]} if key == FRAME else None


@unittest.skipUnless(HAVE_PIL, 'Pillow is installed in the add-on image, not on every test machine')
class PressedTests(unittest.TestCase):
    def test_inverts_only_the_zone(self):
        from PIL import Image
        out = pressed.invert_crop(make_png(), [10, 5, 110, 45])
        with Image.open(io.BytesIO(out)) as image:
            self.assertEqual(image.size, (100, 40))
            self.assertEqual(image.getpixel((25, 15)), 255)   # the middle of the black square at x20-60,y10-30: fill gone (white)
            self.assertEqual(image.getpixel((11, 6)), 0)      # its edge (x21,y11): outline stays black
            self.assertEqual(image.getpixel((1, 1)), 255)     # the page around the button is left alone

    def test_bad_boxes_give_none(self):
        self.assertIsNone(pressed.invert_crop(make_png(), [10, 5, 10, 45]))
        self.assertIsNone(pressed.invert_crop(b'not a png', [0, 0, 50, 50]))

    def test_route(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), device_http.make_handler(FakeFrames(make_png()), None, TOKEN))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        def get(path, token=TOKEN):
            request = urllib.request.Request('http://127.0.0.1:%d%s' % (server.server_port, path), headers={'Authorization': 'Bearer ' + token})
            try:
                with urllib.request.urlopen(request, timeout=5) as r: return r.status, r.read()
            except urllib.error.HTTPError as e: return e.code, b''
        ok = get('/device/v1/pressed/%s/%s' % (FRAME, ZONE))
        self.assertEqual(ok[0], 200); self.assertTrue(ok[1].startswith(b'\x89PNG'))
        self.assertEqual(get('/device/v1/pressed/%s/%s' % (FRAME, ZONE), 'x' * 40)[0], 401)
        self.assertEqual(get('/device/v1/pressed/%s/%s' % (FRAME, 'c' * 32))[0], 404)
        self.assertEqual(get('/device/v1/pressed/%s/%s' % ('d' * 32, ZONE))[0], 404)


if __name__ == '__main__': unittest.main()
