import datetime, json, os, sys, tempfile, unittest
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


class UsageBase(unittest.TestCase):
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


class UsageTests(UsageBase):

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


class SeqRng:
    """Deterministic random source: returns the queued values (modulo n)."""
    def __init__(self, seq): self.seq = list(seq)
    def randrange(self, n): return self.seq.pop(0) % n if self.seq else 0


RESET = datetime.datetime(2026, 11, 20, 13, 5, tzinfo=datetime.timezone.utc)


class VariantTests(UsageBase):
    def setUp(self):
        self.a = self.make(self.out_states())

    def text(self, a, key='aa', reset=RESET):
        return a.usage_reply_text(key, reset)

    def test_variant_chosen_and_time_filled(self):
        a = self.a; a.rng = SeqRng([1]); a.cfg['aa']['usage_texts'] = ['one {time}', 'two {time}']
        self.assertEqual(self.text(a), 'two 13:05')

    def test_variant_without_time_ok(self):
        a = self.a; a.rng = SeqRng([0]); a.cfg['aa']['usage_texts'] = ['no time here']
        self.assertEqual(self.text(a), 'no time here')

    def test_day_time_text(self):
        a = self.a; a.rng = SeqRng([0]); a.cfg['aa']['usage_texts'] = ['at {time}']
        far = datetime.datetime(2026, 11, 27, 4, 59, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.text(a, reset=far), 'at Fri 04:59')

    def test_no_immediate_repeat(self):
        a = self.a; a.cfg['aa']['usage_texts'] = ['A', 'B', 'C']
        a.rng = SeqRng([0] * 20)
        seen = [self.text(a) for _ in range(6)]
        for x, y in zip(seen, seen[1:]):
            self.assertNotEqual(x, y)

    def test_single_variant_repeats_safely(self):
        a = self.a; a.cfg['aa']['usage_texts'] = ['only']
        self.assertEqual([self.text(a) for _ in range(3)], ['only'] * 3)

    def test_bad_placeholder_and_brace_skipped(self):
        a = self.a; a.rng = SeqRng([0, 0, 0]); a.cfg['aa']['usage_texts'] = ['bad {name}', 'stray } brace', 'good {time}']
        for _ in range(3):
            self.assertEqual(self.text(a), 'good 13:05')

    def test_all_bad_falls_back(self):
        a = self.a; a.cfg['aa']['usage_texts'] = ['bad {name}']; a.cfg['aa']['usage_text'] = 'single {t}'
        self.assertEqual(self.text(a), 'single 13:05')

    def test_precedence_with_time(self):
        a = self.a; a.rng = SeqRng([0])
        self.assertTrue(self.text(a).startswith('I cannot read'))
        a.cfg['aa']['usage_text'] = 'single {t}'
        self.assertEqual(self.text(a), 'single 13:05')
        a.cfg['aa']['usage_texts'] = ['list {time}']
        self.assertEqual(self.text(a), 'list 13:05')

    def test_precedence_without_time(self):
        a = self.a; a.rng = SeqRng([0])
        self.assertIn('until it resets', self.text(a, reset=None))
        a.cfg['aa']['usage_text_notime'] = 'single notime'
        self.assertEqual(self.text(a, reset=None), 'single notime')
        a.cfg['aa']['usage_texts_notime'] = ['list notime {time}']
        self.assertEqual(self.text(a, reset=None), 'list notime it resets')
        a.cfg['aa']['usage_texts'] = ['ignored']
        self.assertEqual(self.text(a, reset=None), 'list notime it resets')

    def test_persona_isolation(self):
        a = self.a; a.cfg['aa']['usage_texts'] = ['A', 'B']; a.cfg['bb']['usage_texts'] = ['A', 'B']
        a.rng = SeqRng([0, 0, 0])
        self.assertEqual(self.text(a, 'aa'), 'A')
        self.assertEqual(self.text(a, 'bb'), 'A')
        self.assertEqual(self.text(a, 'aa'), 'B')
        self.assertEqual(a.stores['bb'].state().get('usage_last_time'), 0)

    def test_variant_sent_through_tick(self):
        a = self.a; a.rng = SeqRng([0]); a.cfg['aa']['usage_texts'] = ['Back at {time}, saved your message']
        self.msg(a); self.now[0] += 61
        self.assertTrue(a.tick_usage('aa'))
        self.assertEqual(a.discord['aa'].sent, ['Back at 13:05, saved your message'])


class VariantOptionParserTests(unittest.TestCase):
    def parse(self, **extra):
        p = {'key': 'aa'}; p.update(extra)
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'personas': [p]}, f)
        try:
            return main.load_options(f.name)[0]['personas'][0]
        finally:
            os.unlink(f.name)

    def test_cap_is_ten(self):
        got = self.parse(usage_texts=['v%d' % i for i in range(15)])
        self.assertEqual(got['usage_texts'], ['v%d' % i for i in range(main.USAGE_VARIANTS_MAX)])
        self.assertEqual(main.USAGE_VARIANTS_MAX, 10)

    def test_bad_and_overlong_dropped(self):
        long_ = 'y' * (main.USAGE_VARIANT_MAX_CHARS + 1)
        ok = 'z' * main.USAGE_VARIANT_MAX_CHARS
        got = self.parse(usage_texts_notime=['  keep  ', '', '   ', 5, None, long_, ok])
        self.assertEqual(got['usage_texts_notime'], ['keep', ok])

    def test_not_a_list_gives_empty(self):
        got = self.parse(usage_texts='text', usage_texts_notime={'a': 1})
        self.assertEqual((got['usage_texts'], got['usage_texts_notime']), ([], []))

    def test_missing_gives_empty(self):
        got = self.parse()
        self.assertEqual((got['usage_texts'], got['usage_texts_notime']), ([], []))


if __name__ == '__main__':
    unittest.main()
