import json, os, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
from ihlib import db as ihdb
import engine as E, sources as S, main as M

SAMPLE_REAL_SHAPE = {'battery': {'batteryChargeLevelPercentage': 72.5, 'chargingStatus': 'CHARGING_STATUS_CHARGING', 'estimatedDistanceToEmptyKm': 310,
                                 'chargerConnectionStatus': 'CHARGER_CONNECTION_STATUS_CONNECTED', 'estimatedChargingTimeToFullMinutes': 95},
                     'odometer': {'odometerMeters': 15000000}}


class FakeHA:
    def __init__(self):
        self.posts = []

    def post(self, path, data=None, timeout=30):
        self.posts.append((path, data))


class FakeSource:
    name = 'pypolestar'

    def __init__(self, payload=None, exc=None):
        self.payload, self.exc = payload, exc

    def fetch(self):
        if self.exc:
            raise self.exc
        return self.payload


def mk(opts=None, source=None, t=1000000.0):
    d = ihdb.open(os.path.join(tempfile.mkdtemp(), 'ih.db'), E.MIGRATIONS)
    ha = FakeHA()
    return E.Engine(d, ha, opts or {}, source=source, log=lambda *a: None, clock=lambda: t), ha


class Parse(unittest.TestCase):
    def test_nested(self):
        r = S.parse_payload(SAMPLE_REAL_SHAPE)
        self.assertEqual((r['battery_pct'], r['range_km'], r['charging'], r['charger_connected'], r['odometer_km'], r['eta_full_min']), (72.5, 310.0, 'charging', True, 15000.0, 95.0))

    def test_flat_snake_case_and_miles(self):
        r = S.parse_payload({'battery_charge_level': 50, 'charging_status': 'CHARGING_STATUS_DONE', 'estimated_distance_to_empty_miles': 100, 'odometer_meters': 1609344})
        self.assertEqual((r['battery_pct'], r['charging'], r['odometer_km']), (50.0, 'full', 1609.344))
        self.assertAlmostEqual(r['range_km'], 160.9344)

    def test_missing_and_bad_values_are_none(self):
        for raw in (None, {}, 'x', {'battery': {'batteryChargeLevelPercentage': 'abc', 'estimatedDistanceToEmptyKm': -5}}, {'battery': {'batteryChargeLevelPercentage': 150}}):
            r = S.parse_payload(raw)
            self.assertIsNone(r['battery_pct']); self.assertIsNone(r['range_km']); self.assertEqual(r['charging'], 'unknown')

    def test_charging_states(self):
        for raw, want in (('CHARGING_STATUS_IDLE', 'idle'), ('CHARGING_STATUS_ERROR', 'error'), ('CHARGING_STATUS_SMART_CHARGING', 'scheduled'), ('weird', 'unknown'), (None, 'unknown')):
            self.assertEqual(S.charging_state(raw), want)


class Fixture(unittest.TestCase):
    def test_fixture_is_valid_and_moves(self):
        a, b = S.FixtureSource(lambda: 0).fetch(), S.FixtureSource(lambda: 5000).fetch()
        for raw in (a, b):
            r = S.parse_payload(raw)
            self.assertTrue(20 <= r['battery_pct'] <= 95)
        self.assertNotEqual(a['battery']['batteryChargeLevelPercentage'], b['battery']['batteryChargeLevelPercentage'])

    def test_make_source(self):
        self.assertEqual(S.make_source({'mode': 'fixture'}).name, 'fixture')
        self.assertIsNone(S.make_source({'mode': 'live'}))
        self.assertEqual(S.make_source({'mode': 'live', 'polestar_email': 'a', 'polestar_password': 'b'}).name, 'pypolestar')

    def test_fixture_publishes_flagged(self):
        eng, ha = mk({'mode': 'fixture'})
        eng.publish(eng.run_once())
        names = [p[0] for p in ha.posts]
        self.assertEqual(names, ['states/sensor.polestar_' + n for n in ('battery', 'range', 'charging', 'odometer', 'last_update')])
        self.assertTrue(all(p[1]['attributes']['fixture'] for p in ha.posts))


