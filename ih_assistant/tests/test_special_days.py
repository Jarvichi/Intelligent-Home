import datetime, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import dates, selfprompts as sp
from store import PersonaStore
from main import Assistant

T0 = 1793955600.0
LINES = ['1990-11-07|Sam Lee|family', '11-20|Sam|anniversary', '03-02|Pat|family']


class Pure(unittest.TestCase):
    def test_own_matches_first_name_and_kind(self):
        ppl = dates.parse(LINES)
        r = dates.own(datetime.date(2026, 11, 7), ppl, 'sam')
        self.assertEqual(r, [('birthday', 'Sam Lee', 1990)])
        self.assertEqual(dates.own(datetime.date(2026, 11, 20), ppl, 'Sam Lee')[0][0], 'anniversary')
        self.assertEqual(dates.own(datetime.date(2026, 3, 2), ppl, 'Sam'), [])   # Pat's day is not Sam's
        self.assertEqual(dates.own(datetime.date(2026, 11, 7), ppl, None), [])

    def test_catalogue(self):
        ids = {p['id']: p['when'] for p in sp.load_special()}
        self.assertEqual(ids, {'special_day_morning': 0, 'special_day_ahead': 3, 'special_day_after': -1})
        self.assertNotIn('special_day_morning', [p['id'] for p in sp.load()[1]])   # never drawn as a random prompt


class Tick(unittest.TestCase):
    def make(self, day=7, hour=9, live=True, household=False):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory()
        self.now = datetime.datetime(2026, 11, day, hour, 5)
        a.now = lambda: self.now; a.clock = lambda: T0; a.log = lambda *x: None
        a.is_live = lambda k: live
        a.cfg = {'aa': {'dates': LINES, 'quiet_start': '22:00', 'quiet_end': '08:00', 'household': household, 'fixed_rule': ''}}
        a.stores = {'aa': PersonaStore(self.tmp.name, 'aa', lambda: T0)}
        a.stores['aa'].merge_profile({'person_name': 'Sam'})
        return a

    def test_on_the_day_once(self):
        a = self.make(7)
        self.assertEqual(a.tick_special('aa'), ['special_day_morning'])
        self.assertEqual(a.tick_special('aa'), [])   # once per date
        ev = [e for e in a.stores['aa'].read_inbox(0, 10) if e.get('type') == 'self_prompt']
        self.assertEqual(len(ev), 1)
        self.assertNotIn('silent', ev[0])
        self.assertIn('birthday', ev[0]['text']); self.assertIn('turning 36', ev[0]['text'])

    def test_ahead_and_after(self):
        self.assertEqual(self.make(4).tick_special('aa'), ['special_day_ahead'])
        self.assertEqual(self.make(8).tick_special('aa'), ['special_day_after'])
        self.assertEqual(self.make(14).tick_special('aa'), [])

    def test_blocked(self):
        self.assertEqual(self.make(7, hour=23).tick_special('aa'), [])      # quiet hours
        self.assertEqual(self.make(7, live=False).tick_special('aa'), [])
        self.assertEqual(self.make(7, household=True).tick_special('aa'), [])

    def test_behaviour_line(self):
        a = self.make(20)
        self.assertIn('anniversary', a.special_line('aa'))
        self.assertEqual(self.make(12).special_line('aa'), '')


if __name__ == '__main__':
    unittest.main()
