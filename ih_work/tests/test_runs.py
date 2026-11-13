"""Scheduled and manual runs: schedule logic, count and pace limits, one at a time, settings persistence, API and UI proxy endpoints."""
import json, os, sys, tempfile, threading, unittest, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import runs as R, main as M, ui_server as U
from service import Service
from store import StoreError
from test_service import Base, Clock, Reader, opts, TOKEN

UTC = timezone.utc
T0 = datetime(2026, 11, 5, 1, 0, tzinfo=UTC)     # 01:00, inside the default night window (tz = UTC in tests)


class FakeHA:
    def __init__(self, items):
        self.items = items
        self.calls = 0

    def post(self, path, data, timeout=10):
        self.calls += 1
        return {'service_response': {'todo.claude_to_do': {'items': list(self.items)}}}


def todo(n):
    return [dict(uid='u%d' % i, summary='IH: job %d' % i, description='', due='2026-11-%02d' % (10 + i)) for i in range(n)]


class Pure(unittest.TestCase):
    def test_window_same_day_and_across_midnight(self):
        d = lambda h, m, day=5: datetime(2026, 11, day, h, m, tzinfo=UTC)
        self.assertEqual(R.in_window(d(1, 0), '01:00', '05:30'), (True, '2026-11-05'))
        self.assertEqual(R.in_window(d(0, 59), '01:00', '05:30')[0], False)
        self.assertEqual(R.in_window(d(5, 30), '01:00', '05:30')[0], False)
        self.assertEqual(R.in_window(d(23, 30), '23:00', '05:00'), (True, '2026-11-05'))
        self.assertEqual(R.in_window(d(2, 0, 6), '23:00', '05:00'), (True, '2026-11-05'))   # after midnight: window began yesterday
        self.assertEqual(R.in_window(d(12, 0), '23:00', '05:00')[0], False)

    def test_stop_by_at_next_occurrence(self):
        self.assertEqual(R.stop_by_at(T0, '05:30'), datetime(2026, 11, 5, 5, 30, tzinfo=UTC))
        late = datetime(2026, 11, 5, 23, 30, tzinfo=UTC)
        self.assertEqual(R.stop_by_at(late, '05:00'), datetime(2026, 11, 6, 5, 0, tzinfo=UTC))

    def test_clean_max_and_pace(self):
        self.assertIsNone(R.clean_max(None))
        self.assertIsNone(R.clean_max('Unlimited'))
        self.assertEqual(R.clean_max('7'), 7)
        for bad in (0, -1, 1001, 'x', True, 2.5):
            with self.assertRaises(ValueError):
                R.clean_max(bad)
        self.assertEqual(R.clean_pace('-2.5'), -2.5)
        for bad in (None, True, 'x', 500, float('nan')):
            with self.assertRaises(ValueError):
                R.clean_pace(bad)

    def test_clean_one_rejects_bad_times_unknown_fields_equal_times(self):
        base = R.DEFAULTS['night']
        for raw in ({'start': '25:00'}, {'start': '1:00'}, {'enabled': 'yes'}, {'colour': 1}, {'stop_by': '01:00'}):
            with self.assertRaises(ValueError):
                R.clean_one(raw, base)
        self.assertEqual(R.clean_one({'start': '02:15'}, base)['start'], '02:15')

    def test_defaults_night_on_day_off_and_option_override(self):
        d = R.defaults_from({})
        self.assertTrue(d['night']['enabled'] and d['night']['start'] == '01:00')
        self.assertFalse(d['day']['enabled'])
        d = R.defaults_from({'day_run_enabled': True, 'night_run_max_items': 5, 'night_run_start': 'bad'})
        self.assertTrue(d['day']['enabled'])
        self.assertEqual(d['night']['start'], '01:00')   # bad option: built-in default for that run

    def test_due_kind_once_per_window(self):
        s = R.defaults_from({})
        self.assertEqual(R.due_kind(s, {}, T0), ('night', '2026-11-05'))
        self.assertEqual(R.due_kind(s, {'night': '2026-11-05'}, T0), (None, None))
        self.assertEqual(R.due_kind(s, {}, T0.replace(hour=12)), (None, None))
        s['night']['enabled'] = False
        self.assertEqual(R.due_kind(s, {}, T0), (None, None))

    def test_end_reasons(self):
        now = T0
        run = R.new_run('night', 0.0, 2, now, now + timedelta(hours=1))
        e = lambda **k: R.end_reason(run, k.pop('now', now), k.pop('pace', -5.0), k.pop('session', 10.0), k.pop('week', 40.0), k.pop('stopped', False))
        self.assertEqual(e(), '')
        run['started'] = 2
        self.assertIn('item count', e())
        run['started'] = 0
        self.assertIn('stop-by', e(now=now + timedelta(hours=1)))
        self.assertIn('session', e(session=90.0))
        self.assertIn('week', e(week=95.0))
        self.assertIn('STOP', e(stopped=True))
        self.assertEqual(e(pace=0.5), '')                    # scheduled run, nothing started yet: it waits
        run['started'] = 1
        self.assertIn('pace target', e(pace=0.5))
        self.assertIn('pace target', R.end_reason(R.new_run('now', 0.0, None, now), now, 1.0, 10.0, 40.0, False))   # manual: ends at once

    def test_unlimited_never_hits_count(self):
        run = R.new_run('night', 0.0, None, T0)
        run['started'] = 500
        self.assertEqual(R.end_reason(run, T0, -5.0, 10.0, 40.0, False), '')


