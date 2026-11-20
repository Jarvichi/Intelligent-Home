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

TOKEN = 't' * 40
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 100
Q = 'fl=26&fr=32&rl=29&rr=20&tfl=32&tfr=32&trl=32&trr=32'


def serve(card):
    server = ThreadingHTTPServer(('127.0.0.1', 0), device_http.make_handler(None, None, TOKEN, card))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def get(server, path, token=TOKEN):
    request = urllib.request.Request('http://127.0.0.1:%d%s' % (server.server_port, path), headers={'Authorization': 'Bearer ' + token})
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b''


class CardRouteTests(unittest.TestCase):
    def test_card_needs_the_token_and_valid_numbers(self):
        seen = []
        server = serve(lambda name, values: seen.append((name, values)) or PNG)
        self.addCleanup(server.shutdown)
        self.assertEqual(get(server, '/device/v1/card/tyres?' + Q, 'x' * 40)[0], 401)
        self.assertEqual(get(server, '/device/v1/card/tyres?' + Q), (200, PNG))
        self.assertEqual(seen[0][0], 'tyres')
        self.assertEqual(seen[0][1]['rr'], 20.0)
        self.assertEqual(get(server, '/device/v1/card/tyres?fl=1')[0], 400)
        self.assertEqual(get(server, '/device/v1/card/tyres?' + Q.replace('fl=26', 'fl=abc'))[0], 400)

    def test_card_unavailable_and_off(self):
        server = serve(lambda name, values: None)
        self.addCleanup(server.shutdown)
        self.assertEqual(get(server, '/device/v1/card/tyres?' + Q)[0], 503)
        off = serve(None)
        self.addCleanup(off.shutdown)
        self.assertEqual(get(off, '/device/v1/card/tyres?' + Q)[0], 404)


if __name__ == '__main__': unittest.main()
