"""Template ingress server: starts, serves the page, health ok, parallel requests do not block.
Run: python3 -m unittest discover -s ih_ui/tests"""
import json, os, sys, threading, time, unittest, urllib.request
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / 'template'
sys.path.insert(0, str(TEMPLATE))

os.environ['IH_UI_PEER'] = ''
import urllib.error
import ui_server


def fetch(port, path):
    with urllib.request.urlopen('http://127.0.0.1:%d%s' % (port, path), timeout=5) as r:
        return r.status, r.read()


class TemplateServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ui_server.make_server(0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def test_page_and_assets(self):
        code, body = fetch(self.port, '/')
        self.assertEqual(code, 200)
        self.assertIn(b'<ih-nav', body)
        for p in ('/ui.js', '/ui.css', '/ih-ui.js', '/ih-ui.css', '/ui.json'):
            self.assertEqual(fetch(self.port, p)[0], 200, p)

    def test_healthz(self):
        self.assertEqual(fetch(self.port, '/healthz'), (200, b'ok'))

    def test_status_is_json(self):
        code, body = fetch(self.port, '/api/status')
        self.assertEqual(code, 200)
        self.assertIn('healthy', json.loads(body))

    def test_unknown_path_404(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            fetch(self.port, '/nope')
        self.assertEqual(c.exception.code, 404)

    def test_parallel_requests_do_not_block(self):
        # a slow client holds a connection open; /healthz must still answer
        import socket
        slow = socket.create_connection(('127.0.0.1', self.port))
        slow.sendall(b'GET / HTTP/1.1\r\nHost: x\r\n')   # incomplete request, never finished
        try:
            t0 = time.time()
            self.assertEqual(fetch(self.port, '/healthz')[0], 200)
            self.assertLess(time.time() - t0, 2)
        finally:
            slow.close()


if __name__ == '__main__':
    unittest.main()