class Runs(Base):
    def make(self, when=T0, items=3, **o):
        return super().make(when, FakeHA(todo(items)), **o)

    def tick(self, **kw):
        self.c.move(**kw) if kw else None
        self.svc.tick()
        return self.svc.load_state()

    def test_night_run_starts_at_window_and_only_once(self):
        self.make()
        st = self.tick()
        self.assertEqual(st['run']['kind'], 'night')
        self.assertEqual(st['run']['stop_by_at'], '2026-11-05T05:30:00+00:00')
        self.svc.run_stop()
        st = self.tick(minutes=1)
        self.assertNotIn('run', st)           # that window already ran
        self.assertEqual(st['run_last']['reason'], 'stopped by the user')

    def test_no_run_outside_window_or_when_disabled(self):
        self.make(when=T0.replace(hour=12))
        self.assertNotIn('run', self.tick())
        self.make()
        self.svc.set_run_settings({'night': {'enabled': False}})
        self.assertNotIn('run', self.tick())

    def test_day_run_off_by_default_and_runs_when_enabled(self):
        self.make(when=T0.replace(hour=11))
        self.assertNotIn('run', self.tick())
        self.svc.set_run_settings({'day': {'enabled': True}})
        self.assertEqual(self.tick()['run']['kind'], 'day')

    def test_dry_run_logs_and_counts_would_feed_then_ends_at_count(self):
        self.make(mode='dry')
        self.svc.set_run_settings({'night': {'max_items': 2}})
        self.tick()                                         # starts, feeds one (dry)
        self.assertEqual(self.svc.store.list(), [])         # dry: nothing queued
        self.assertTrue(any('[DRY] would feed: IH: job 0' in l for l in self.logs))
        self.tick(seconds=31)
        self.assertTrue(any('IH: job 1' in l for l in self.logs))
        st = self.tick(seconds=31)
        self.assertNotIn('run', st)
        self.assertIn('item count reached (2)', st['run_last']['reason'])
        self.assertFalse(any('job 2' in l for l in self.logs))

    def test_live_run_feeds_one_at_a_time_and_counts_claims(self):
        self.make(mode='live')
        self.svc.set_run_settings({'night': {'max_items': 2, 'pace_target': 0.0}})
        self.pace(-3.0)
        self.tick()
        q = self.svc.store.list()
        self.assertEqual([i['kind'] for i in q], ['todo'])
        self.tick(seconds=31)
        self.assertEqual(len(self.svc.store.list()), 1)     # still one: one at a time
        c = self.svc.claim('w1')
        self.assertTrue(c['claimed'])
        self.svc.store.finish(c['item']['id'], 'w1', True, summary='ok')
        self.tick(seconds=31)
        c = self.svc.claim('w1')
        self.assertTrue(c['claimed'])
        self.svc.store.finish(c['item']['id'], 'w1', True, summary='ok')
        st = self.tick(seconds=31)
        self.assertNotIn('run', st)
        self.assertIn('item count', st['run_last']['reason'])
        self.assertEqual(st['run_last']['started'], 2)
        self.assertEqual(len(self.svc.store.list()), 2)     # never fed a third

    def test_run_pace_target_replaces_band_levels(self):
        self.make(when=T0.replace(hour=12), mode='live')    # day band: would wait until pace is below -5
        self.add()
        self.pace(-0.5)
        c = self.svc.claim('w1')
        self.assertFalse(c['claimed'])
        self.assertIn('nothing runs', c['reason'])
        self.svc.run_start_now(0.0, 2)
        c = self.svc.claim('w1')
        self.assertTrue(c['claimed'])
        self.assertEqual(self.svc.load_state()['run']['started'], 1)

    def test_run_waits_then_ends_when_pace_reached_after_start(self):
        self.make(mode='live')
        self.pace(1.0)                                       # above target 0.0
        st = self.tick()
        self.assertIn('run', st)                             # waits, does not end
        self.assertEqual(self.svc.runs_view()['active']['state'], 'waiting')
        self.assertEqual(self.svc.store.list(), [])
        self.pace(-2.0)
        self.tick(seconds=31)
        self.assertEqual(len(self.svc.store.list()), 1)
        c = self.svc.claim('w1')
        self.svc.store.finish(c['item']['id'], 'w1', True)
        self.pace(0.2)
        st = self.tick(seconds=31)
        self.assertNotIn('run', st)
        self.assertIn('pace target reached', st['run_last']['reason'])

    def test_stop_by_ends_run(self):
        self.make()
        self.tick()
        st = self.tick(hours=4, minutes=31)
        self.assertNotIn('run', st)
        self.assertEqual(st['run_last']['reason'], 'stop-by time reached')

    def test_session_limit_ends_run(self):
        self.make()
        self.tick()
        self.r.v['session'] = 90.0
        self.svc._reading = (0, None, '')
        self.assertIn('5-hour session', self.tick(seconds=31)['run_last']['reason'])

    def test_stop_switch_ends_run(self):
        self.make()
        self.tick()
        self.svc.opts['stop'] = True
        self.assertIn('STOP', self.tick(seconds=31)['run_last']['reason'])

    def test_backlog_empty_ends_run(self):
        self.make(items=0)
        st = self.tick()
        self.assertNotIn('run', st)
        self.assertEqual(st['run_last']['reason'], 'backlog empty')

    def test_one_at_a_time_and_run_now(self):
        self.make(when=T0.replace(hour=12), mode='dry')
        v = self.svc.run_start_now(-1, 3)
        self.assertEqual(v['active']['kind'], 'now')
        self.assertEqual(v['active']['pace_target'], -1.0)
        with self.assertRaises(StoreError) as e:
            self.svc.run_start_now(0, None)
        self.assertEqual(e.exception.code, 'conflict')
        self.svc.set_run_settings({'day': {'enabled': True, 'start': '11:00', 'stop_by': '13:00'}})
        self.assertEqual(self.tick()['run']['kind'], 'now')   # a scheduled run does not replace the active one
        self.svc.run_stop()
        with self.assertRaises(StoreError):
            self.svc.run_stop()
        self.assertEqual(self.svc.runs_view()['last']['reason'], 'stopped by the user')

    def test_run_now_unlimited_and_validation(self):
        self.make(when=T0.replace(hour=12))
        self.assertIsNone(self.svc.run_start_now(-1, None)['active']['max_items'])
        self.svc.run_stop()
        for p, n in (('x', 1), (0, 0), (0, 5000), (999, 1)):
            with self.assertRaises(StoreError):
                self.svc.run_start_now(p, n)

    def test_run_now_ends_at_once_if_pace_not_below_target(self):
        self.make(when=T0.replace(hour=12))
        self.pace(2.0)
        self.svc.run_start_now(0, 2)
        self.assertIn('pace target reached', self.tick()['run_last']['reason'])

    def test_no_run_means_old_behaviour(self):
        self.make(when=T0.replace(hour=12))                  # day band, no run
        self.add()
        self.pace(-6.0)
        self.assertTrue(self.svc.claim('w1')['claimed'])
        self.assertEqual(self.svc.opts.get('feed_backlog'), False)
        self.assertEqual(self.svc.ha.calls, 0)

    def test_urgent_handoff_still_first(self):
        self.make(mode='live')
        self.add('x')
        self.svc.store.enqueue('other', 'handoff-1', 'urgent', 'go')
        self.svc.tick()
        c = self.svc.claim('w1')
        self.assertEqual(c['item']['ref'], 'handoff-1')

    def test_settings_persist_and_validate(self):
        self.make()
        self.svc.set_run_settings({'night': {'start': '02:30', 'max_items': 4, 'pace_target': -1.5}})
        s2 = Service(self.dir, opts(), self.r, None, self.c)       # a fresh service reads the file
        n = s2.run_settings()['night']
        self.assertEqual((n['start'], n['max_items'], n['pace_target'], n['enabled']), ('02:30', 4, -1.5, True))
        self.svc.set_run_settings({'night': {'max_items': 'unlimited'}})
        self.assertIsNone(s2.run_settings()['night']['max_items'])
        before = s2.run_settings()
        with self.assertRaises(StoreError):
            self.svc.set_run_settings({'night': {'start': 'nope'}})
        self.assertEqual(s2.run_settings(), before)
        with open(os.path.join(self.dir, 'run_settings.json'), 'w') as f:
            f.write('{broken')
        self.assertTrue(s2.run_settings()['night']['enabled'])     # corrupt file: option defaults

    def test_option_defaults_feed_settings(self):
        self.make(runs_options={'night_run_start': '03:00', 'night_run_max_items': 2, 'day_run_enabled': True})
        s = self.svc.run_settings()
        self.assertEqual((s['night']['start'], s['night']['max_items'], s['day']['enabled']), ('03:00', 2, True))

    def test_load_options_collects_run_options(self):
        p = os.path.join(tempfile.mkdtemp(), 'options.json')
        with open(p, 'w') as f:
            json.dump({'night_run_start': '02:00', 'day_run_enabled': True, 'other': 1}, f)
        self.assertEqual(M.load_options(p)['runs_options'], {'night_run_start': '02:00', 'day_run_enabled': True})


