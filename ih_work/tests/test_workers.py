"""Live worker status (0.4.0): validation, stalled/drop times, persistence, idle, and the HTTP endpoints (token, unknown fields)."""
import json, os, sys, tempfile, threading, unittest, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
sys.path.insert(0, os.path.dirname(__file__))
import main as M
from store import StoreError
from workers import Workers
from test_service import Base, TOKEN, DAY

T0 = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


class Clk:
    def __init__(self): self.t = T0
    def __call__(self): return self.t
    def move(self, **k): self.t += timedelta(**k)


MSG = dict(worker='queue', title='Do the thing', ref='r1', started_at='2026-11-13T11:55:00+00:00', stage='assessing', detail='Read a/b.md')


class Unit(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.c = Clk(); self.w = Workers(self.d, self.c)

    def test_update_and_view(self):
        self.w.update(MSG); v = self.w.view()['workers']
        self.assertEqual(len(v), 1); x = v[0]
        self.assertEqual((x['worker'], x['title'], x['stage'], x['detail'], x['stalled']), ('queue', 'Do the thing', 'assessing', 'Read a/b.md', False))
        self.assertEqual(x['elapsed_s'], 300)

    def test_bad_input(self):
        for bad in (dict(MSG, worker='Bad Name'), dict(MSG, worker=5), dict(MSG, stage='dancing'), dict(MSG, stage=None)):
            with self.assertRaises(StoreError): self.w.update(bad)

    def test_text_clean_and_capped(self):
        self.w.update(dict(MSG, title='x' * 500, detail='token=abc123 ' + 'y' * 200, ref='Bearer abcdefghijkl'))
        x = self.w.view()['workers'][0]
        self.assertLessEqual(len(x['title']), 120); self.assertLessEqual(len(x['detail']), 80); self.assertNotIn('abc123', x['detail']); self.assertNotIn('abcdefghijkl', x['ref'])

    def test_stalled_after_5_min_dropped_after_30(self):
        self.w.update(MSG)
        self.c.move(seconds=299); self.assertFalse(self.w.view()['workers'][0]['stalled'])
        self.c.move(seconds=2); x = self.w.view()['workers'][0]; self.assertTrue(x['stalled']); self.assertEqual(x['stage'], 'assessing')
        self.c.move(minutes=24); self.assertEqual(len(self.w.view()['workers']), 1)
        self.c.move(minutes=1); self.assertEqual(self.w.view()['workers'], [])

    def test_update_resets_stalled_keeps_start(self):
        self.w.update(dict(MSG, started_at=None) if False else MSG); self.c.move(minutes=10)
        self.w.update(dict(MSG, stage='planning', detail='brain_write x')); x = self.w.view()['workers'][0]
        self.assertFalse(x['stalled']); self.assertEqual(x['stage'], 'planning'); self.assertEqual(x['elapsed_s'], 15 * 60)

    def test_missing_started_at_uses_first_seen(self):
        m = {k: v for k, v in MSG.items() if k != 'started_at'}; self.w.update(m); self.c.move(minutes=2); self.w.update(m)
        self.assertEqual(self.w.view()['workers'][0]['elapsed_s'], 120)

    def test_idle_removes(self):
        self.w.update(MSG); self.w.update(dict(MSG, stage='idle')); self.assertEqual(self.w.view()['workers'], [])

    def test_two_workers_sorted(self):
        self.w.update(dict(MSG, worker='queue')); self.w.update(dict(MSG, worker='overnight'))
        self.assertEqual([x['worker'] for x in self.w.view()['workers']], ['overnight', 'queue'])

    def test_worker_cap(self):
        for i in range(8): self.w.update(dict(MSG, worker='w%d' % i))
        with self.assertRaises(StoreError): self.w.update(dict(MSG, worker='extra'))
        self.w.update(dict(MSG, worker='w1'))   # an existing one still updates

    def test_persisted_copy_survives_restart(self):
        self.w.update(MSG); w2 = Workers(self.d, self.c); self.assertEqual(w2.view()['workers'][0]['title'], 'Do the thing')

    def test_corrupt_copy_ignored(self):
        open(os.path.join(self.d, 'workers.json'), 'w').write('{not json'); self.assertEqual(Workers(self.d, self.c).view()['workers'], [])

    def test_stored_file_has_no_secrets_fields(self):
        self.w.update(MSG); self.assertEqual(set(json.load(open(os.path.join(self.d, 'workers.json')))['queue']), {'worker', 'title', 'ref', 'started_at', 'stage', 'detail', 'updated_at'})


class Api(Base):
    def setUp(self):
        self.make()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), M.make_handler(self.svc, lambda: self.svc.opts['api_token']))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()

    def call(self, method, path, body=None, token=TOKEN):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), method=method, data=None if body is None else json.dumps(body).encode())
        if token is not None: req.add_header('Authorization', 'Bearer ' + token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def test_token_required(self):
        self.assertEqual(self.call('GET', '/workers', token=None)[0], 401); self.assertEqual(self.call('GET', '/workers', token='nope')[0], 401)
        self.assertEqual(self.call('POST', '/workers/status', MSG, token=None)[0], 401); self.assertEqual(self.call('POST', '/workers/status', MSG, token='nope')[0], 401)
        self.assertEqual(self.call('GET', '/workers')[1]['workers'], [])

    def test_post_then_get(self):
        self.assertEqual(self.call('POST', '/workers/status', MSG), (200, {'ok': True}))
        s, v = self.call('GET', '/workers'); x = v['workers'][0]
        self.assertEqual((s, x['worker'], x['stage'], x['title']), (200, 'queue', 'assessing', 'Do the thing')); self.assertNotIn(TOKEN, json.dumps(v))
        self.call('POST', '/workers/status', dict(MSG, stage='idle')); self.assertEqual(self.call('GET', '/workers')[1]['workers'], [])

    def test_validation_errors(self):
        self.assertEqual(self.call('POST', '/workers/status', dict(MSG, stage='x'))[0], 400)
        self.assertEqual(self.call('POST', '/workers/status', dict(MSG, extra=1))[0], 400)
        self.assertEqual(self.call('POST', '/workers/status', {'worker': 'queue'})[0], 400)

    def test_runner_payload_shape_accepted(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
        import worker_stage as WS
        h = WS.Heartbeat('queue', 'T', 'r'); h.update('implementing', 'Edit x/y.py'); p = h.sent[0]
        self.assertEqual(self.call('POST', '/workers/status', p)[0], 200)

    def test_queue_api_unaffected(self):
        self.assertEqual(self.call('GET', '/status')[0], 200)


if __name__ == '__main__':
    unittest.main()
