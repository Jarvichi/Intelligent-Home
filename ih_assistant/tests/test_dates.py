import datetime, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import dates
from store import PersonaStore
from main import Assistant

LINES = ['1950-11-10|Sam|family', '11-10|Alex|other', 'junk', '02-30|X|family']


class Pure(unittest.TestCase):
    def test_parse_and_due(self):
        p = dates.parse(LINES)
        self.assertEqual(len(p), 2)
        txt, facts = dates.due(datetime.date(2026, 11, 7), p, (7, 3))
        self.assertIn('Family: Sam (turning 76)', txt); self.assertIn('Other: Alex', txt)
        self.assertEqual(facts['days_ahead'], '3')
        self.assertIsNone(dates.due(datetime.date(2026, 11, 1), p, (7, 3)))
        self.assertEqual(dates.leads('bad'), (7, 3)); self.assertEqual(dates.leads('3, 14'), (14, 3))


class Tick(unittest.TestCase):
    def make(self, hour):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory()
        self.t = datetime.datetime(2026, 11, 7, hour, 5)
        a.now = lambda: self.t; a.clock = lambda: 1793955600.0; a.log = lambda *x: None; a.maybe_intro = lambda k: None
        a.cfg = {'aa': {'dates': LINES, 'dates_leads': '7,3', 'dates_at': '18:00', 'dates_latest': '21:30', 'quiet_start': '22:00', 'quiet_end': '08:00'}}
        a.stores = {'aa': PersonaStore(self.tmp.name, 'aa', a.clock)}
        return a

    def test_queues_once_inside_the_window(self):
        a = self.make(18)
        self.assertTrue(a.tick_dates('aa'))
        self.assertFalse(a.tick_dates('aa'))   # not twice
        ev = a.stores['aa'].read_inbox(0, 10)
        self.assertEqual((ev[0]['type'], ev[0]['source']), ('reminder_due', 'dates'))
        self.assertIn('Sam', ev[0]['suggested_text'])

    def test_outside_the_window_or_nothing_due(self):
        self.assertFalse(self.make(9).tick_dates('aa'))
        a = self.make(19); self.t = datetime.datetime(2026, 11, 20, 19, 0)
        self.assertFalse(a.tick_dates('aa'))
        self.assertEqual(a.stores['aa'].read_inbox(0, 10), [])


if __name__ == '__main__':
    unittest.main()
