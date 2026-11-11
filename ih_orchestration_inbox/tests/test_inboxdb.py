"""Inbox storage: legacy import (copy, verify, trash), restart, damaged and too-new files, degraded run, snapshots, trash purge, status. Temp folders only."""
import json, os, shutil, sys, tempfile, time, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import inboxdb, filequeue
from ihlib import db as ihdb

START = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400
CHANNEL = 'chan_a'


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t


def rec(n, received=START - 60, channel=CHANNEL, allowed=True, **over):
    r = {'msg_id': 'm%d' % n, 'channel_key': channel, 'author_id': 'u1', 'allowed': allowed, 'ts': 1700000000 + n, 'received': int(received),
         'text': 'hello %d' % n, 'reply_to': None, 'attachments': {'count': 1, 'kinds': ['image']}}
    r.update(over)
    return r


def put_segment(data, first_seq, recs):
    d = os.path.join(data, 'queue')
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, 'q-%012d.jsonl' % first_seq)
    with open(path, 'w') as f:
        for i, r in enumerate(recs):
            f.write(json.dumps(dict(r, seq=first_seq + i), ensure_ascii=False) + '\n')
    return path


def put_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(obj if isinstance(obj, str) else json.dumps(obj))


class Base(unittest.TestCase):
    def setUp(self):
        self.d, self.clock, self.logs, self.stores = tempfile.mkdtemp(), Clock(), [], []

    def tearDown(self):
        for s in self.stores:
            s.close()
        shutil.rmtree(self.d, ignore_errors=True)

    def store(self, days=7):
        s = inboxdb.InboxStore(self.d, days, clock=self.clock, log=self.logs.append).open()
        self.stores.append(s)
        return s

    def p(self, *a):
        return os.path.join(self.d, *a)

    def legacy(self):
        put_segment(self.d, 1, [rec(1), rec(2, allowed=False), rec(3, reply_to='x9', ts=None)])
        put_segment(self.d, 4, [rec(4, channel='chan_b'), rec(5)])
        put_json(self.p('cursors.json'), {'claude': 3, 'worker': 5})


