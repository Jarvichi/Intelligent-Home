"""Pocket sync history, GET /api/pocket shape, and no secret in the payload. Synthetic data only."""
import json, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import brainlib, extras, ui_server

TOKEN, KEY, URL = 'tok-SECRET-111', 'key-SECRET-222', 'https://pocket.example.test'


def make(tmp, http, live=True):
    return extras.Pocket(tmp + '/notes', tmp + '/data', TOKEN, KEY, URL, live=live, http=http, log=lambda *_: None)


def failing_http(m, u, h, b=None):
    raise RuntimeError('boom %s %s %s' % (TOKEN, KEY, URL + '/inbox'))


def ok_http(m, u, h, b=None):
    return {'items': []}


class History(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(); self.addCleanup(self.t.cleanup)
        Path(self.t.name, 'notes').mkdir()

    def test_capped_at_20(self):
        p = make(self.t.name, ok_http, live=False)
        for _ in range(25):
            p.run_once()
        self.assertEqual(len(p.status()['history']), 20)

    def test_failing_after_three_errors_only(self):
        p = make(self.t.name, failing_http)
        p.run_once(); p.run_once()
        self.assertFalse(p.status()['failing'])
        p.run_once()
        self.assertTrue(p.status()['failing'])
        p.http = ok_http; p.live = False; p.run_once()
        self.assertFalse(p.status()['failing'])

    def test_no_secret_in_payload(self):
        p = make(self.t.name, failing_http)
        p.run_once()
        text = json.dumps(p.status())
        for secret in (TOKEN, KEY, URL, 'pocket.example'):
            self.assertNotIn(secret, text)
        self.assertEqual(p.status()['last']['ok'], False)

    def test_scrub_replaces_secrets_and_urls(self):
        p = make(self.t.name, ok_http)
        out = p._scrub('bad %s at %s and http://x.test/y' % (KEY, URL))
        self.assertNotIn(KEY, out); self.assertNotIn('http', out)


class Api(unittest.TestCase):
    def serve(self, pocket):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        brain = brainlib.Brain(Path(t.name) / 'notes', Path(t.name) / 'data')
        srv = ThreadingHTTPServer(('127.0.0.1', 0), ui_server.make_handler(brain, pocket=pocket))
        th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close(), th.join()))
        pt = patch.object(ui_server, 'GATEWAY_PEER', '127.0.0.1'); pt.start(); self.addCleanup(pt.stop)
        return srv

    def get(self, srv, path='api/pocket'):
        with urllib.request.urlopen('http://127.0.0.1:%d/%s' % (srv.server_port, path)) as r:
            return json.loads(r.read())

    def test_not_configured(self):
        d = self.get(self.serve(None))
        self.assertEqual((d['configured'], d['history'], d['failing']), (False, [], False))

    def test_shape(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        Path(t.name, 'notes').mkdir()
        p = make(t.name, ok_http, live=False); p.run_once()
        d = self.get(self.serve(p))
        self.assertEqual(set(d), {'configured', 'live', 'history', 'last', 'failing'})
        self.assertEqual(set(d['last']), {'at', 'inbox', 'selected', 'changed', 'removed', 'refused', 'live', 'ok', 'error'})

    def test_blocked_without_ingress(self):
        srv = self.serve(None)
        with patch.object(ui_server, 'GATEWAY_PEER', '172.30.32.2'):
            with self.assertRaises(urllib.error.HTTPError) as c:
                self.get(srv)
        self.assertEqual(c.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
