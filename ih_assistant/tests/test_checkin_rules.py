import datetime, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
from main import Assistant

UTC = datetime.timezone.utc


def ts(y, m, d, h=10):
    return datetime.datetime(y, m, d, h, tzinfo=UTC).timestamp()


class Rules(unittest.TestCase):
    def make(self, tmp, when, closed=(), level=10, mpd=4):
        a = Assistant.__new__(Assistant)
        a.clock = lambda: when; a.tz = UTC
        a.stores = {'aa': PersonaStore(tmp, 'aa', a.clock)}
        a.cfg = {'aa': {'max_per_day': mpd, 'closed_dates': list(closed), 'checkin_days': [], 'levels': {'checkin_level': level}}}
        a.log = lambda *x: None
        return a

    # 2026-11-18 is a Wednesday, 2026-11-21 a Saturday
    def test_no_override_is_unchanged(self):
        with tempfile.TemporaryDirectory() as t:
            a = self.make(t, ts(2026, 11, 18))
            self.assertEqual(a.checkin_cap('aa'), 4)
            self.assertTrue(a.day_allowed('aa'))
            b = self.make(t, ts(2026, 11, 18), closed=['2026-11-18'])
            self.assertEqual(b.day_type('aa'), 'holiday')
            self.assertFalse(b.day_allowed('aa'))

    def test_cap_by_day_type(self):
        with tempfile.TemporaryDirectory() as t:
            a = self.make(t, ts(2026, 11, 18))
            self.assertEqual(a.checkin_rules_set('aa', {'weekday': 2, 'weekend': 1})[0], 200)
            self.assertEqual(a.checkin_cap('aa'), 2)
            a.clock = lambda: ts(2026, 11, 21)
            self.assertEqual((a.day_type('aa'), a.checkin_cap('aa')), ('weekend', 1))

    def test_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as t:
            self.make(t, ts(2026, 11, 18)).checkin_rules_set('aa', {'weekday': 3, 'add_closed': '2026-12-24'})
            b = self.make(t, ts(2026, 11, 18))
            self.assertEqual(b.checkin_cap('aa'), 3)
            self.assertEqual(b.checkin_rules('aa')['extra_closed'], ['2026-12-24'])

    def test_ceiling_and_validation(self):
        with tempfile.TemporaryDirectory() as t:
            a = self.make(t, ts(2026, 11, 18))
            for bad in (99, 5, -1, 'x', 1.5, True):
                self.assertEqual(a.checkin_rules_set('aa', {'weekday': bad})[0], 400, bad)
            for bad in ('2026-13', 'tomorrow', ['2026-11-01..2026-10-01'], 5):
                self.assertEqual(a.checkin_rules_set('aa', {'add_closed': bad})[0], 400, bad)
            self.assertEqual(a.checkin_rules('aa')['weekday'], None)
            self.assertEqual(a.checkin_cap('aa'), 4)
            self.assertEqual(a.checkin_rules_set('aa', {'weekday': 4})[0], 200)

    def test_level_zero_stays_zero(self):
        with tempfile.TemporaryDirectory() as t:
            a = self.make(t, ts(2026, 11, 18), level=0)
            a.checkin_rules_set('aa', {'weekday': 3})
            self.assertEqual(a.checkin_cap('aa'), 0)

    def test_closed_range_and_holiday_cap(self):
        with tempfile.TemporaryDirectory() as t:
            a = self.make(t, ts(2026, 12, 25))
            a.checkin_rules_set('aa', {'add_closed': ['2026-12-23..2026-12-27']})
            self.assertEqual(a.day_type('aa'), 'holiday')
            self.assertFalse(a.day_allowed('aa'))
            a.checkin_rules_set('aa', {'holiday': 1})
            self.assertTrue(a.day_allowed('aa'))
            self.assertEqual(a.checkin_cap('aa'), 1)
            code, v = a.checkin_rules_set('aa', {'remove_closed': ['2026-12-23..2026-12-27']})
            self.assertEqual(v['today_type'], 'weekday' if datetime.date(2026, 12, 25).weekday() < 5 else 'weekend')
            self.assertEqual(v['changes'], [{'field': 'extra_closed', 'old': '2026-12-23..2026-12-27', 'new': None}])

    def test_limit_of_closed_dates(self):
        with tempfile.TemporaryDirectory() as t:
            a = self.make(t, ts(2026, 11, 18))
            days = ['2027-01-%02d' % i for i in range(1, 29)] + ['2027-02-%02d' % i for i in range(1, 23)]
            self.assertEqual(a.checkin_rules_set('aa', {'add_closed': days})[0], 200)
            self.assertEqual(a.checkin_rules_set('aa', {'add_closed': ['2027-03-01']})[0], 400)


if __name__ == '__main__':
    unittest.main()
