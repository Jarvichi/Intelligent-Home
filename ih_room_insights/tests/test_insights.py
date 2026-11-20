import os, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import insights as I
import engine as E
from ihlib import db as ihdb

T0 = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


def row(mins, cur, tgt=21.5, action='heating', state='heat'):
    return {'last_updated': (T0 + timedelta(minutes=mins)).isoformat(),
            'state': state, 'attributes': {'current_temperature': cur, 'temperature': tgt, 'hvac_action': action}}


def office_rows():
    """18.3 -> 21.5 in 80 minutes, then idle."""
    rows = [row(m, round(18.3 + 3.2 * m / 80, 2)) for m in range(0, 81, 10)]
    rows.append(row(85, 21.5, action='idle'))
    return rows


class Warmup(unittest.TestCase):
    def test_office_about_25_min_per_degree(self):
        runs = I.warmup_runs(I.samples_from_history(office_rows()))
        self.assertEqual(len(runs), 1)
        self.assertAlmostEqual(runs[0]['min_per_deg'], 25.0, delta=1.0)
        self.assertAlmostEqual(runs[0]['minutes'], 80.0, delta=1)

    def test_short_or_flat_runs_ignored(self):
        short = [row(0, 18.0), row(10, 18.6), row(12, 18.6, action='idle')]
        flat = [row(m, 18.0) for m in range(0, 120, 20)] + [row(130, 18.0, action='idle')]
        self.assertEqual(I.warmup_runs(I.samples_from_history(short)), [])
        self.assertEqual(I.warmup_runs(I.samples_from_history(flat)), [])

    def test_gap_ends_run(self):
        rows = [row(0, 18.0), row(20, 18.5), row(200, 20.0), row(220, 20.5), row(240, 21.0, action='idle')]
        self.assertTrue(len(I.warmup_runs(I.samples_from_history(rows))) >= 1)

    def test_median_and_minutes(self):
        self.assertEqual(I.median_min_per_deg([20, 30, 25]), 25)
        self.assertIsNone(I.median_min_per_deg([]))
        self.assertEqual(I.minutes_to_target(19.5, 21.5, 25), 50)
        self.assertEqual(I.minutes_to_target(22, 21.5, 25), 0)
        self.assertIsNone(I.minutes_to_target(None, 21.5, 25))
        self.assertIsNone(I.minutes_to_target(19, 21.5, None))


class Frozen(unittest.TestCase):
    def now(self, h):
        return T0 + timedelta(hours=h)

    def test_stuck_while_heating_is_suspect(self):
        rows = [row(m, 17.0) for m in range(0, 8 * 60, 30)]
        sus, why = I.frozen_check(I.samples_from_history(rows), self.now(8))
        self.assertTrue(sus)
        self.assertIn('stuck', why)

    def test_stuck_but_not_wanting_heat_is_fine(self):
        rows = [row(m, 21.5, tgt=19.0, action='idle') for m in range(0, 8 * 60, 30)]
        self.assertFalse(I.frozen_check(I.samples_from_history(rows), self.now(8))[0])

    def test_changing_reading_is_fine(self):
        rows = [row(m, 17.0 + m / 60) for m in range(0, 8 * 60, 30)]
        self.assertFalse(I.frozen_check(I.samples_from_history(rows), self.now(8))[0])

    def test_short_history_not_judged(self):
        rows = [row(m, 17.0) for m in range(0, 120, 30)]
        self.assertFalse(I.frozen_check(I.samples_from_history(rows), self.now(3))[0])

    def test_unavailable_long(self):
        rows = [row(0, 18.0), row(30, None, state='unavailable')]
        rows[1]['attributes'] = {}
        self.assertTrue(I.frozen_check(I.samples_from_history(rows), self.now(8))[0])
        self.assertFalse(I.frozen_check(I.samples_from_history(rows), self.now(2))[0])


def hum(mins, v):
    return {'last_updated': (T0 + timedelta(minutes=mins)).isoformat(), 'state': str(v)}


class Mould(unittest.TestCase):
    def test_levels(self):
        now = T0 + timedelta(hours=4)
        high = I.humidity_samples([hum(0, 50), hum(60, 75), hum(120, 80), hum(200, 78)])
        self.assertEqual(I.mould_check(high, now)['level'], 'high')
        watch = I.humidity_samples([hum(0, 50), hum(200, 75)])
        self.assertEqual(I.mould_check(watch, now)['level'], 'watch')
        ok = I.humidity_samples([hum(0, 90), hum(100, 55)])
        self.assertEqual(I.mould_check(ok, now)['level'], 'ok')
        self.assertEqual(I.mould_check([], now)['level'], 'unknown')
        self.assertEqual(I.humidity_samples([hum(0, 'unavailable')]), [])

    def test_break_resets(self):
        now = T0 + timedelta(hours=5)
        s = I.humidity_samples([hum(0, 80), hum(100, 60), hum(200, 80)])
        self.assertEqual(I.mould_check(s, now)['level'], 'watch')


class FakeHA:
    def __init__(self):
        self.posts, self.services = [], []

    def get(self, path, timeout=30):
        return [{'entity_id': 'climate.office_thermostat_thermostat', 'state': 'heat', 'attributes': {}},
                {'entity_id': 'climate.kitchen_underfloor', 'state': 'heat', 'attributes': {}},
                {'entity_id': 'climate.hallway_thermostat', 'state': 'heat', 'attributes': {}},
                {'entity_id': 'sensor.bath_humidity', 'state': '80', 'attributes': {'device_class': 'humidity'}}]

    def history(self, ent, start, end=None):
        return office_rows() if ent.startswith('climate.') else [hum(0, 75), hum(200, 80)]

    def post(self, path, data=None, timeout=30):
        self.posts.append(path)
        return {}

    def call_service(self, *a, **k):
        self.services.append(a)


class EngineTest(unittest.TestCase):
    def test_run_and_publish_make_no_service_calls(self):
        d = ihdb.open(os.path.join(tempfile.mkdtemp(), 'ih.db'), E.MIGRATIONS)
        ha = FakeHA()
        eng = E.Engine(d, ha, {}, clock=lambda: T0 + timedelta(hours=4))
        out = eng.run_once()
        self.assertEqual(list(out['rooms']), ['climate.office_thermostat_thermostat'])
        r = out['rooms']['climate.office_thermostat_thermostat']
        self.assertAlmostEqual(r['min_per_deg'], 25.0, delta=1)
        self.assertEqual(r['minutes_to_target'], 0)
        self.assertEqual(out['mould']['sensor.bath_humidity']['level'], 'high')
        eng.run_once()   # same history again: no duplicate runs
        self.assertEqual(r['runs'], 1)
        self.assertEqual(eng.run_once()['rooms']['climate.office_thermostat_thermostat']['runs'], 1)
        self.assertEqual(ha.posts, [])
        eng.publish(out)
        self.assertEqual(ha.services, [])
        self.assertTrue(all(p.startswith('states/sensor.ih_') for p in ha.posts))
        self.assertEqual(sorted(ha.posts), ['states/sensor.ih_frozen_valves', 'states/sensor.ih_mould_risk',
                                            'states/sensor.ih_warmup_office'])

    def test_no_service_call_in_source(self):
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app')
        for f in ('engine.py', 'insights.py', 'main.py'):
            self.assertNotIn('call_service', open(os.path.join(base, f)).read())


if __name__ == '__main__':
    unittest.main()