class Http(Base):
    def setUp(self):
        self.make(ha=FakeHA(todo(2)), mode='dry')
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), M.make_handler(self.svc, lambda: TOKEN))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:%d' % self.srv.server_address[1]
        os.environ['IHW_DATA'] = self.dir
        with open(os.path.join(self.dir, 'options.json'), 'w') as f:
            json.dump({'api_token': TOKEN}, f)
        U.DATA, U.PEER = self.dir, '127.0.0.1'
        os.environ['IHW_API'] = self.base
        self.ui = ThreadingHTTPServer(('127.0.0.1', 0), U.Handler)
        threading.Thread(target=self.ui.serve_forever, daemon=True).start()
        self.uib = 'http://127.0.0.1:%d' % self.ui.server_address[1]

    def tearDown(self):
        self.srv.shutdown(); self.ui.shutdown()
        os.environ.pop('IHW_API', None)

    def call(self, base, path, body=None, token=TOKEN, ctype='application/json'):
        h = {'Content-Type': ctype}
        if token:
            h['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=h, method='GET' if body is None else 'POST')
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b'null')
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw

    def test_api_needs_token_and_roundtrip(self):
        self.assertEqual(self.call(self.base, '/runs', token='')[0], 401)
        self.assertEqual(self.call(self.base, '/runs/stop', {}, token='')[0], 401)
        code, v = self.call(self.base, '/runs')
        self.assertEqual(code, 200)
        self.assertTrue(v['settings']['night']['enabled'])
        self.assertNotIn(TOKEN, json.dumps(v))
        code, v = self.call(self.base, '/runs/settings', {'day': {'enabled': True, 'pace_target': -3}})
        self.assertEqual((code, v['settings']['day']['pace_target']), (200, -3.0))
        self.assertEqual(self.call(self.base, '/runs/settings', {'day': {'start': 'zz'}})[0], 400)
        self.assertEqual(self.call(self.base, '/runs/settings', {'week': {}})[0], 400)
        self.assertEqual(self.call(self.base, '/runs/start', {'pace_target': 0})[0], 400)    # max_items required
        code, v = self.call(self.base, '/runs/start', {'pace_target': -1, 'max_items': None})
        self.assertEqual((code, v['active']['kind']), (200, 'now'))
        self.assertEqual(self.call(self.base, '/runs/start', {'pace_target': -1, 'max_items': 1})[0], 409)
        code, v = self.call(self.base, '/runs/stop', {})
        self.assertEqual((code, v['active']), (200, None))
        self.assertEqual(self.call(self.base, '/runs/stop', {})[0], 409)

    def test_ui_proxy_endpoints(self):
        code, v = self.call(self.uib, '/api/runs')
        self.assertEqual(code, 200)
        self.assertIn('settings', v)
        self.assertNotIn(TOKEN, json.dumps(v))
        code, v = self.call(self.uib, '/api/runs/start', {'pace_target': -2, 'max_items': 3}, token='')
        self.assertEqual((code, v['active']['max_items']), (200, 3))
        self.assertEqual(self.call(self.uib, '/api/runs/start', {'pace_target': -2, 'max_items': 3}, token='')[0], 409)
        self.assertEqual(self.call(self.uib, '/api/runs/settings', {'night': {'max_items': 'unlimited'}}, token='')[0], 200)
        self.assertEqual(self.call(self.uib, '/api/runs/stop', {}, token='')[0], 200)
        self.assertEqual(self.call(self.uib, '/api/runs/settings', {'night': {'start': 'x'}}, token='')[0], 400)
        self.assertEqual(self.call(self.uib, '/api/runs/settings', {}, token='', ctype='text/plain')[0], 415)
        self.assertEqual(self.call(self.uib, '/api/queue', {}, token='')[0], 404)       # only the run calls are proxied

    def test_ui_refuses_other_peers_and_serves_assets(self):
        U.PEER = '10.9.9.9'
        self.assertEqual(self.call(self.uib, '/api/runs')[0], 403)
        self.assertEqual(self.call(self.uib, '/api/runs/stop', {}, token='')[0], 403)
        U.PEER = '127.0.0.1'
        for a in ('/runs.js', '/runs.css'):
            req = urllib.request.urlopen(self.uib + a, timeout=5)
            self.assertEqual(req.status, 200)


if __name__ == '__main__':
    unittest.main()
