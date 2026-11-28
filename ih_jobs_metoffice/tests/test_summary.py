"""Met Office /summary: shape, token required, stale fallback, no secrets. No network."""
import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
sys.path.insert(0, os.path.dirname(__file__))
import summary
from test_today import best, met, NOW, SECRET

TOKEN = 'tok-assistant-abc'
OK = 'Bearer ' + TOKEN


def hourly_best(source='met_office'):
    b = best(source)
    b['attributes']['hourly'] = [{'datetime': '2026-11-05T11:00:00+00:00', 'condition': 'rainy', 'temperature': 8.2, 'apikey': SECRET}]
    return b


class T(unittest.TestCase):
    def fetch(self, b=None, m=None):
        return lambda: (b or hourly_best(), m or met(24))

    def test_shape(self):
        s = summary.build(hourly_best(), met(24), NOW)
        self.assertEqual(set(s), {'now', 'today', 'next_days', 'source', 'source_note', 'forecast_age_min'})
        self.assertEqual(s['now'], {'condition': 'Rainy', 'temperature_c': 8.2})
        self.assertEqual(s['today'], {'condition': 'Cloudy', 'high_c': 9.7, 'low_c': 7.1, 'rain_chance_pct': 47, 'rain_mm': None})
        self.assertEqual([d['name'] for d in s['next_days']], ['tomorrow', 'day_after'])
        self.assertEqual(s['source'], 'Met Office')
        self.assertEqual(s['forecast_age_min'], 24)
        self.assertNotIn(SECRET, json.dumps(s))

    def test_stale_met_office_falls_back(self):
        s = summary.build(hourly_best(), met(4 * 60), NOW)
        self.assertEqual(s['source'], 'met.no')
        self.assertIn('3 hours', s['source_note'])

    def test_metno_source(self):
        self.assertEqual(summary.build(hourly_best('met.no'), None, NOW)['source'], 'met.no')

    def test_no_forecast(self):
        self.assertIsNone(summary.build(None, None, NOW))
        self.assertIsNone(summary.build(best(daily=[]), None, NOW))

    def test_token_required(self):
        f = self.fetch()
        self.assertEqual(summary.respond(TOKEN, None, '', f, lambda: NOW)[0], 401)
        self.assertEqual(summary.respond(TOKEN, 'Bearer wrong', '', f, lambda: NOW)[0], 401)
        self.assertEqual(summary.respond(TOKEN, TOKEN, '', f, lambda: NOW)[0], 401)
        self.assertEqual(summary.respond(TOKEN, OK, '', f, lambda: NOW)[0], 200)

    def test_empty_option_is_off(self):
        self.assertEqual(summary.respond('', OK, '', self.fetch(), lambda: NOW)[0], 404)
        self.assertEqual(summary.respond('', 'Bearer ', '', self.fetch(), lambda: NOW)[0], 404)

    def test_formats(self):
        c, ct, body = summary.respond(TOKEN, OK, '', self.fetch(), lambda: NOW)
        self.assertTrue(ct.startswith('text/plain')); self.assertIn(b'Source: Met Office', body); self.assertNotIn(SECRET.encode(), body)
        c, ct, body = summary.respond(TOKEN, OK, 'format=json', self.fetch(), lambda: NOW)
        self.assertEqual(ct, 'application/json'); self.assertEqual(json.loads(body)['now']['condition'], 'Rainy')

    def test_fetch_failure_503(self):
        def boom(): raise OSError('x')
        self.assertEqual(summary.respond(TOKEN, OK, '', boom, lambda: NOW)[0], 503)

    def test_route_wrapper_only_summary(self):
        class JC:
            @staticmethod
            def make_handler(child):
                class H:
                    def do_GET(self): self.called = 'old'
                return H
        summary.install(JC)
        H = JC.make_handler(type('C', (), {'opts': {}})())
        h = H(); h.path = '/status'; h.do_GET(); self.assertEqual(h.called, 'old')


if __name__ == '__main__':
    unittest.main()
