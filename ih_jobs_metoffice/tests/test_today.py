"""Met Office 'Today' card: normal, missing, stale, fallback source, no secrets. No network."""
import json, os, sys, unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import today

SECRET = 'sk-SECRET-TOKEN-123'
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc).timestamp()


def day(offset, cond, t, low, prob):
    d = datetime(2026, 11, 5, 12, tzinfo=timezone.utc) + timedelta(days=offset)
    return {'datetime': d.isoformat(), 'condition': cond, 'temperature': t, 'templow': low, 'precipitation_probability': prob, 'apikey': SECRET}


def best(source='met_office', daily=None):
    daily = [day(0, 'cloudy', 9.7, 7.1, 47), day(1, 'rainy', 10.4, 6.9, 33), day(2, 'partlycloudy', 10.2, 6.5, 7)] if daily is None else daily
    return {'state': source, 'attributes': {'source': source, 'daily': daily, 'hourly': [], 'url': 'https://example.invalid/' + SECRET}}


def met(age_min):
    return {'state': 'x', 'attributes': {'fetched_at': (datetime.fromtimestamp(NOW, timezone.utc) - timedelta(minutes=age_min)).isoformat()}}


class T(unittest.TestCase):
    def test_normal(self):
        out = today.build(best(), met(24), NOW)
        names = [r['name'] for r in out['rows']]
        self.assertEqual(names, ['Today', 'Tomorrow', 'Saturday', 'Forecast age'])
        r = out['rows'][0]
        self.assertEqual((r['value'], r['model'], r['pct'], r['note'], r['state']), ('9.7°', 'Cloudy. Low 7.1°', 47.0, '47% chance of rain', 'Met Office'))
        self.assertEqual(out['rows'][3]['value'], '24 min old')
        self.assertEqual(out['rows'][3]['state'], 'ok')

    def test_missing(self):
        self.assertIsNone(today.build(None, None, NOW))
        self.assertIsNone(today.build(best(daily=[]), met(1), NOW))
        self.assertIsNone(today.build({'state': 'x', 'attributes': {'daily': ['bad', {'datetime': 'nope'}]}}, None, NOW))

    def test_missing_fields_and_age(self):
        out = today.build(best(daily=[{'datetime': day(0, 'x', 1, 1, 1)['datetime']}]), None, NOW)
        r = out['rows'][0]
        self.assertTrue(r.get('plain')); self.assertNotIn('value', r); self.assertNotIn('pct', r)
        self.assertEqual(out['rows'][-1]['note'], 'Not reported')

    def test_stale(self):
        out = today.build(best(), met(5 * 60), NOW)
        last = out['rows'][-1]
        self.assertEqual((last['state'], last['value']), ('old', '5 h old'))
        self.assertIn('Older than 3 hours', last['note'])

    def test_fallback_source(self):
        out = today.build(best('met.no'), None, NOW)
        self.assertEqual(out['rows'][0]['state'], 'met.no')
        self.assertEqual(out['rows'][-1]['state'], 'fallback')

    def test_no_secrets(self):
        out = json.dumps(today.build(best(), met(1), NOW))
        self.assertNotIn(SECRET, out)
        self.assertNotIn('http', out)
        self.assertNotIn('apikey', out)

    def test_cache_and_failure(self):
        today._cache.update(t=0.0, v=None)
        n = []
        def f():
            n.append(1); return best(), met(2)
        a = today.today(f, lambda: NOW); b = today.today(f, lambda: NOW + 1)
        self.assertIs(a, b); self.assertEqual(len(n), 1)
        today._cache.update(t=0.0, v=None)
        def boom():
            raise OSError('x')
        self.assertIsNone(today.today(boom, lambda: NOW))


if __name__ == '__main__':
    unittest.main()
