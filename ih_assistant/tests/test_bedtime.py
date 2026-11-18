import datetime, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
from main import Assistant, load_options


class Bed(unittest.TestCase):
    def make(self, h, m=0, live=True, day=17, **over):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory()
        self.t = datetime.datetime(2026, 11, day, h, m)
        self.states = {'switch.me': 'on', 'device_tracker.pc': 'home'}
        a.now = lambda: self.t; a.clock = lambda: 1793955600.0; a.log = lambda *x: None; a.maybe_intro = lambda k: None
        a.opts = {'mode': 'live' if live else 'dry'}
        a.ha = type('H', (), {'state': lambda s, e: {'state': self.states.get(e, 'unknown')}})()
        cfg = {'bedtime_nudge': True, 'bedtime_start': '22:00', 'bedtime_end': '00:30', 'bedtime_every_min': 30, 'bedtime_firm_after': '23:00',
               'awake_entities': ['device_tracker.pc'], 'bedtime_texts': [], 'max_per_day': 4, 'live': True, 'presence_entity': 'switch.me'}
        cfg.update(over)
        a.cfg = {'aa': cfg}
        a.stores = {'aa': PersonaStore(self.tmp.name, 'aa', a.clock)}
        return a

    def evs(self, a):
        return a.stores['aa'].read_inbox(0, 20)

    def test_window_and_default_off(self):
        self.assertTrue(self.make(22, 5).tick_bedtime('aa'))
        self.assertFalse(self.make(21, 59).tick_bedtime('aa'))
        self.assertFalse(self.make(0, 45).tick_bedtime('aa'))
        self.assertTrue(self.make(0, 10).tick_bedtime('aa'))   # after midnight, still inside
        self.assertFalse(self.make(22, 5, bedtime_nudge=False).tick_bedtime('aa'))

    def test_interval_and_no_repeat(self):
        a = self.make(22, 5)
        self.assertTrue(a.tick_bedtime('aa')); self.assertFalse(a.tick_bedtime('aa'))
        self.t = datetime.datetime(2026, 11, 17, 22, 25); self.assertFalse(a.tick_bedtime('aa'))
        self.t = datetime.datetime(2026, 11, 17, 22, 31); self.assertTrue(a.tick_bedtime('aa'))
        self.assertEqual(len(self.evs(a)), 2)

    def test_firm_switch(self):
        a = self.make(22, 5); a.tick_bedtime('aa')
        self.t = datetime.datetime(2026, 11, 17, 23, 5); a.tick_bedtime('aa')
        ev = self.evs(a)
        self.assertEqual((ev[0]['source'], ev[0]['type'], ev[0]['facts']['tone']), ('bedtime', 'reminder_due', 'gentle'))
        self.assertEqual(ev[1]['facts']['tone'], 'firm')

    def test_awake_and_presence_gate(self):
        a = self.make(22, 5); self.states['device_tracker.pc'] = 'not_home'
        self.assertFalse(a.tick_bedtime('aa'))
        self.states['device_tracker.pc'] = 'home'; self.states['switch.me'] = 'off'
        self.assertFalse(a.tick_bedtime('aa'))
        self.states['switch.me'] = 'on'
        self.assertTrue(a.tick_bedtime('aa'))   # a blocked slot is not used up
        self.assertFalse(self.make(22, 5, awake_entities=[]).tick_bedtime('aa'))

    def test_max_per_day(self):
        a = self.make(22, 5, max_per_day=2)
        for hh, mm in ((22, 5), (22, 40), (23, 10)):
            self.t = datetime.datetime(2026, 11, 17, hh, mm); a.tick_bedtime('aa')
        self.assertEqual(len(self.evs(a)), 2)

    def test_dry_mode_sends_nothing(self):
        a = self.make(22, 5, live=False)
        self.assertTrue(a.tick_bedtime('aa')); self.assertFalse(a.tick_bedtime('aa'))
        self.assertEqual(self.evs(a), [])

    def test_own_texts_and_options(self):
        a = self.make(22, 5, bedtime_texts=['Bed soon'])
        a.tick_bedtime('aa'); self.assertEqual(self.evs(a)[0]['suggested_text'], 'Bed soon')
        p = os.path.join(self.tmp.name, 'o.json')
        open(p, 'w').write('{"personas":[{"key":"aa","bedtime_nudge":true,"awake_entities":["device_tracker.pc","bad"]}]}')
        c = load_options(p)[0]['personas'][0]
        self.assertEqual((c['bedtime_nudge'], c['bedtime_start'], c['bedtime_every_min'], c['awake_entities']), (True, '22:00', 30, ['device_tracker.pc']))


if __name__ == '__main__':
    unittest.main()
