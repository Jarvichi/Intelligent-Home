"""bus_watch plugin: return journey, sensor/page-data publishing. Fake clock, fake feed, fake Home Assistant; fictional stops and numbers."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import ihlib.state
from plugins import bus_watch as B

UTC = dt.timezone.utc
MON = dt.datetime(2026, 11, 9, 15, 0, tzinfo=UTC)   # a Monday


def feed_xml(buses):
    """buses = [(vehicle, direction, lat, lon, recorded_at)]"""
    items = ''.join(
        '<VehicleActivity><RecordedAtTime>%s</RecordedAtTime><MonitoredVehicleJourney><DirectionRef>%s</DirectionRef><VehicleRef>%s</VehicleRef>'
        '<VehicleLocation><Latitude>%s</Latitude><Longitude>%s</Longitude></VehicleLocation></MonitoredVehicleJourney></VehicleActivity>' % (rec.isoformat(), d, v, la, lo)
        for v, d, la, lo, rec in buses)
    return ('<Siri xmlns="http://www.siri.org.uk/siri"><ServiceDelivery><VehicleMonitoringDelivery>%s</VehicleMonitoringDelivery></ServiceDelivery></Siri>' % items).encode()


class FakeHttp:
    def __init__(self, buses=(), fail=False, vtype='Zed Enviro400'):
        self.buses, self.fail, self.vtype, self.feed_calls, self.page_calls = list(buses), fail, vtype, 0, 0

    def get_bytes(self, url, headers=None, timeout=20, min_interval=0.0, max_bytes=0):
        if 'datafeed' in url:
            self.feed_calls += 1
            if self.fail:
                raise OSError('down')
            return feed_xml(self.buses)
        self.page_calls += 1
        return ('<meta property="og:description" content="%s">' % self.vtype).encode()

    def post_json(self, url, obj, headers=None, timeout=20):
        return 200


class FakeHA:
    def __init__(self, fail=False):
        self.posts, self.fail = [], fail

    def post(self, path, data=None, timeout=30):
        if self.fail:
            raise RuntimeError('ha down')
        self.posts.append((path, data))
        return {}


class Ctx:
    def __init__(self, folder, settings, now, http, dry=False, ha=None):
        self.settings, self.now, self.http, self.dry = settings, now, http, dry
        self.ha = ha or FakeHA()
        self.state = ihlib.state.Store(folder)
        self.logs = []
        self.opts = {}

    def log(self, m):
        self.logs.append(m)

    def option(self, name):
        return self.opts.get(name, 'test-key' if name == 'bods_api_key' else '')


def base_settings(**over):
    s = dict(B.DEFAULTS)
    s.update({
        'operator_ref': 'OP', 'line_ref': '99', 'direction': 'inbound',
        'stops': {'home_stop': {'name': 'Home Stop', 'code': 'H1', 'lat': 51.1, 'lon': -3.1}},
        'schedule': {'tue': [{'stop': 'home_stop', 'time': '07:30'}]},
        'closed': [['2026-12-24', '2026-12-26', 'Winter break']],
        'route_polyline': [[51.0, -3.0], [51.2, -3.0]], 'sweep_stop': 'home_stop',
        'return_leg': {
            'direction': 'outbound', 'label': 'Town to Village', 'stop': 'town_stop', 'min_gap_min': 15,
            'stops': {'town_stop': {'name': 'Town Stop', 'code': 'S3', 'lat': 51.02, 'lon': -3.0}},
            'departures': ['08:00', '15:20', '17:10'], 'lesson_end': {'mon': '15:05', 'tue': '16:40'}, 'notes': {'mon': 'a note'}},
        'return_route_stops': [{'code': 'S%d' % i, 'lat': 51.0 + 0.01 * (i - 1), 'lon': -3.0, 'name': 'Route stop %d' % i} for i in range(1, 5)],
        'publish_entity': 'sensor.test_bus_home', 'publish_name': 'Test bus home',
    })
    s.update(over)
    return s


def bus_at(lat, now, veh='X-1', d='outbound', age=0):
    return (veh, d, lat, -3.0, now - dt.timedelta(minutes=age))


class TestRules(unittest.TestCase):
    def test_pick_departure(self):
        deps = ['06:55', '10:10', '15:20', '17:10']
        self.assertEqual(B.pick_departure(deps, '15:05', 15), '15:20')
        self.assertEqual(B.pick_departure(deps, '15:06', 15), '17:10')
        self.assertIsNone(B.pick_departure(deps, '22:00', 15))

    def test_rules_computed_fixed_and_empty(self):
        s = base_settings()
        self.assertEqual([(r['stop'], r['time'], r['note']) for r in B.return_rules_day(s, 'mon')], [('town_stop', '15:20', 'a note')])
        self.assertEqual(B.return_rules_day(s, 'tue')[0]['time'], '17:10')
        self.assertEqual(B.return_rules_day(s, 'wed'), [])
        s['return_leg']['schedule'] = {'wed': [{'stop': 'town_stop', 'time': '09:09'}]}
        self.assertEqual(B.return_rules_day(s, 'wed')[0]['time'], '09:09')

    def test_ready_needs_stop_code_in_route(self):
        s = base_settings()
        self.assertTrue(B.return_ready(s))
        s['return_route_stops'] = s['return_route_stops'][:2]
        self.assertFalse(B.return_ready(s))
        self.assertFalse(B.return_ready(base_settings(return_leg={})))

    def test_deck_of(self):
        self.assertEqual(B.deck_of('Zed Enviro400'), 'double')
        self.assertEqual(B.deck_of('Zed Solo'), 'single')
        self.assertIsNone(B.deck_of('?'))
        self.assertIsNone(B.deck_of(None))


class TestStatus(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def st(self, now, http, dry=False, **over):
        s = base_settings(**over)
        ctx = Ctx(self.dir, s, now, http, dry)
        box = B.Box(ctx)
        calls = []

        def getbuses():
            calls.append(1)
            return B.parse(B.fetch(ctx, s))
        return B.status(ctx, box, s, 'return', getbuses, now), calls

    def test_closed_day(self):
        out, calls = self.st(dt.datetime(2026, 12, 24, 15, 0, tzinfo=UTC), FakeHttp())
        self.assertEqual((out['state'], out['note'], out['direction']), ('No college bus', 'Winter break', 'Town to Village'))
        self.assertEqual(calls, [])

    def test_no_rules_day(self):
        out, _ = self.st(dt.datetime(2026, 11, 11, 15, 0, tzinfo=UTC), FakeHttp())   # Wednesday
        self.assertEqual((out['state'], out['note']), ('No college bus', 'No bus today'))

    def test_outside_window_no_network(self):
        h = FakeHttp()
        out, calls = self.st(dt.datetime(2026, 11, 9, 9, 0, tzinfo=UTC), h)
        self.assertEqual((out['state'], out['note']), ('Off', 'Next check 14:55'))
        self.assertEqual((calls, h.feed_calls), ([], 0))
        out, _ = self.st(dt.datetime(2026, 11, 9, 16, 0, tzinfo=UTC), h)
        self.assertEqual(out['note'], 'Next check tomorrow')

    def test_live_bus(self):
        h = FakeHttp([bus_at(51.004, MON)])
        out, _ = self.st(MON, h)
        self.assertEqual(out['state'], '4 min')
        self.assertEqual(sorted(out), sorted(['state', 'eta_min', 'updated', 'direction', 'stop', 'usual', 'arrives', 'late_min', 'deck', 'vehicle',
                                              'last_seen_near', 'km_away', 'data_age_min', 'others']))
        self.assertEqual((out['stop'], out['usual'], out['vehicle'], out['deck'], out['others']), ('Town Stop', '15:20', 'X-1', 'double', []))
        self.assertEqual(out['last_seen_near'], 'Route stop 1')
        self.assertIsInstance(out['late_min'], int)

    def test_due_and_other_buses(self):
        out, _ = self.st(MON, FakeHttp([bus_at(51.0195, MON)]))
        self.assertEqual(out['state'], 'Due')
        h = FakeHttp([bus_at(51.0195, MON), bus_at(51.0, MON, veh='X-2')])   # the bus nearest the usual time is the one followed
        out, _ = self.st(MON, h)
        self.assertEqual((out['vehicle'], [o['veh'] for o in out['others']]), ('X-2', ['X-1']))

    def test_no_bus_yet_no_live_no_data(self):
        out, _ = self.st(MON, FakeHttp([bus_at(51.0, MON, d='inbound')]))
        self.assertEqual((out['state'], out['note']), ('No bus yet', 'No outbound no. 99 bus before your stop right now'))
        out, _ = self.st(MON, FakeHttp([bus_at(51.004, MON, age=12)]))
        self.assertEqual((out['state'], out['note']), ('No live data', 'Last GPS 12 min old'))
        self.assertEqual(out['last_seen_near'], 'Route stop 1')
        out, _ = self.st(MON, FakeHttp(fail=True))
        self.assertEqual((out['state'], out['note']), ('No data', 'BODS fetch failed: OSError'))

    def test_sensor_payload(self):
        state, attrs = B.sensor_payload({'state': '4 min', 'eta_min': 3.6}, 'Name')
        self.assertEqual((state, attrs), ('4 min', {'eta_min': 3.6, 'friendly_name': 'Name', 'icon': 'mdi:bus-clock'}))


class TestRun(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def ctx(self, now, http, dry=False, ha=None, **over):
        return Ctx(self.dir, base_settings(**over), now, http, dry, ha)

    def test_outside_window_no_network_and_publishes_off(self):
        h = FakeHttp()
        c = self.ctx(dt.datetime(2026, 11, 9, 9, 0, tzinfo=UTC), h)
        self.assertTrue(B.run(c))
        self.assertEqual(h.feed_calls, 0)
        path, data = c.ha.posts[0]
        self.assertEqual((path, data['state'], data['attributes']['note']), ('states/sensor.test_bus_home', 'Off', 'Next check 14:55'))
        self.assertEqual(data['attributes']['friendly_name'], 'Test bus home')

    def test_nothing_set_nothing_published_no_network(self):
        h = FakeHttp()
        c = self.ctx(dt.datetime(2026, 11, 9, 9, 0, tzinfo=UTC), h, publish_entity='')
        B.run(c)
        self.assertEqual((h.feed_calls, c.ha.posts), (0, []))

    def test_live_tracks_publishes_once_per_pass_and_throttles(self):
        h = FakeHttp([bus_at(51.004, MON)])
        c = self.ctx(MON, h)
        self.assertTrue(B.run(c))
        self.assertEqual(h.feed_calls, 1)   # tracker and sensor share one fetch
        self.assertEqual(c.ha.posts[0][1]['state'], '4 min')
        rows = [json.loads(l) for l in open(os.path.join(self.dir, 'shadow.jsonl'))]
        self.assertEqual([r['kind'] for r in rows], ['all', 'poll'])
        self.assertEqual(rows[1]['journey'], 'return')
        # 90 s later: not due yet (interval 120, pass 90 -> due after 75 s... so use 30 s)
        c2 = self.ctx(MON + dt.timedelta(seconds=30), h, ha=c.ha)
        B.run(c2)
        self.assertEqual(len(c.ha.posts), 1)
        c3 = self.ctx(MON + dt.timedelta(seconds=120), h, ha=c.ha)
        B.run(c3)
        self.assertEqual(len(c.ha.posts), 2)

    def test_dry_writes_nothing_posts_nothing(self):
        h = FakeHttp([bus_at(51.004, MON)])
        c = self.ctx(MON, h, dry=True)
        self.assertTrue(B.run(c))
        self.assertEqual(c.ha.posts, [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'shadow.jsonl')))
        self.assertTrue(any('[DRY] would publish sensor.test_bus_home: 4 min' in m for m in c.logs))

    def test_failed_post_is_retried_next_pass(self):
        h = FakeHttp()
        c = self.ctx(dt.datetime(2026, 11, 9, 9, 0, tzinfo=UTC), h, ha=FakeHA(fail=True))
        self.assertFalse(B.run(c))
        good = FakeHA()
        c2 = self.ctx(dt.datetime(2026, 11, 9, 9, 0, 5, tzinfo=UTC), h, ha=good)
        B.run(c2)
        self.assertEqual(len(good.posts), 1)

    def test_bus_passes_stop_writes_return_promise_record(self):
        h = FakeHttp([bus_at(51.004, MON)])
        B.run(self.ctx(MON, h))
        h.buses = [bus_at(51.03, MON + dt.timedelta(minutes=5))]   # past the stop
        t = MON + dt.timedelta(minutes=5)
        B.run(self.ctx(t, h))
        recs = [json.loads(l) for l in open(os.path.join(self.dir, 'promise_log.jsonl'))]
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r['journey'], r['stop'], r['scheduled'], r['vehicle'], r['promise_journey'], r['verdict']), ('return', 'town_stop', '15:20', 'X-1', False, 'kept'))

    def test_upsert_keeps_journeys_apart(self):
        a = B.make_record('2026-11-09', 'x', 'X', '07:30', 'V', None, None, None, promise_stop='x')
        b = B.make_record('2026-11-09', 'x', 'X', '07:30', 'V', None, None, None, promise_stop='x', journey='return')
        self.assertTrue(a['promise_journey'])
        self.assertFalse(b['promise_journey'])
        rows, ch = B.upsert_rows([a], b)
        self.assertEqual((len(rows), ch), (2, True))

    def test_closed_day_no_return_tracking(self):
        h = FakeHttp([bus_at(51.004, MON)])
        c = self.ctx(MON, h, closed=[['2026-11-09', '2026-11-09', 'Shut']])
        B.run(c)
        self.assertEqual(c.ha.posts[0][1]['state'], 'No college bus')
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'shadow.jsonl')))


class TestPageData(unittest.TestCase):
    def rec(self, day, journey='morning', verdict='kept', stop='home_stop', sched='07:30', veh='V-1'):
        r = B.make_record(day, stop, 'Home Stop (x)', sched, veh, 'Zed Enviro400', None, None, promise_stop='home_stop', journey=journey)
        r['verdict'] = verdict
        return r

    def test_promise_payload(self):
        rows = [self.rec('2026-11-0%d' % d) for d in range(1, 6)] + [self.rec('2026-11-06', verdict='broken'), self.rec('2026-11-06', journey='return', stop='town_stop', sched='15:20')]
        state, a = B.promise_payload(base_settings(), rows, MON)
        self.assertEqual(state, '7')
        self.assertEqual((a['counts']['kept'], a['counts']['broken'], a['counts']['days'], a['counts']['pct_kept']), (5, 1, 6, 83))
        self.assertEqual(a['rows'][0]['date'], '2026-11-06')
        self.assertEqual(sum(1 for r in a['rows'] if r['journey'] == 'return'), 1)
        self.assertIn('Promised journey, 6 day(s)', a['summary'])
        self.assertEqual(a['compare']['college']['double'], 5)
        self.assertEqual(sorted(a['rows'][0]), sorted(['date', 'journey', 'scheduled', 'stop', 'stop_name', 'day_type', 'promise_journey', 'vehicle', 'vehicle_type', 'deck',
                                                      'verdict', 'label', 'text', 'bus']))

    def test_promise_payload_capped(self):
        rows = [self.rec((dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat()) for i in range(1500)]
        s = base_settings(promise_rows=1000)
        state, a = B.promise_payload(s, rows, MON)
        self.assertEqual(state, '1500')
        self.assertLessEqual(len(json.dumps(a)), B.PAYLOAD_MAX)
        self.assertEqual(a['rows'][0]['date'], rows[-1]['date'])
        self.assertGreater(len(a['rows']), 50)

    def test_promise_entity_published_from_log(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, 'promise_log.jsonl'), 'w') as f:
            f.write(json.dumps(self.rec('2026-11-06')) + '\n')
        c = Ctx(d, base_settings(publish_entity='', promise_entity='sensor.test_promise'), dt.datetime(2026, 11, 9, 9, 0, tzinfo=UTC), FakeHttp())
        B.run(c)
        path, data = c.ha.posts[0]
        self.assertEqual((path, data['state'], len(data['attributes']['rows'])), ('states/sensor.test_promise', '1', 1))

    def test_schedule_payload(self):
        state, a = B.schedule_payload(base_settings())
        self.assertEqual(a['rows']['tue'], [['Home Stop', '07:30'], ['Bus home: Town Stop', '17:10']])
        self.assertEqual(a['rows']['mon'], [['Bus home: Town Stop', '15:20']])
        self.assertEqual(a['closed'], [['2026-12-24', '2026-12-26', 'Winter break']])
        self.assertEqual(state, '3')
        _, a = B.schedule_payload(base_settings(return_row_prefix='Home: '))
        self.assertEqual(a['rows']['mon'], [['Home: Town Stop', '15:20']])

    def test_morning_entity_dry_logs_only(self):
        c = Ctx(tempfile.mkdtemp(), base_settings(publish_entity='', morning_entity='sensor.test_morning', schedule_entity='sensor.test_sched'),
                dt.datetime(2026, 11, 9, 9, 0, tzinfo=UTC), FakeHttp(), dry=True)
        B.run(c)
        self.assertEqual(c.ha.posts, [])
        self.assertTrue(any('would publish sensor.test_morning' in m for m in c.logs))
        self.assertTrue(any('would publish sensor.test_sched' in m for m in c.logs))


if __name__ == '__main__':
    unittest.main()