class Engine(unittest.TestCase):
    def test_no_login_waits_for_car(self):
        eng, ha = mk({'mode': 'live'})
        self.assertIsNone(eng.run_once())
        self.assertIn('waiting for car', eng.status()['error']); self.assertEqual(ha.posts, [])

    def test_live_reading_stored_and_published(self):
        eng, ha = mk({'mode': 'live'}, FakeSource(SAMPLE_REAL_SHAPE))
        rd = eng.run_once(); eng.publish(rd)
        self.assertEqual(len(eng.readings()), 1); self.assertEqual(eng.readings()[0]['battery_pct'], 72.5)
        d = dict(ha.posts)
        self.assertEqual(d['states/sensor.polestar_battery']['state'], 72.5)
        self.assertEqual(d['states/sensor.polestar_range']['state'], 193)
        self.assertEqual(d['states/sensor.polestar_charging']['state'], 'charging')
        self.assertFalse(d['states/sensor.polestar_battery']['attributes']['fixture'])

    def test_error_never_leaks_password(self):
        eng, ha = mk({'mode': 'live'}, FakeSource(exc=RuntimeError('login failed for hunter2')))
        self.assertIsNone(eng.run_once())
        self.assertNotIn('hunter2', json.dumps(eng.status())); self.assertEqual(ha.posts, [])

    def test_empty_reply_not_stored(self):
        eng, _ = mk({}, FakeSource({}))
        self.assertIsNone(eng.run_once()); self.assertEqual(eng.readings(), [])

    def test_old_rows_pruned(self):
        eng, _ = mk({'keep_days': 7}, FakeSource(SAMPLE_REAL_SHAPE), t=1000000.0)
        eng.run_once()
        eng.clock = lambda: 1000000.0 + 8 * 86400
        eng.run_once()
        self.assertEqual(len(eng.readings()), 1)

    def test_no_command_code(self):
        import re
        here = os.path.dirname(E.__file__)
        src = ''
        for n in ('engine.py', 'sources.py', 'main.py'):
            with open(os.path.join(here, n)) as f:
                src += f.read()
        for bad in (r'call_service', r'\block\b', r'\bunlock\b', r'\bclimate\b', r'\bwake\b', r'services/'):
            self.assertIsNone(re.search(bad, src, re.I), bad)


def serve(handler):
    s = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, 'http://127.0.0.1:%d' % s.server_address[1]


def get(url, token=None):
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Http(unittest.TestCase):
    def setUp(self):
        self.eng, _ = mk({'mode': 'live'}, FakeSource(SAMPLE_REAL_SHAPE)); self.eng.run_once()

    def test_token_endpoint(self):
        s, u = serve(M.make_token_handler(self.eng, lambda: 'tok'))
        self.addCleanup(s.shutdown)
        self.assertEqual(get(u + '/healthz')[0], 200)
        self.assertEqual(get(u + '/status')[0], 401); self.assertEqual(get(u + '/status', 'bad')[0], 401)
        c, b = get(u + '/status', 'tok'); self.assertEqual(c, 200); self.assertEqual(json.loads(b)['mode'], 'live')
        c, b = get(u + '/readings', 'tok'); self.assertEqual(json.loads(b)['latest']['battery_pct'], 72.5)
        self.assertEqual(get(u + '/nope', 'tok')[0], 404)

    def test_empty_token_always_refused(self):
        s, u = serve(M.make_token_handler(self.eng, lambda: ''))
        self.addCleanup(s.shutdown)
        self.assertEqual(get(u + '/status', '')[0], 401)

    def test_ui_only_from_ingress_peer(self):
        s, u = serve(M.make_ui_handler(self.eng)); self.addCleanup(s.shutdown)
        self.assertEqual(get(u + '/')[0], 403)
        M.INGRESS_PEER = '127.0.0.1'
        self.addCleanup(setattr, M, 'INGRESS_PEER', '172.30.32.2')
        c, b = get(u + '/'); self.assertEqual(c, 200); self.assertIn(b'Polestar 2', b)
        self.assertEqual(get(u + '/ui.js')[0], 200); self.assertEqual(get(u + '/ih-ui.css')[0], 200)
        self.assertEqual(json.loads(get(u + '/api/readings')[1])['latest']['charging'], 'charging')
        self.assertEqual(get(u + '/etc/passwd')[0], 404)


if __name__ == '__main__':
    unittest.main()
