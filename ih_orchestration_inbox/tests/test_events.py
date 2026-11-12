"""Events, digest and long poll of the inbox (0.2.0). Fakes only: a temp folder, a local HTTP server on a free port, no bus, no network beyond localhost.
run: python3 -m unittest discover -s ih_orchestration_inbox/tests"""
import json, os, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import main as M

TOKEN = 'FAKE-API-TOKEN-xyz'
SECRET_TEXT = 'please look up the bin day'


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.build()

    def build(self, **opt):
        p = os.path.join(self.dir, 'options.json')
        json.dump(dict({'api_token': TOKEN}, **opt), open(p, 'w'))
        self.ring = M.Ring()
        self.m = M.Module(M.load_options(p), ring=self.ring, data=self.dir, threaded=False)
        self.m.lg.addHandler(self.ring); self.m.lg.setLevel(20)
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), M.make_handler(self.m))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.url = 'http://127.0.0.1:%d' % self.srv.server_address[1]

    def call(self, method, path, body=None, token=TOKEN, raw=None):
        h = {'Authorization': 'Bearer ' + token} if token else {}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + path, method=method, headers=h, data=data), timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}')

    def event(self, kind='handoff', source='assistant', ref='h1', text=SECRET_TEXT, **kw):
        return self.call('POST', '/events', dict({'kind': kind, 'source': source, 'ref': ref, 'text': text}, **kw))


class Events(Base):
    def test_event_is_stored_and_read_back(self):
        st, a = self.event()
        self.assertEqual((st, a), (200, {'stored': True, 'seq': 1}))
        st, q = self.call('GET', '/queue')
        r = q['messages'][0]
        self.assertEqual((r['kind'], r['source'], r['ref'], r['text'], r['allowed'], r['seq']), ('handoff', 'assistant', 'h1', SECRET_TEXT, True, 1))

    def test_same_ref_is_stored_once_but_other_kind_or_ref_is_new(self):
        self.event(); st, a = self.event()
        self.assertEqual(a, {'stored': False, 'seq': None})
        self.assertTrue(self.event(ref='h2')[1]['stored'])
        self.assertTrue(self.event(kind='reaction')[1]['stored'])
        self.assertEqual(self.m.status()['counts']['events'], 3)
        self.assertEqual(self.m.status()['counts']['event_duplicates'], 1)

    def test_no_ref_is_never_a_duplicate(self):
        self.assertTrue(self.event(ref='')[1]['stored']); self.assertTrue(self.event(ref='')[1]['stored'])

    def test_duplicate_check_survives_a_restart(self):
        self.event(); self.srv.shutdown(); self.build()
        self.assertFalse(self.event()[1]['stored'])
        self.assertEqual(self.event(ref='h2')[1]['seq'], 2)

    def test_needs_the_token(self):
        self.assertEqual(self.call('POST', '/events', {'kind': 'handoff', 'source': 's'}, token='wrong')[0], 401)
        self.assertEqual(self.call('GET', '/digest', token='')[0], 401)
        self.srv.shutdown(); self.build(api_token='')
        self.assertEqual(self.call('POST', '/events', {'kind': 'handoff', 'source': 's'})[0], 401)   # no token configured: refuses everything

    def test_bad_input_is_refused(self):
        for bad in ({'kind': 'Hand off'}, {'kind': 'message'}, {'kind': ''}, {'source': 'a b'}, {'source': ''}, {'ref': 'a/b c'}, {'ref': 'x' * 81}, {'text': 5}, {'kind': 7}):
            self.assertEqual(self.event(**bad)[0], 400, bad)
        self.assertEqual(self.call('POST', '/events', raw=b'not json')[0], 400)
        self.assertEqual(self.call('POST', '/events', {'source': 's'})[0], 400)
        self.assertEqual(self.call('POST', '/events', raw=b'{"kind":"a","source":"s","text":"' + b'x' * M.EVENT_BODY_MAX + b'"}')[0], 413)
        self.assertEqual(self.m.queue.seq, 0)

    def test_text_is_cut_and_never_logged_or_in_status(self):
        self.event(text='y' * (M.MAX_TEXT + 50))
        self.assertEqual(len(self.call('GET', '/queue')[1]['messages'][0]['text']), M.MAX_TEXT)
        self.event(ref='h2')
        self.assertNotIn(SECRET_TEXT, ' '.join(self.ring.lines))
        self.assertNotIn(SECRET_TEXT, json.dumps(self.call('GET', '/status', token='')[1]))

    def test_kind_filter_and_messages_have_kind_message(self):
        self.m.queue.add({'msg_id': 'm1', 'channel_key': 'inbox', 'allowed': True, 'received': int(time.time()), 'text': 'hello'})
        self.event(); self.event(kind='reaction', ref='r1')
        kinds = lambda path: [M.kind_of(r) for r in self.call('GET', path)[1]['messages']]
        self.assertEqual(kinds('/queue'), ['message', 'handoff', 'reaction'])
        self.assertEqual(kinds('/queue?kind=handoff'), ['handoff'])
        self.assertEqual(kinds('/queue?kind=message'), ['message'])
        self.assertEqual(self.call('GET', '/queue?kind=BAD!')[0], 400)


