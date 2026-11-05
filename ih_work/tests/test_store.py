"""Queue store: FIFO, dedupe, leases, expiry, acknowledgement, cancel, retry, corrupt files, concurrency."""
import json, os, sys, tempfile, threading, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import Store, StoreError

T0 = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def move(self, **kw):
        self.t += timedelta(**kw)


class T(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.c = Clock()
        self.s = Store(self.dir, self.c)

    def add(self, ref, **k):
        return self.s.enqueue('other', ref, 't' + str(ref), 'do it', **k)

    def code(self, fn, *a, **k):
        with self.assertRaises(StoreError) as cm:
            fn(*a, **k)
        return cm.exception.code

    def test_fifo(self):
        a, b, c = self.add('1'), self.add('2'), self.add('3')
        s = self.s
        x = s.claim('w1', 600); self.assertEqual(x['id'], a['id'])
        s.heartbeat(a['id'], 'w1', 600); s.finish(a['id'], 'w1', True, 'ok')
        self.assertEqual(s.claim('w1', 600)['id'], b['id'])

    def test_one_at_a_time(self):
        self.add('1'); self.add('2'); self.s.claim('w1', 600)
        self.assertEqual(self.code(self.s.claim, 'w2', 600), 'conflict')

    def test_empty(self):
        self.assertIsNone(self.s.claim('w1', 600))

    def test_dedupe_open_only(self):
        a = self.add('1')
        self.assertEqual(self.add('1')['id'], a['id'])
        self.assertEqual(len(self.s.list()), 1)
        self.s.claim('w', 600)
        self.assertEqual(self.add('1')['id'], a['id'])       # claimed counts as open
        self.s.finish(a['id'], 'w', True)
        self.assertNotEqual(self.add('1')['id'], a['id'])
        self.assertNotEqual(self.s.enqueue('todo', '1', 't', 'i')['id'], a['id'])   # other kind

    def test_lease_expiry_requeues_in_place(self):
        a, b = self.add('1'), self.add('2')
        self.s.claim('w1', 600)
        self.c.move(minutes=9); self.assertEqual(self.s.get(a['id'])['status'], 'claimed')
        self.c.move(minutes=2)
        got = self.s.get(a['id'])
        self.assertEqual((got['status'], got['attempts'], got['claimed_by']), ('queued', 1, None))
        self.assertEqual(self.s.claim('w2', 600)['id'], a['id'])   # still first in line

    def test_heartbeat_renews(self):
        a = self.add('1'); self.s.claim('w1', 600)
        for _ in range(5):
            self.c.move(minutes=8)
            self.assertEqual(self.s.heartbeat(a['id'], 'w1', 600)['status'], 'running')
        self.assertEqual(self.s.get(a['id'])['status'], 'running')

    def test_three_expiries_fail(self):
        a = self.add('1')
        for n in range(3):
            self.s.claim('w', 60); self.c.move(minutes=2)
        got = self.s.get(a['id'])
        self.assertEqual((got['status'], got['attempts']), ('failed', 3))
        self.assertIn('lease expired 3 times', got['error'])
        self.assertIsNone(self.s.claim('w', 60))

    def test_ack_needs_the_holder(self):
        a = self.add('1'); self.s.claim('w1', 600)
        self.assertEqual(self.code(self.s.finish, a['id'], 'w2', True), 'forbidden')
        self.assertEqual(self.code(self.s.heartbeat, a['id'], 'w2', 600), 'forbidden')
        self.assertEqual(self.s.finish(a['id'], 'w1', True, 'fine')['status'], 'done')
        self.assertEqual(self.code(self.s.finish, a['id'], 'w1', True), 'conflict')   # no second ack

    def test_ack_after_expiry_refused(self):
        a = self.add('1'); self.s.claim('w1', 60); self.c.move(minutes=5)
        self.assertEqual(self.code(self.s.finish, a['id'], 'w1', True), 'conflict')
        self.assertEqual(self.s.get(a['id'])['status'], 'queued')

    def test_fail_records_error(self):
        a = self.add('1'); self.s.claim('w1', 600)
        got = self.s.finish(a['id'], 'w1', False, error='boom')
        self.assertEqual((got['status'], got['error']), ('failed', 'boom'))

    def test_cancel(self):
        a = self.add('1')
        self.assertEqual(self.s.cancel(a['id'])['status'], 'cancelled')
        self.assertEqual(self.code(self.s.cancel, a['id']), 'conflict')
        self.assertIsNone(self.s.claim('w', 60))
        self.assertEqual(self.code(self.s.cancel, 'nope'), 'missing')
        b = self.add('2'); self.s.claim('w', 60)
        self.assertEqual(self.code(self.s.cancel, b['id']), 'conflict')

    def test_retry_goes_to_back(self):
        a, b = self.add('1'), self.add('2')
        self.s.claim('w', 600); self.s.finish(a['id'], 'w', False, error='x')
        r = self.s.retry(a['id'])
        self.assertEqual((r['status'], r['attempts'], r['error']), ('queued', 0, None))
        self.assertEqual([i['id'] for i in self.s.list(('queued',))], [b['id'], a['id']])
        self.assertEqual(self.code(self.s.retry, b['id']), 'conflict')
        self.assertEqual(len(os.listdir(self.s.items)), 2)    # old file removed

    def test_validation(self):
        self.assertEqual(self.code(self.s.enqueue, 'nope', 'r', 't', 'i'), 'bad')
        self.assertEqual(self.code(self.s.enqueue, 'other', 'r', 't' * 201, 'i'), 'bad')
        self.assertEqual(self.code(self.s.enqueue, 'other', 'r', 't', 'i' * 4001), 'bad')
        self.assertEqual(self.code(self.s.enqueue, 'other', 'r', 't', ''), 'bad')
        self.assertEqual(self.code(self.s.enqueue, 'other', 'r', 't', 'i', approved='yes'), 'bad')
        self.assertEqual(self.code(self.s.enqueue, 'other', 'r', 't', 'i', by='bad name!'), 'bad')
        self.assertEqual(self.code(self.s.enqueue, 'other', 'r', 5, 'i'), 'bad')
        self.assertEqual(self.code(self.s.claim, 'bad name!', 60), 'bad')
        a = self.add('1'); self.s.claim('w', 60)
        self.assertEqual(self.code(self.s.finish, a['id'], 'w', True, 's' * 2001), 'bad')

    def test_corrupt_file_skipped(self):
        self.add('1')
        with open(os.path.join(self.s.items, '000099-bad.json'), 'w') as f:
            f.write('{nope')
        with open(os.path.join(self.s.items, '000098-odd.json'), 'w') as f:
            f.write(json.dumps({'id': 'x', 'seq': 98, 'status': 'weird'}))
        self.assertEqual(len(self.s.list()), 1)
        self.assertEqual(sorted(self.s.corrupt), ['000098-odd.json', '000099-bad.json'])
        self.assertEqual(self.add('2')['seq'], 2)
        os.remove(self.s.counter)
        self.assertEqual(self.add('3')['seq'], 3)      # counter rebuilt from the files

    def test_concurrent_enqueue(self):
        res = []
        ts = [threading.Thread(target=lambda n=n: res.append(self.add(str(n))['seq'])) for n in range(20)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sorted(res), list(range(1, 21)))

    def test_survives_restart(self):
        a = self.add('1'); self.s.claim('w', 600)
        s2 = Store(self.dir, self.c)
        self.assertEqual(s2.get(a['id'])['claimed_by'], 'w')


if __name__ == '__main__':
    unittest.main()
