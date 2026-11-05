"""Pace gate: bands, hysteresis, day/night switch, limits. No network."""
import os, sys, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import gate as G

DAY = datetime(2026, 11, 5, 12, 0)
NIGHT = datetime(2026, 11, 5, 2, 0)
LATE = datetime(2026, 11, 5, 23, 30)


def d(mode, pace, when=DAY, last=None, session=10, week=40, recent=0, stopped=False):
    return G.decide(mode, last, pace, session, week, recent, stopped, when)


class Bands(unittest.TestCase):
    def test_band_of(self):
        for h, m, want in [(5, 59, 'night'), (6, 0, 'day'), (12, 0, 'day'), (22, 59, 'day'), (23, 0, 'night'), (0, 0, 'night')]:
            self.assertEqual(G.band_of(datetime(2026, 1, 1, h, m)), want, (h, m))

    def test_band_not_wrapping_and_equal(self):
        b = G.DEFAULT_BANDS._replace(night_from='01:00', night_to='05:00')
        self.assertEqual(G.band_of(datetime(2026, 1, 1, 3, 0), b), 'night')
        self.assertEqual(G.band_of(datetime(2026, 1, 1, 6, 0), b), 'day')
        self.assertEqual(G.band_of(datetime(2026, 1, 1, 3, 0), b._replace(night_from='00:00', night_to='00:00')), 'day')

    def test_bad_times_use_defaults(self):
        self.assertEqual(G.band_of(datetime(2026, 1, 1, 2, 0), G.DEFAULT_BANDS._replace(night_from='x', night_to='99:99')), 'night')

    def test_levels_lift_bad_stop(self):
        self.assertEqual(G.levels('day', G.DEFAULT_BANDS._replace(day_start=-3.0, day_stop=-5.0)), (-3.0, -2.0))


class Day(unittest.TestCase):
    def test_hysteresis(self):
        m = 'idle'
        for pace, ok, mode in [(-3.0, False, 'idle'), (-4.5, False, 'idle'), (-5.1, True, 'draining'), (-4.5, True, 'draining'),
                               (-4.0, False, 'idle'), (-3.9, False, 'idle'), (-4.5, False, 'idle')]:
            r = d(m, pace)
            self.assertEqual((r.ok, r.mode), (ok, mode), (pace, r.reason))
            m = r.mode

    def test_exactly_start_does_not_start(self):
        self.assertFalse(d('idle', -5.0).ok)

    def test_reasons_name_the_band(self):
        self.assertEqual(d('idle', -3.2).reason.split(';')[0], 'day band: pace -3.2, above -4, nothing runs')
        self.assertTrue(d('idle', -4.5).reason.startswith('day band: pace -4.5, waiting until it drops below -5'))
        self.assertIn('pace below -5 in about', d('idle', -4.5).reason)
        self.assertEqual(d('idle', -6.0).reason, 'day band: pace -6.0, draining, stops at -4')


class Night(unittest.TestCase):
    def test_hysteresis(self):
        m = 'idle'
        for pace, ok, mode in [(0.5, False, 'idle'), (-0.4, False, 'idle'), (-1.1, True, 'draining'), (-0.4, True, 'draining'),
                               (0.0, False, 'idle'), (-0.4, False, 'idle')]:
            r = d(m, pace, NIGHT)
            self.assertEqual((r.ok, r.mode), (ok, mode), (pace, r.reason))
            m = r.mode

    def test_reasons(self):
        self.assertTrue(d('idle', -0.4, NIGHT).reason.startswith('night band: pace -0.4, waiting until it drops below -1'))
        self.assertTrue(d('idle', 0.3, NIGHT).reason.startswith('night band: pace +0.3, above 0, nothing runs'))
        self.assertEqual(d('idle', -1.5, NIGHT).reason, 'night band: pace -1.5, draining, stops at 0')

    def test_late_evening_is_night(self):
        self.assertEqual(d('idle', -1.5, LATE).band, 'night')


class Switch(unittest.TestCase):
    def test_night_to_day_drops_draining(self):
        r = d('draining', -2.0, DAY, last='night')   # night band allowed this; the day band needs -5
        self.assertFalse(r.ok)
        self.assertEqual((r.mode, r.band), ('idle', 'day'))

    def test_night_to_day_still_allowed_when_far_under(self):
        r = d('draining', -6.0, DAY, last='night')
        self.assertTrue(r.ok)
        self.assertEqual(r.mode, 'draining')

    def test_same_band_keeps_draining(self):
        self.assertTrue(d('draining', -4.5, DAY, last='day').ok)

    def test_day_to_night_keeps_nothing(self):
        r = d('draining', -0.5, NIGHT, last='day')   # drain dropped, then -0.5 is not below -1
        self.assertFalse(r.ok)
        self.assertEqual(r.mode, 'idle')


class Limits(unittest.TestCase):
    def test_session_and_week(self):
        self.assertFalse(d('idle', -9, session=85).ok)
        self.assertFalse(d('idle', -9, week=90).ok)
        r = d('draining', -9, session=90)
        self.assertEqual(r.mode, 'draining')
        self.assertIn('5-hour session at 90%', r.reason)
        self.assertTrue(d('idle', -9, session=None).ok)

    def test_hourly_limit(self):
        self.assertTrue(d('idle', -9, recent=2).ok)
        r = d('idle', -9, recent=3)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, 'rate limit: 3 items already in the last hour')
        self.assertEqual(r.mode, 'draining')

    def test_stop_switch_and_unreadable(self):
        r = d('draining', -9, stopped=True)
        self.assertFalse(r.ok)
        self.assertEqual(r.mode, 'draining')
        self.assertIn('STOP', r.reason)
        r = G.decide('idle', None, None, None, None, 0, False, DAY)
        self.assertFalse(r.ok)
        self.assertIn('cannot be read', r.reason)

    def test_eta(self):
        self.assertEqual(G.eta_hours(-6, -5), 0.0)
        self.assertEqual(G.eta_hours(-2.0, -5.0), round(3 / (100 / 168) + 0.1, 1))


if __name__ == '__main__':
    unittest.main()
