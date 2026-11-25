"""Main inbox behaviour on the database: on_in stores and reacts, the HTTP API keeps its shape, status carries the db block. Temp folders, no bus."""
import json, os, shutil, sys, tempfile, threading, unittest, urllib.request, urllib.error
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import main

KEY = 'chan_a'
TOKEN = 'test-token'


class FakeClient:
    def __init__(self):
        self.dry, self.reacted, self.active, self.last_hello, self.rejected = True, [], True, None, {}

    def react(self, channel, msg_id, emoji):
        self.reacted.append((channel, msg_id, emoji, self.dry))
        return {'ok': True}


def event(n, allowed=True, text=None):
    return {'type': 'message', 'msg_id': 'm%d' % n, 'channel_key': KEY, 'author_id': 'u1', 'allowed': allowed, 'msg_ts': 1700000000 + n,
            'content': text or 'hi %d' % n, 'attachments': {'count': 0, 'kinds': []}}


class Inbox(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, 'options.json'), 'w') as f:
            json.dump({'channel_keys': [KEY], 'api_token': TOKEN, 'shadow': False}, f)
        self.opts = main.load_options(os.path.join(self.d, 'options.json'))
        self.mod = main.Module(self.opts, bus=object(), data=self.d, threaded=False)
        self.mod.client = FakeClient()
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), main.make_handler(self.mod))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:%d' % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.mod.queue.close()
        shutil.rmtree(self.d, ignore_errors=True)

    def get(self, path, token=TOKEN):
        req = urllib.request.Request(self.base + path, headers={'Authorization': 'Bearer ' + token} if token else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, self._body(r)
        except urllib.error.HTTPError as e:
            return e.code, self._body(e)

    @staticmethod
    def _body(r):
        raw = r.read()
        return json.loads(raw) if r.headers.get('Content-Type') == 'application/json' else raw

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method='POST', headers={'Authorization': 'Bearer ' + TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_on_in_stores_dedupes_and_reacts_only_for_allowed(self):
        self.mod.on_in(event(1))
        self.mod.on_in(event(1))
        self.mod.on_in(event(2, allowed=False))
        self.mod.on_in({'type': 'other'})
        self.assertEqual((self.mod.queued, self.mod.dup), (2, 1))
        self.assertEqual(len(self.mod.client.reacted), 1)                            # the stranger got no emoji
        self.assertEqual(self.mod.queue.stats()['rows'], 2)

    def test_queue_api_shape(self):
        self.mod.on_in(event(1)); self.mod.on_in(event(2, allowed=False)); self.mod.on_in(event(3))
        code, body = self.get('/queue?since=0')
        self.assertEqual(code, 200)
        self.assertEqual(set(body), {'since', 'consumer', 'messages', 'newest_seq'})
        self.assertEqual((body['since'], body['consumer'], body['newest_seq']), (0, 'default', 3))
        self.assertEqual([m['seq'] for m in body['messages']], [1, 3])
        self.assertEqual(set(body['messages'][0]), {'msg_id', 'channel_key', 'author_id', 'allowed', 'ts', 'received', 'text', 'reply_to', 'attachments', 'seq'})
        self.assertEqual(len(self.get('/queue?since=0&all=1')[1]['messages']), 3)
        self.assertEqual(len(self.get('/queue?since=0&channel=nothere')[1]['messages']), 0)

    def test_ack_moves_the_consumer_cursor_and_queue_uses_it(self):
        for n in (1, 2, 3):
            self.mod.on_in(event(n))
        self.assertEqual(self.post('/ack', {'seq': 2, 'consumer': 'claude'}), (200, {'consumer': 'claude', 'cursor': 2}))
        self.assertEqual(self.post('/ack', {'seq': 1, 'consumer': 'claude'})[1]['cursor'], 2)
        body = self.get('/queue?consumer=claude')[1]
        self.assertEqual((body['since'], [m['seq'] for m in body['messages']]), (2, [3]))
        self.assertEqual(self.get('/queue?consumer=other')[1]['since'], 0)

    def test_api_errors(self):
        self.assertEqual(self.get('/queue', token='')[0], 401)
        self.assertEqual(self.get('/queue?since=x')[0], 400)
        self.assertEqual(self.get('/queue?consumer=bad%20name')[0], 400)
        self.assertEqual(self.post('/ack', {'seq': -1})[0], 400)
        self.assertEqual(self.get('/healthz', token='')[0], 200)

    def test_status_has_db_block_and_no_message_text(self):
        self.mod.on_in(event(1, text='private words'))
        code, st = self.get('/status', token='')
        self.assertEqual(code, 200)
        self.assertTrue({'queue', 'counts', 'bus', 'db', 'db_degraded'} <= set(st))
        self.assertEqual((st['db']['schema_version'], st['db_degraded'], st['queue']['seq']), (1, False, 1))
        self.assertNotIn('private words', json.dumps(st))

    def test_degraded_inbox_still_serves_the_api_and_stays_healthy(self):
        self.mod.queue.close()
        os.remove(os.path.join(self.d, 'ih.db'))
        with open(os.path.join(self.d, 'ih.db'), 'wb') as f:
            f.write(b'garbage' * 500)
        self.mod.queue = main.inboxdb.InboxStore(self.d, 7).open()
        self.mod.on_in(event(1))
        self.assertEqual([m['seq'] for m in self.get('/queue?since=0')[1]['messages']], [1])
        code, st = self.get('/status', token='')
        self.assertTrue(st['db_degraded'] and st['db']['degraded'])
        self.assertEqual(self.get('/healthz', token='')[0], 200)                      # a restart would not help: stay up

    def test_retention_through_tick(self):
        t = [1000000.0]
        mod = main.Module(self.opts, bus=object(), data=tempfile.mkdtemp(), clock=lambda: t[0], threaded=False)
        mod.client = FakeClient()
        try:
            mod.on_in(event(1))
            t[0] += 8 * 86400
            mod.tick()
            self.assertEqual(mod.queue.stats()['rows'], 0)
        finally:
            mod.queue.close(); shutil.rmtree(mod.data, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
