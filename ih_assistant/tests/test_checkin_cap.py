import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
from main import Assistant


class CapTests(unittest.TestCase):
    def test_replies_do_not_use_up_the_checkin_allowance(self):
        a = Assistant.__new__(Assistant)
        tmp = tempfile.TemporaryDirectory()
        a.clock = lambda: 1793955600.0; a.tz = None
        a.stores = {'aa': PersonaStore(tmp.name, 'aa', a.clock)}
        for _ in range(12):
            a._count_out('aa', 'reply')
        self.assertEqual((a.out_today('aa'), a.checkins_today('aa')), (12, 0))
        a._count_out('aa', 'checkin')
        self.assertEqual((a.out_today('aa'), a.checkins_today('aa')), (13, 1))


import datetime


def make(reason_seq, hhmm=(10, 0)):
    """An Assistant with stubbed checks; reason_seq is a list popped per _skip_reason call (None = pass)."""
    a = Assistant.__new__(Assistant)
    tmp = tempfile.TemporaryDirectory()
    a._tmp = tmp
    a.t = [1793955600.0]
    a.clock = lambda: a.t[0]
    a.tz = None
    a.cfg = {'aa': {'wake_end': '21:00'}}
    a.stores = {'aa': PersonaStore(tmp.name, 'aa', a.clock)}
    a.hm = list(hhmm)
    a.now = lambda: datetime.datetime(2026, 11, 27, a.hm[0], a.hm[1])
    a.make_day_plan = lambda *x, **k: True
    a.log = lambda m: None
    a.sent = []
    a.maybe_intro = lambda k: None
    a.queue_checkin = lambda k, t, kind: a.sent.append(t)
    a._count_out = lambda k, kind: None
    a._note_fired = lambda k, t: None
    a.reasons = reason_seq
    a._skip_reason = lambda k, sl, nm: a.reasons.pop(0) if a.reasons else None
    a.stores['aa'].update_state(lambda s: s.setdefault('plans', {}).__setitem__(
        '2026-11-27', {'slots': [{'time': '10:00', 'kind': 'random', 'status': 'pending'}]}))
    return a


def slot(a):
    return a.stores['aa'].state()['plans']['2026-11-27']['slots'][0]


def advance(a, minutes):
    a.t[0] += minutes * 60
    m = a.hm[0] * 60 + a.hm[1] + minutes
    a.hm = [m // 60, m % 60]


class RetryTests(unittest.TestCase):
    def test_soft_skip_defers_then_sends(self):
        a = make(['messaged recently', None])
        a.tick_plan('aa')
        self.assertEqual(slot(a)['status'], 'deferred')
        a.tick_plan('aa')                      # too early to retry
        self.assertEqual(slot(a)['status'], 'deferred')
        advance(a, 16)
        self.assertEqual(a.tick_plan('aa'), ['10:00'])
        self.assertEqual(slot(a)['status'], 'sent')
        self.assertEqual(a.sent, ['10:00'])

    def test_deferred_expires_to_missed(self):
        a = make(['busy now'] * 50)
        a.tick_plan('aa')
        for _ in range(14):
            advance(a, 16)
            a.tick_plan('aa')
        self.assertEqual(slot(a)['status'], 'missed')
        self.assertEqual(a.sent, [])

    def test_hard_skip_stays_skipped(self):
        for why in ('quiet hours', 'max per day', 'outside waking hours'):
            a = make([why])
            a.tick_plan('aa')
            self.assertEqual(slot(a)['status'], 'skipped')

    def test_no_time_left_is_skipped(self):
        a = make(['not home'], hhmm=(20, 50))
        a.stores['aa'].update_state(lambda s: s['plans']['2026-11-27']['slots'][0].update(time='20:50'))
        a.tick_plan('aa')
        self.assertEqual(slot(a)['status'], 'skipped')

    def test_plan_view_shows_deferred(self):
        a = make(['not home'])
        a._day = lambda: '2026-11-27'
        a.cfg['aa']['checkin_plan'] = True
        a.tick_plan('aa')
        self.assertEqual(a.plan_view('aa')['slots'][0]['status'], 'deferred')


class AutoReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = [1793955600.0]
        self.st = PersonaStore(self.tmp.name, 'aa', lambda: self.t[0])

    def test_auto_entries_ignored_real_ones_count(self):
        self.st.log_outbox({'kind': 'reply', 'auto': True})
        self.assertIsNone(self.st.last_outbox_ts(('reply',), skip_auto=True))
        self.assertIsNotNone(self.st.last_outbox_ts(('reply',)))
        self.st.log_outbox({'kind': 'reply'})
        self.assertIsNotNone(self.st.last_outbox_ts(('reply', 'ask'), skip_auto=True))

    def test_auto_detected_from_inbox(self):
        a = Assistant.__new__(Assistant)
        a.stores = {'aa': self.st}
        self.assertFalse(a._auto_reply('aa', {}))
        self.assertTrue(a._auto_reply('aa', {'auto': True}))
        self.st.append_event({'type': 'message', 'text': 'hi'})
        self.st.append_event({'type': 'reminder_due', 'source': 'dates'})
        self.assertTrue(a._auto_reply('aa', {}))
        self.st.append_event({'type': 'message', 'text': 'thanks'})
        self.assertFalse(a._auto_reply('aa', {}))


if __name__ == '__main__':
    unittest.main()