class Queue(Base):
    def test_add_read_dedupe_ack(self):
        s = self.store()
        a = s.add(rec(1))
        self.assertEqual(a['seq'], 1)
        self.assertEqual(list(a)[-1], 'seq')
        self.assertIsNone(s.add(rec(1)))                       # same msg_id: not stored twice
        s.add(rec(2, allowed=False))
        s.add(rec(3, channel='chan_b'))
        self.assertEqual([m['seq'] for m in s.read(0)], [1, 3])                       # strangers hidden
        self.assertEqual([m['seq'] for m in s.read(0, allowed_only=False)], [1, 2, 3])
        self.assertEqual([m['seq'] for m in s.read(0, 'chan_b')], [3])
        self.assertEqual([m['seq'] for m in s.read(1, limit=1)], [3])
        self.assertEqual(s.read(0)[0], dict(rec(1), seq=1))                            # round trip is exact
        self.assertEqual(s.seq, 3)
        self.assertEqual(s.ack('w', 2), 2)
        self.assertEqual(s.ack('w', 1), 2)                                            # never backwards
        self.assertEqual(s.ack('w', 99), 3)                                           # never past the newest
        self.assertEqual(s.cursor('w'), 3)
        self.assertEqual(s.cursor('nobody'), 0)

    def test_retention_and_seq_never_reused(self):
        s = self.store(days=7)
        s.add(rec(1, received=START - 8 * DAY))
        s.add(rec(2))
        self.assertEqual([m['seq'] for m in s.read(0)], [2])                           # old row already hidden
        self.assertEqual(s.retain(), 1)
        self.assertEqual(s.stats()['segments_dropped'], 1)
        s.retain()
        self.clock.t += 8 * DAY
        self.assertEqual(s.retain(), 1)                                               # now everything is gone
        self.assertEqual(s.add(rec(3))['seq'], 3)                                     # numbers keep counting up

    def test_stats_keys_unchanged(self):
        s = self.store()
        s.add(rec(1))
        s.ack('w', 1)
        st = s.stats()
        self.assertTrue({'seq', 'segments', 'bytes', 'cursors', 'segments_dropped'} <= set(st))
        self.assertEqual((st['seq'], st['cursors'], st['rows']), (1, {'w': 1}, 1))

    def test_many_threads_write_without_losing_rows(self):
        import threading
        s = self.store()
        errs = []

        def work(k):
            try:
                for i in range(20):
                    s.add(rec(k * 100 + i))
            except Exception as e:
                errs.append(e)
        ts = [threading.Thread(target=work, args=(k,)) for k in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(s.stats()['rows'], 120)
        self.assertEqual(s.seq, 120)


class Legacy(Base):
    def test_real_shaped_files_move_to_trash_after_verify(self):
        self.legacy()
        s = self.store()
        self.assertEqual(s.imported, {'messages': 2, 'cursors': 1})
        self.assertEqual([m['seq'] for m in s.read(0, allowed_only=False)], [1, 2, 3, 4, 5])
        self.assertEqual(s.read(0, allowed_only=False)[2]['reply_to'], 'x9')
        self.assertIsNone(s.read(0, allowed_only=False)[2]['ts'])
        self.assertEqual(s.cursor('worker'), 5)
        self.assertEqual(s.add(rec(6))['seq'], 6)                                     # numbering carries on
        self.assertFalse(os.path.exists(self.p('queue')))                              # empty folder removed
        self.assertFalse(os.path.exists(self.p('cursors.json')))
        self.assertEqual(sorted(os.listdir(self.p('trash'))), ['cursors.json', 'q-000000000001.jsonl', 'q-000000000004.jsonl'])
        self.assertEqual(s.health()['trash']['files'], 3)
        self.assertEqual(s.health()['legacy_import_errors'], [])
        self.assertTrue(s.last_check['ok'] and s.last_check['rows_match_live'])       # safety snapshot after import

    def test_restart_is_idempotent(self):
        self.legacy()
        self.store().close()
        s = self.store()
        self.assertEqual(s.imported, {})
        self.assertEqual(s.stats()['rows'], 5)
        self.assertEqual(s.health()['rows']['ih_legacy_import'], 3)

    def test_file_that_reappears_is_trashed_not_reimported(self):
        self.legacy()
        self.store().close()
        put_segment(self.d, 1, [rec(1)])
        s = self.store()
        self.assertEqual(s.stats()['rows'], 5)
        self.assertFalse(os.path.exists(self.p('queue', 'q-000000000001.jsonl')))

    def test_torn_last_line_is_skipped(self):
        path = put_segment(self.d, 1, [rec(1), rec(2)])
        with open(path, 'a') as f:
            f.write('{"seq": 3, "msg_id": "m3", "te')
        s = self.store()
        self.assertEqual(s.stats()['rows'], 2)
        self.assertEqual(s.health()['legacy_import_errors'], [])

    def test_bad_cursors_file_stays_and_is_reported(self):
        put_json(self.p('cursors.json'), '{not json')
        s = self.store()
        self.assertTrue(os.path.exists(self.p('cursors.json')))
        self.assertEqual(len(s.health()['legacy_import_errors']), 1)
        self.assertNotIn('not json', json.dumps(s.health()))                          # counts only

    def test_old_record_without_optional_fields(self):
        put_segment(self.d, 1, [{'msg_id': 'old1', 'channel_key': CHANNEL, 'author_id': 'u', 'allowed': True, 'received': int(START), 'text': 't'}])
        s = self.store()
        m = s.read(0)[0]
        self.assertEqual((m['seq'], m['text'], m['attachments']), (1, 't', {'count': 0, 'kinds': []}))

    def test_cursor_import_keeps_the_higher_value(self):
        s = self.store()
        s.add(rec(1)); s.ack('claude', 1)
        s.close()
        put_json(self.p('cursors.json'), {'claude': 0, 'new': 7, 'bad name!': 3})
        s = self.store()
        self.assertEqual((s.cursor('claude'), s.cursor('new'), s.cursor('bad name!')), (1, 7, 0))

    def test_failed_verify_leaves_file_and_rows(self):
        put_segment(self.d, 1, [rec(1)])
        real = inboxdb._values
        inboxdb._values = lambda r: real(dict(r, text='changed'))                     # stored row will differ from the file
        try:
            s = self.store()
        finally:
            inboxdb._values = real
        self.assertTrue(os.path.exists(self.p('queue', 'q-000000000001.jsonl')))
        self.assertEqual(s.stats()['rows'], 0)
        self.assertEqual(len(s.health()['legacy_import_errors']), 1)


class Degraded(Base):
    def test_corrupt_db_runs_degraded_and_is_never_recreated(self):
        os.makedirs(self.d, exist_ok=True)
        with open(self.p('ih.db'), 'wb') as f:
            f.write(b'this is not a database' * 200)
        self.legacy()
        s = self.store()
        self.assertTrue(s.degraded)
        h = s.health()
        self.assertTrue(h['degraded'] and 'WARNING' in h and h['corrupt_copies'] == 1)
        self.assertFalse(os.path.exists(self.p('ih.db')))                              # not recreated
        self.assertTrue(any('DEGRADED' in l for l in self.logs))
        a = s.add(rec(10))                                                            # still works, on files
        self.assertEqual(a['seq'], 1)
        self.assertTrue(os.path.isdir(self.p('degraded', 'queue')))
        self.assertEqual([m['msg_id'] for m in s.read(0)], ['m10'])
        self.assertEqual(s.ack('w', 1), 1)
        self.assertEqual(s.cursor('w'), 1)
        self.assertEqual(s.stats()['seq'], 1)
        self.assertTrue(os.path.exists(self.p('queue', 'q-000000000001.jsonl')))       # old files untouched

    def test_too_new_schema_runs_degraded_and_file_untouched(self):
        db = ihdb.open(self.p('ih.db'), inboxdb.MIGRATIONS + [(2, 'CREATE TABLE future(a)')])
        db.close()
        with open(self.p('ih.db'), 'rb') as f:
            before = f.read()
        s = self.store()
        self.assertIn('schema version 2', s.degraded)
        with open(self.p('ih.db'), 'rb') as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(ihdb.quarantined(self.p('ih.db')), [])

    def test_degraded_files_import_after_restore_with_new_seq(self):
        s = self.store()
        s.add(rec(1)); s.ack('w', 1)
        s.close()
        fq = filequeue.Queue(self.p('degraded'), 7, self.clock)                       # what a degraded run leaves behind
        fq.add(rec(2)); fq.add(rec(1)); fq.ack('w', 1); fq.ack('z', 2)
        s = self.store()
        self.assertEqual([(m['seq'], m['msg_id']) for m in s.read(0)], [(1, 'm1'), (2, 'm2')])   # m1 already present, m2 gets seq 2
        self.assertEqual((s.cursor('w'), s.cursor('z')), (1, 2))
        self.assertFalse(os.path.exists(self.p('degraded', 'queue')))

    def test_runtime_write_failure_falls_back_to_files(self):
        s = self.store()
        s.add(rec(1))
        s.db.close()                                                                  # every later call fails
        a = s.add(rec(2))
        self.assertIsNotNone(a)
        self.assertTrue(s.health()['error'])
        self.assertTrue(os.path.isdir(self.p('degraded', 'queue')))
        self.assertTrue(any('DATABASE CALL FAILED' in l for l in self.logs))


class Nightly(Base):
    def test_snapshot_once_a_night_keep_three_and_restore_check(self):
        s = self.store()
        s.add(rec(1))
        s.maintain(local_hour=3)                                                      # too early
        snaps = lambda: sorted(os.listdir(self.p('backup')))
        self.assertEqual(len(snaps()) if os.path.isdir(self.p('backup')) else 0, 0)
        for day in range(5):
            self.clock.t += DAY
            s.maintain(local_hour=4)
            s.maintain(local_hour=5)                                                  # same night: no second snapshot
            self.assertTrue(s.last_check['ok'] and s.last_check['rows_match_live'])
        self.assertEqual(len(snaps()), ihdb.SNAPSHOT_KEEP)
        r = ihdb.restore_check(os.path.join(self.p('backup'), snaps()[-1]))
        self.assertEqual((r['ok'], r['tables']['queue_messages']), (True, 1))
        self.assertTrue(any('restore check OK' in l for l in self.logs))

    def test_trash_purged_after_30_days_with_fake_clock(self):
        self.legacy()
        s = self.store()
        self.assertEqual(s.health()['trash']['files'], 3)
        self.clock.t += 29 * DAY
        s.maintain()
        self.assertEqual(s.health()['trash']['files'], 3)
        self.clock.t += 2 * DAY
        s.maintain()
        self.assertEqual(s.health()['trash']['files'], 0)

    def test_status_has_db_health_counts_only(self):
        s = self.store()
        s.add(rec(1, text='secret words'))
        h = s.health()
        self.assertEqual((h['schema_version'], h['expected_schema_version'], h['degraded'], h['error']), (1, 1, False, None))
        self.assertEqual(h['rows']['queue_messages'], 1)
        self.assertNotIn('secret words', json.dumps(h))
        self.assertEqual(h['corrupt_copies'], 0)


if __name__ == '__main__':
    unittest.main()


class EventKindTests(unittest.TestCase):
    def test_event_kind_roundtrip_and_filter(self):
        import tempfile, inboxdb
        with tempfile.TemporaryDirectory() as d:
            s = inboxdb.InboxStore(d, 7).open()
            try:
                ev = s.add({'kind': 'handoff', 'source': 'x', 'ref': 'r1', 'msg_id': 'ev:handoff:r1', 'channel_key': '', 'author_id': '',
                            'allowed': True, 'ts': 1, 'received': int(time.time()), 'text': 'hi'})
                s.add({'msg_id': 'm1', 'channel_key': 'c', 'author_id': 'a', 'allowed': True, 'ts': 1, 'received': int(time.time()), 'text': 't'})
                self.assertEqual([r['seq'] for r in s.read(0, kind='handoff')], [ev['seq']])
                self.assertEqual(s.read(0, kind='handoff')[0]['ref'], 'r1')
                self.assertEqual(len(s.read(0, kind='message')), 1)
                self.assertNotIn('kind', s.read(0, kind='message')[0])
                self.assertFalse(s.wait_beyond(s.seq, 0.05))
            finally:
                s.close()
