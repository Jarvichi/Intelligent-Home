import datetime, os, sys, tempfile, unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
import main
from main import Assistant

LONDON = ZoneInfo('Europe/London')
BASE = datetime.datetime(2026, 11, 20, 10, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
S = main.USAGE_SENSORS


class FakeHA:
    def __init__(self, states): self.states = states
    def state(self, e):
        if e not in self.states:
            raise RuntimeError('no such entity')
        return {'state': self.states[e]}


class FakeDiscord:
    def __init__(self): self.sent = []; self.last_id = '9'
    def send(self, ch, text, reply):
        self.sent.append(text); return True


def iso(h, m=0, day=20):
    return datetime.datetime(2026, 11, day, h, m, tzinfo=datetime.timezone.utc).isoformat()


class UsageTests(unittest.TestCase):
    def make(self, states, live=True, opts=None):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory(); self.now = [BASE]
        a.clock = lambda: self.now[0]; a.log = lambda *x: None; a.tz = LONDON
        a.opts = opts if opts is not None else {}
        a.cfg = {k: {'usage_text': '', 'usage_text_notime': ''} for k in ('aa', 'bb')}
        a.stores = {k: PersonaStore(self.tmp.name, k, a.clock) for k in ('aa', 'bb')}
        a.discord = {k: FakeDiscord() for k in ('aa', 'bb')}; a._channel = lambda k: 'ch'
        a.is_live = lambda k: live
        a.ha = FakeHA(states)
        os.environ['IHP_DATA'] = self.tmp.name
        return a

    def out_states(self, **kw):
        s = {S['session']: '100', S['week']: '10', S['session_reset']: iso(13, 5), S['week_reset']: iso(4, 59, 27),
             S['extra_enabled']: 'False', S['extra_pct']: 'unavailable'}
        s.update(kw); return s

    def msg(self, a, k='aa'):
        a.usage_note_message(k, '1')

    def test_not_out_nothing(self):
        a = self.make(self.out_states(**{S['session']: '50'}))
        self.msg(a); self.now[0] += 61
        self.assertFalse(a.tick_usage('aa')); self.assertEqual(a.discord['aa'].sent, [])

    def test_exact_wording_and_wait(self):
        a = self.make(self.out_states())
        self.msg(a); self.now[0] += 59
        self.assertFalse(a.tick_usage('aa')); self.assertEqual(a.discord['aa'].sent, [])
        self.now[0] += 2
        self.assertTrue(a.tick_usage('aa'))
        self.assertEqual(a.discord['aa'].sent, ['I cannot read your message right now: I am out of Claude usage until 13:05. Your message is saved here. I answer when it resets.'])
        self.assertFalse(a.tick_usage('aa')); self.assertEqual(len(a.discord['aa'].sent), 1)   # once per message

    def test_london_time_in_summer(self):
        a = self.make(self.out_states(**{S['session_reset']: datetime.datetime(2026, 7, 1, 12, 0, tzinfo=datetime.timezone.utc).isoformat()}))
        a.clock = lambda: datetime.datetime(2026, 7, 1, 10, 0, tzinfo=datetime.timezone.utc).timestamp()
        self.msg(a); a.clock = lambda: datetime.datetime(2026, 7, 1, 10, 2, tzinfo=datetime.timezone.utc).timestamp()
        a.tick_usage('aa')
        self.assertIn('until 13:00.', a.discord['aa'].sent[0])

    def test_far_reset_has_day(self):
        a = self.make(self.out_states(**{S['session']: '10', S['week']: '100'}))
        self.msg(a); self.now[0] += 61
        a.tick_usage('aa')
        self.assertIn('until Fri 04:59.', a.discord['aa'].sent[0])

    def test_unknown_reset_wording(self):
        a = self.make(self.out_states(**{S['session_reset']: 'unknown'}))
        self.msg(a); self.now[0] += 61
        a.tick_usage('aa')
        self.assertEqual(a.discord['aa'].sent[0], 'I cannot read your message right now: I am out of Claude usage until it resets. Your message is saved here. I answer when it resets.')

    def test_both_out_uses_later(self):
        a = self.make(self.out_states(**{S['week']: '100'}))
        self.msg(a); self.now[0] += 61
        a.tick_usage('aa')
        self.assertIn('until Fri 04:59.', a.discord['aa'].sent[0])

    def test_answered_in_meantime(self):
        a = self.make(self.out_states())
        self.msg(a); self.now[0] += 30
        a.stores['aa'].log_outbox({'kind': 'reply', 'chars': 5, 'mode': 'live', 'ok': True})
        self.now[0] += 40
        self.assertFalse(a.tick_usage('aa')); self.assertEqual(a.discord['aa'].sent, [])
        self.assertFalse(a.stores['aa'].state().get('usage_pending'))

    def test_min_gap(self):
        a = self.make(self.out_states())
        self.msg(a); self.now[0] += 61; a.tick_usage('aa')
        self.now[0] += 100; a.stores['aa'].update_state(lambda s: s.pop('usage_pending', None)); self.msg(a); self.now[0] += 61
        self.assertFalse(a.tick_usage('aa')); self.assertEqual(len(a.discord['aa'].sent), 1)
        self.now[0] += main.USAGE_MIN_GAP_S
        self.msg(a); self.now[0] += 61
        self.assertTrue(a.tick_usage('aa')); self.assertEqual(len(a.discord['aa'].sent), 2)

    def test_extra_credits_left(self):
        a = self.make(self.out_states(**{S['extra_enabled']: 'True', S['extra_pct']: '40'}))
        self.msg(a); self.now[0] += 61
        self.assertFalse(a.tick_usage('aa')); self.assertEqual(a.discord['aa'].sent, [])
        a.ha.states[S['extra_pct']] = '98.5'
        self.assertTrue(a.tick_usage('aa'))

    def test_unavailable_sensor(self):
        a = self.make(self.out_states(**{S['session']: 'unavailable', S['week']: 'unknown'}))
        self.msg(a); self.now[0] += 61
        self.assertFalse(a.tick_usage('aa')); self.assertEqual(a.discord['aa'].sent, [])
        b = self.make({})
        self.msg(b); self.now[0] += 61
        self.assertFalse(b.tick_usage('aa'))

    def test_dry_run_logs_only(self):
        a = self.make(self.out_states(), live=False)
        self.msg(a); self.now[0] += 61
        self.assertTrue(a.tick_usage('aa'))
        self.assertEqual(a.discord['aa'].sent, [])
        self.assertEqual(a.stores['aa'].last_outbox_ts(('usage_reply',)) is not None, True)
        self.assertFalse(a.stores['aa'].state().get('usage_reply_ts'))

    def test_persona_isolation(self):
        a = self.make(self.out_states())
        self.msg(a, 'aa'); self.now[0] += 61
        a.tick_usage('bb'); self.assertEqual(a.discord['bb'].sent, [])
        a.tick_usage('aa'); self.assertEqual(len(a.discord['aa'].sent), 1)
        self.assertEqual(a.discord['bb'].sent, [])

    def test_option_off(self):
        a = self.make(self.out_states(), opts={'usage_autoreply': False})
        self.msg(a); self.now[0] += 61
        self.assertFalse(a.tick_usage('aa')); self.assertEqual(a.discord['aa'].sent, [])


if __name__ == '__main__':
    unittest.main()