class Digest(Base):
    def test_digest_counts_per_kind_without_text_and_follows_the_cursor(self):
        self.event(); self.event(ref='h2'); self.event(kind='reaction', ref='r1')
        st, d = self.call('GET', '/digest?consumer=claude')
        self.assertEqual(d, {'consumer': 'claude', 'cursor': 0, 'new': 3, 'kinds': {'handoff': 2, 'reaction': 1}, 'newest_seq': 3})
        self.assertNotIn(SECRET_TEXT, json.dumps(d))
        self.call('POST', '/ack', {'seq': 2, 'consumer': 'claude'})
        self.assertEqual(self.call('GET', '/digest?consumer=claude')[1]['kinds'], {'reaction': 1})
        self.assertEqual(self.call('GET', '/digest?consumer=other')[1]['new'], 3)   # each consumer has its own cursor
        self.assertEqual(self.call('GET', '/digest?consumer=bad%20name')[0], 400)

    def test_digest_ignores_strangers(self):
        self.m.queue.add({'msg_id': 'm2', 'channel_key': 'inbox', 'allowed': False, 'received': int(time.time()), 'text': 'x'})
        self.assertEqual(self.call('GET', '/digest')[1]['new'], 0)


class LongPoll(Base):
    def test_wait_returns_at_once_when_something_is_there(self):
        self.event(); t = time.time()
        self.assertEqual(self.call('GET', '/digest?wait=5')[1]['new'], 1)
        self.assertLess(time.time() - t, 2)

    def test_wait_times_out_empty(self):
        t = time.time(); st, d = self.call('GET', '/digest?wait=0.3')
        self.assertEqual((st, d['new']), (200, 0)); self.assertGreaterEqual(time.time() - t, 0.25)

    def test_wait_ends_when_an_event_arrives(self):
        for path, pick in (('/digest?wait=8', lambda a: a['new']), ('/queue?wait=8', lambda a: len(a['messages']))):
            out = {}
            th = threading.Thread(target=lambda: out.update(a=self.call('GET', path)[1], t=time.time()))
            t0 = time.time(); th.start(); time.sleep(0.3)
            self.event(ref='w' + path[1]); th.join(10)
            self.assertEqual(pick(out['a']), 1, path); self.assertLess(out['t'] - t0, 4, path)
            self.call('POST', '/ack', {'seq': self.m.queue.seq})

    def test_wait_is_capped_and_bad_wait_refused(self):
        self.assertEqual(self.call('GET', '/digest?wait=abc')[0], 400)
        self.assertEqual(M.MAX_WAIT_S, 25)


if __name__ == '__main__':
    unittest.main()
