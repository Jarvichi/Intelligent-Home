"""Service (gate-guarded claim, test mode, state, feeder) and the HTTP API (token, unknown fields, no secrets)."""
import json, os, sys, tempfile, threading, unittest, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import gate as G, feed, main as M, pace
from service import Service
from store import StoreError
from tasks_client import TasksError, TasksClient

NIGHT = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)     # tz = UTC in tests: 02:00 is night
DAY = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
TOKEN = 'tok-SECRET-123'


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def move(self, **kw):
        self.t += timedelta(**kw)


class Reader:
    def __init__(self, pace_=-6.0, session=10.0, week=40.0):
        self.v = dict(pace=pace_, session=session, week=week, elapsed=50.0, reset='x')
        self.fail = False

    def read(self, now=None):
        if self.fail:
            raise ValueError('secret detail ' + TOKEN)
        return dict(self.v)


def opts(**k):
    o = dict(mode='live', api_token=TOKEN, lease_s=1200, stop=False, bands=G.DEFAULT_BANDS, tz=timezone.utc, feed_backlog=False,
             feed_prefixes=['IH:', 'Check:', 'Review:'])
    o.update(k)
    return o


class Base(unittest.TestCase):
    def make(self, when=DAY, ha=None, **o):
        self.dir = tempfile.mkdtemp()
        self.c = Clock(when)
        self.r = Reader()
        self.logs = []
        self.svc = Service(self.dir, opts(**o), self.r, ha, self.c, self.logs.append)
        return self.svc

    def add(self, ref='1'):
        return self.svc.store.enqueue('other', ref, 't' + ref, 'do it')

    def pace(self, v):
        self.r.v['pace'] = v
        self.svc._reading = (0, None, '')


class Claim(Base):
    def test_claims_when_gate_allows(self):
        self.make(); a = self.add()
        r = self.svc.claim('w1')
        self.assertTrue(r['claimed'])
        self.assertEqual((r['item']['id'], r['item']['claimed_by']), (a['id'], 'w1'))
        self.assertIn('day band', r['reason'])

    def test_refusal_reasons_are_plain(self):
        self.make(); self.assertEqual(self.svc.claim('w')['reason'], 'queue empty')
        self.add(); self.pace(-3.2)
        r = self.svc.claim('w')
        self.assertFalse(r['claimed'])
        self.assertTrue(r['reason'].startswith('day band: pace -3.2, above -4, nothing runs'))
        self.assertIn('pace below -5 in about', r['reason'])
        self.assertEqual(self.svc.store.list()[0]['status'], 'queued')

    def test_night_band_allows_closer(self):
        self.make(NIGHT); self.add(); self.pace(-1.5)
        self.assertTrue(self.svc.claim('w')['claimed'])

    def test_one_at_a_time_and_stop(self):
        self.make(); self.add('1'); self.add('2')
        self.svc.claim('w')
        self.assertEqual(self.svc.claim('w2')['reason'], 'an item is already running')
        open(os.path.join(self.dir, 'STOP'), 'w').close()
        self.assertIn('STOP', self.svc.claim('w3')['reason'])

    def test_stop_option(self):
        self.make(stop=True); self.add()
        self.assertFalse(self.svc.claim('w')['claimed'])

    def test_hourly_limit(self):
        self.make()
        for n in range(4):
            self.add(str(n))
        for n in range(3):
            r = self.svc.claim('w')
            self.assertTrue(r['claimed'], n)
            self.svc.store.finish(r['item']['id'], 'w', True)
            self.c.move(minutes=1)
        r = self.svc.claim('w')
        self.assertTrue(r['claimed'])        # 0.1.1: no hourly item cap, pace decides
        self.svc.store.finish(r['item']['id'], 'w', True)

    def test_drain_stops_at_band_stop(self):
        self.make()
        for n in range(3):
            self.add(str(n))
        r = self.svc.claim('w'); self.svc.store.finish(r['item']['id'], 'w', True)
        self.pace(-4.5)
        r = self.svc.claim('w'); self.assertTrue(r['claimed']); self.svc.store.finish(r['item']['id'], 'w', True)
        self.pace(-3.9)
        self.assertFalse(self.svc.claim('w')['claimed'])
        self.assertEqual(self.svc.load_state()['mode'], 'idle')

    def test_night_into_day_reevaluates(self):
        self.make(datetime(2026, 11, 5, 5, 50, tzinfo=timezone.utc))
        for n in range(2):
            self.add(str(n))
        self.pace(-2.0)
        r = self.svc.claim('w'); self.assertTrue(r['claimed']); self.svc.store.finish(r['item']['id'], 'w', True)
        self.assertEqual(self.svc.load_state()['mode'], 'draining')
        self.c.move(minutes=15)                     # 06:05: day band
        r = self.svc.claim('w')
        self.assertFalse(r['claimed'])
        self.assertTrue(r['reason'].startswith('day band'))

    def test_session_limit(self):
        self.make(); self.add(); self.r.v['session'] = 90.0
        self.assertIn('5-hour session', self.svc.claim('w')['reason'])

    def test_sensor_failure_blocks_and_hides_detail(self):
        self.make(); self.add(); self.r.fail = True; self.svc._reading = (0, None, '')
        r = self.svc.claim('w')
        self.assertFalse(r['claimed'])
        self.assertIn('could not be read (ValueError)', r['reason'])
        self.assertNotIn(TOKEN, json.dumps(r))

    def test_dry_mode_hands_out_nothing(self):
        self.make(mode='dry'); a = self.add()
        r = self.svc.claim('w')
        self.assertEqual((r['claimed'], r['dry']), (False, True))
        self.assertEqual(r['would_claim']['id'], a['id'])
        self.assertEqual(self.svc.store.get(a['id'])['status'], 'queued')
        self.assertTrue(any('[DRY] would hand out' in l for l in self.logs))
        for _ in range(5):
            self.assertTrue(self.svc.claim('w')['dry'])      # no item cap in dry mode either

    def handoff(self, n='1'):
        return self.svc.store.enqueue('other', 'handoff-clara-2026110810%s' % n.zfill(4), 'Handoff from clara: x', 'do it')

    def test_handoff_skips_pace_pause_and_goes_first(self):
        self.make(); self.add('plain'); h = self.handoff(); self.pace(5.0)          # gate closed for ordinary items
        r = self.svc.claim('w')
        self.assertTrue(r['claimed']); self.assertEqual(r['item']['id'], h['id']); self.assertIn('urgent handoff', r['reason'])

    def test_ordinary_items_stay_gated_when_a_handoff_is_not_waiting(self):
        self.make(); self.add('plain'); self.pace(5.0)
        self.assertFalse(self.svc.claim('w')['claimed'])

    def test_handoff_still_obeys_stop_session_week_and_daily_cap(self):
        self.make(); self.handoff(); self.pace(5.0)
        self.r.v['session'] = 90.0; self.svc._reading = (0, None, '')
        self.assertIn('session', self.svc.claim('w')['reason'])
        self.r.v['session'] = 10.0; self.r.v['week'] = 95.0; self.svc._reading = (0, None, '')
        self.assertIn('week', self.svc.claim('w')['reason'])
        self.r.v['week'] = 40.0; self.svc._reading = (0, None, '')
        self.svc.URGENT_PER_DAY = 2
        for n in ('1', '2'):
            self.handoff(n); r = self.svc.claim('w'); self.assertTrue(r['claimed'], r); self.svc.store.finish(r['item']['id'], 'w', True)
        self.handoff('9'); self.assertIn('handoffs in the last 24 hours', self.svc.claim('w')['reason'])

    def test_handoff_in_stop_mode_waits(self):
        self.make(stop=True); self.handoff()
        self.assertFalse(self.svc.claim('w')['claimed'])

    def test_override_is_python_only(self):
        self.make(); self.add(); self.pace(5.0)
        self.assertFalse(self.svc.claim('w')['claimed'])
        self.assertTrue(self.svc.claim('w', override=True)['claimed'])

    def test_expired_lease_frees_the_slot(self):
        self.make(); self.add(); self.svc.claim('w1', 60)
        self.c.move(minutes=2); self.pace(-6.0)
        r = self.svc.claim('w2')
        self.assertTrue(r['claimed'])
        self.assertEqual(r['item']['attempts'], 1)

    def test_status_shape(self):
        self.make(); self.add('1'); self.add('2'); self.svc.claim('w')
        s = self.svc.status()
        self.assertEqual(s['counts'], {'queued': 1, 'running': 1, 'done': 0, 'failed': 0, 'cancelled': 0, 'waiting': 0})
        self.assertEqual(s['running']['claimed_by'], 'w')
        self.assertEqual(s['next']['title'], 't2')
        self.assertEqual(s['worker']['state'], 'working')
        for k in ('state', 'reason', 'mode', 'updated'):
            self.assertIn(k, s['worker'])
        for k in ('gate', 'gate_at', 'last_finished', 'corrupt_files'):
            self.assertIn(k, s)
        self.assertTrue(s['gate'].startswith('queue gate: '))

    def test_tick_writes_snapshot_and_mode(self):
        self.make(); self.add()
        self.svc.tick()
        st = self.svc.load_state()
        self.assertEqual(st['mode'], 'draining')
        self.assertEqual(st['snapshot']['worker']['state'], 'ready')
        self.assertEqual(st['snapshot']['worker']['band'], 'day')
        self.pace(-3.0); self.c.move(seconds=40); self.svc._reading = (0, None, '')
        self.svc.tick()
        self.assertEqual(self.svc.load_state()['mode'], 'idle')

    def test_tick_survives_reader_failure(self):
        self.make(); self.add(); self.r.fail = True; self.svc._reading = (0, None, '')
        self.svc.tick()
        self.assertEqual(self.svc.load_state()['snapshot']['worker']['state'], 'waiting for pace')


class FakeHA:
    """A fake IH Tasks client: approved() returns the tasks, move() records the stage moves."""
    def __init__(self, items):
        self.items, self.calls, self.moves, self.down = items, 0, [], False

    def approved(self):
        self.calls += 1
        if self.down:
            raise TasksError('IH Tasks could not be reached (URLError)')
        return self.items

    def move(self, number, to, reason='', result=''):
        self.moves.append((number, to, reason, result))
        return ''


def todo(uid, summary, desc='', due=None):
    return {'uid': uid, 'summary': summary, 'description': desc, 'due': due, 'status': 'needs_action'}


class Feeder(Base):
    ITEMS = [todo('a', 'Buy milk'), todo('b', 'IH: later job', due='2026-12-01'), todo('c', 'IH: sooner job', due='2026-11-10'),
             todo('d', 'Check: waiting thing', '[waiting]'), todo('e', 'IH: plan it', '[plan]'), todo('f', 'IH: needs ok', '[needs-ok #3]'),
             todo('g', 'Misc tagged', '[claude]'), todo('h', 'IH: approved one', '[approved #4]', due='2026-12-30')]

    def test_instruction_asks_claude_for_a_missing_plan(self):
        self.assertIn('NO PLAN YET', feed.instruction(todo('a', 'Check: x', '')))
        self.assertNotIn('NO PLAN YET', feed.instruction(todo('a', 'Check: x', 'PLAN (unreviewed): brain projects/x/plan')))
        self.assertNotIn('NO PLAN YET', feed.instruction(todo('a', 'Check: x', 'AI job 12')))

    def test_pick_rules(self):
        now = DAY
        item, ap = feed.pick(self.ITEMS, ['IH:', 'Check:'], {}, now)
        self.assertEqual((item['uid'], ap), ('h', True))                         # approved first
        item, ap = feed.pick([i for i in self.ITEMS if i['uid'] != 'h'], ['IH:', 'Check:'], {}, now)
        self.assertEqual((item['uid'], ap), ('c', False))                        # earliest due
        item, _ = feed.pick([todo('a', 'Buy milk'), todo('d', 'Check: x', '[waiting]')], ['IH:'], {}, now, any_item=True)   # runs: any open item, skip tags still apply
        self.assertEqual(item['uid'], 'a')
        self.assertIsNone(feed.pick([todo('d', 'Check: x', '[waiting]')], ['IH:'], {}, now, any_item=True)[0])
        item, _ = feed.pick([todo('a', 'Buy milk'), todo('d', 'Check: x', '[waiting]')], ['IH:'], {}, now)
        self.assertIsNone(item)                                                  # personal / waiting never
        item, _ = feed.pick(self.ITEMS, ['IH:'], {'h': (now - timedelta(days=6)).isoformat(), 'c': now.isoformat()}, now)
        self.assertEqual(item['uid'], 'b')                                       # fed within 7 days skipped
        item, _ = feed.pick(self.ITEMS, ['IH:'], {'h': (now - timedelta(days=8)).isoformat()}, now)
        self.assertEqual(item['uid'], 'h')

    def test_off_by_default(self):
        ha = FakeHA(self.ITEMS); self.make(ha=ha); self.svc.tick()
        self.assertEqual((ha.calls, len(self.svc.store.list())), (0, 0))

    def test_dry_only_logs(self):
        ha = FakeHA(self.ITEMS); self.make(ha=ha, feed_backlog=True, mode='dry'); self.svc.tick()
        self.assertEqual(len(self.svc.store.list()), 0)
        self.assertTrue(any(l.startswith('[DRY] would feed: IH: approved one') for l in self.logs))
        self.svc.tick(); self.assertEqual(ha.calls, 1)                           # throttled

    def test_live_enqueues_one_then_never_twice(self):
        ha = FakeHA(self.ITEMS); self.make(ha=ha, feed_backlog=True); self.svc.tick()
        items = self.svc.store.list()
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]['kind'], items[0]['ref'], items[0]['approved']), ('todo', 'h', True))
        self.svc.store.cancel(items[0]['id'])
        self.c.move(minutes=10); self.svc.tick()                                 # queue empty again: next item, not 'h'
        self.assertEqual([i['ref'] for i in self.svc.store.list(('queued',))], ['c'])

    def test_not_fed_when_gate_closed_or_queue_busy(self):
        ha = FakeHA(self.ITEMS); self.make(ha=ha, feed_backlog=True); self.pace(-2.0); self.svc.tick()
        self.assertEqual(ha.calls, 0)
        self.pace(-6.0); self.svc._reading = (0, None, ''); self.add('x'); self.svc.tick()
        self.assertEqual(ha.calls, 0)


class FromIHTasks(Base):
    def test_unreachable_hands_out_nothing_and_warns(self):
        t = FakeHA([dict(todo('7', 'Task seven', 'plan', due='2026-12-01'), approved=True)]); t.down = True
        self.make(ha=t, feed_backlog=True); self.svc.tick()
        self.assertEqual(len(self.svc.store.list()), 0)
        self.assertTrue(any('no tasks handed out' in l for l in self.logs))
        self.assertIn('could not be reached', self.svc.status()['tasks_warning'])
        t.down = False; self.c.move(minutes=10); self.svc.tick()                 # back up: the warning clears and the task is fed
        self.assertEqual(self.svc.status()['tasks_warning'], '')
        self.assertEqual([i['ref'] for i in self.svc.store.list()], ['7'])

    def test_not_configured_hands_out_nothing(self):
        self.make(ha=None, feed_backlog=True); self.svc.tick()
        self.assertEqual(len(self.svc.store.list()), 0)
        self.assertIn('not configured', self.svc.status()['tasks_warning'])

    def test_task_is_the_plan_and_needs_no_tags(self):
        t = FakeHA([todo('7', 'Task seven', 'plan text [waiting]')])             # old list tags do not apply to IH Tasks tasks
        t.items[0]['approved'] = True
        self.make(ha=t, feed_backlog=True); self.svc.tick()
        it = self.svc.store.list()[0]
        self.assertEqual((it['ref'], it['approved']), ('7', True))
        self.assertIn('IH Task #7', it['instruction']); self.assertIn('plan text', it['instruction'])

    def test_stage_moves(self):
        t = FakeHA([dict(todo('7', 'Task seven', 'plan'), approved=True)])
        self.make(ha=t, feed_backlog=True); self.svc.tick()
        got = self.svc.claim('w'); self.assertTrue(got['claimed'], got)
        self.assertEqual(t.moves, [(7, 'implementing', '', '')])
        it = self.svc.store.finish(got['item']['id'], 'w', True, summary='built it'); self.svc.finished(it, True, 'built it')
        self.assertEqual(t.moves[-1], (7, 'verifying', '', 'built it'))

    def test_failure_and_stop_go_back_to_planning(self):
        t = FakeHA([]); self.make(ha=t)
        it = dict(kind='todo', ref='9')
        self.svc.finished(it, False, 'boom'); self.svc.waiting(it, 'needs_ok', 'need a decision')
        self.assertEqual([(m[0], m[1]) for m in t.moves], [(9, 'planning'), (9, 'planning')])
        self.assertIn('boom', t.moves[0][2]); self.assertIn('needs_ok', t.moves[1][2])
        self.svc.finished(dict(kind='review', ref='9'), True); self.svc.finished(dict(kind='todo', ref='abc'), True)   # not tasks: no move
        self.assertEqual(len(t.moves), 2)


class _Resp:
    def __init__(self, obj): self.b = json.dumps(obj).encode()
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Client(unittest.TestCase):
    def fake(self, routes):
        self.seen = []
        def opener(req, timeout=0):
            self.seen.append((req.get_method(), req.full_url, req.headers.get('Authorization'), req.data))
            r = routes.get((req.get_method(), req.full_url.split('/tasks', 1)[1] if '/tasks' in req.full_url else ''))
            if isinstance(r, Exception):
                raise r
            return _Resp(r)
        return TasksClient('http://t:1/', 'secret-token', opener)

    def test_only_approved_with_plan_text(self):
        c = self.fake({('GET', '?stage=approved'): {'tasks': [dict(id=3, stage='approved'), dict(id=4, stage='approved', needs_input=True), dict(id=5, stage='todo')]},
                       ('GET', '/3'): dict(id=3, stage='approved', title='T3', plan='the plan', due='2026-12-01', priority='high')})
        items = c.approved()
        self.assertEqual(items, [dict(uid='3', summary='T3', description='the plan', due='2026-12-01', priority='high', approved=True)])
        self.assertEqual(self.seen[0][1], 'http://t:1/tasks?stage=approved')
        self.assertEqual(self.seen[0][2], 'Bearer secret-token')

    def test_task_moved_on_since_is_skipped(self):
        c = self.fake({('GET', '?stage=approved'): {'tasks': [dict(id=3, stage='approved')]}, ('GET', '/3'): dict(id=3, stage='implementing', title='T')})
        self.assertEqual(c.approved(), [])

    def test_errors_become_tasks_error_without_secrets(self):
        c = self.fake({('GET', '?stage=approved'): urllib.error.URLError('boom secret-token')})
        with self.assertRaises(TasksError) as cm:
            c.approved()
        self.assertNotIn('secret-token', str(cm.exception))
        self.assertIn('could not be reached', str(cm.exception))
        with self.assertRaises(TasksError):
            TasksClient('', '').approved()
        c = self.fake({('GET', '?stage=approved'): {'tasks': 'bad'}})
        with self.assertRaises(TasksError):
            c.approved()

    def test_move_posts_and_never_raises(self):
        c = self.fake({('POST', '/3/move'): {'ok': 1}})
        self.assertEqual(c.move(3, 'verifying', result='done it'), '')
        self.assertEqual(json.loads(self.seen[0][3]), {'to': 'verifying', 'result': 'done it'})
        c = self.fake({('POST', '/3/move'): urllib.error.HTTPError('u', 409, 'x', {}, None)})
        self.assertIn('409', c.move(3, 'verifying'))


class Options(unittest.TestCase):
    def test_defaults_and_bad_values(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'o.json')
        o = M.load_options(p)
        self.assertEqual((o['mode'], o['api_token'], o['lease_s'], o['feed_backlog']), ('dry', '', 1200, False))
        self.assertEqual((o['tasks_api_url'], o['tasks_api_token']), ('', ''))
        self.assertNotIn('todo_entity', o)
        self.assertEqual(o['bands'], G.DEFAULT_BANDS)
        with open(p, 'w') as f:
            json.dump({'mode': 'weird', 'day_start': 'x', 'lease_minutes': 9999, 'timezone': 'No/Where', 'feed_prefixes': 'str'}, f)
        o = M.load_options(p, log=lambda *a: None)
        self.assertEqual((o['mode'], o['bands'].day_start, o['lease_s']), ('dry', -5.0, 7200))
        self.assertEqual(o['feed_prefixes'], M.DEFAULT_PREFIXES)


class Pace(unittest.TestCase):
    R = datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc)

    def test_compute(self):
        self.assertEqual(pace.compute(50, self.R, self.R - pace.WEEK / 2), (0.0, 50.0))
        self.assertEqual(pace.compute(42, self.R, self.R - pace.WEEK + timedelta(days=4.2)), (-18.0, 60.0))
        self.assertEqual(pace.compute(90, self.R, self.R + timedelta(hours=3)), (-10.0, 100.0))

    def test_reader_auto_discovers(self):
        class HA:
            def get(self, p, t=30):
                if p == 'states':
                    return [{'entity_id': 'sensor.claude_usage_x_week_usage'}, {'entity_id': 'sensor.claude_usage_x_session_usage'},
                            {'entity_id': 'sensor.claude_usage_x_weekly_reset_time'}, {'entity_id': 'sensor.claude_usage_x_limit_usage'}]
                return {'state': {'sensor.claude_usage_x_week_usage': '30', 'sensor.claude_usage_x_session_usage': '12',
                                  'sensor.claude_usage_x_weekly_reset_time': (Pace.R).isoformat()}[p.split('/', 1)[1]]}
        r = pace.PaceReader(HA()).read(Pace.R - pace.WEEK / 2)
        self.assertEqual((r['pace'], r['week'], r['session']), (-20.0, 30.0, 12.0))

    def test_reader_unreadable(self):
        class HA:
            def get(self, p, t=30):
                return [] if p == 'states' else {'state': 'unavailable'}
        with self.assertRaises(ValueError):
            pace.PaceReader(HA(), 'sensor.a', 'sensor.b', 'sensor.c').read()


class Api(Base):
    def setUp(self):
        self.make(); self.pace(-6.0)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), M.make_handler(self.svc, lambda: self.svc.opts['api_token']))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()

    def call(self, method, path, body=None, token=TOKEN):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), method=method, data=None if body is None else json.dumps(body).encode())
        if token is not None:
            req.add_header('Authorization', 'Bearer ' + token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b'null') if r.headers.get('Content-Type', '').startswith('application/json') else r.read()
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    ENQ = {'kind': 'other', 'ref': 'r1', 'title': 'T', 'instruction': 'I'}

    def test_token_required(self):
        for m, p in [('GET', '/status'), ('GET', '/list'), ('GET', '/item/x'), ('POST', '/enqueue'), ('POST', '/claim')]:
            self.assertEqual(self.call(m, p, {} if m == 'POST' else None, token=None)[0], 401, p)
            self.assertEqual(self.call(m, p, {} if m == 'POST' else None, token='wrong')[0], 401, p)
        self.assertEqual(self.call('GET', '/healthz', token=None)[0], 200)

    def test_empty_token_refuses_everything(self):
        self.svc.opts['api_token'] = ''
        self.assertEqual(self.call('GET', '/status', token='')[0], 401)
        self.assertEqual(self.call('GET', '/status', token=None)[0], 401)
        self.assertEqual(self.call('GET', '/healthz', token=None)[0], 200)

    def test_full_flow(self):
        code, it = self.call('POST', '/enqueue', self.ENQ)
        self.assertEqual(code, 200)
        self.assertEqual(self.call('POST', '/enqueue', self.ENQ)[1]['id'], it['id'])
        self.assertEqual(self.call('GET', '/item/' + it['id'])[1]['status'], 'queued')
        self.assertEqual(len(self.call('GET', '/list?status=queued')[1]['items']), 1)
        code, cl = self.call('POST', '/claim', {'consumer': 'runner'})
        self.assertTrue(cl['claimed'])
        self.assertEqual(self.call('POST', '/heartbeat', {'id': it['id'], 'consumer': 'runner'})[1]['status'], 'running')
        self.assertEqual(self.call('POST', '/complete', {'id': it['id'], 'consumer': 'other'})[0], 403)
        code, done = self.call('POST', '/complete', {'id': it['id'], 'consumer': 'runner', 'summary': 'ok'})
        self.assertEqual((code, done['status'], done['result_summary']), (200, 'done', 'ok'))
        self.assertEqual(self.call('POST', '/complete', {'id': it['id'], 'consumer': 'runner'})[0], 409)
        s = self.call('GET', '/status')[1]
        self.assertEqual(s['counts']['done'], 1)
        self.assertEqual(s['last_finished']['id'], it['id'])

    def test_fail_cancel_retry(self):
        _, a = self.call('POST', '/enqueue', self.ENQ)
        self.call('POST', '/claim', {'consumer': 'r'})
        self.assertEqual(self.call('POST', '/fail', {'id': a['id'], 'consumer': 'r', 'error': 'x'})[1]['status'], 'failed')
        self.assertEqual(self.call('POST', '/retry', {'id': a['id']})[1]['status'], 'queued')
        self.assertEqual(self.call('POST', '/cancel', {'id': a['id']})[1]['status'], 'cancelled')
        self.assertEqual(self.call('POST', '/cancel', {'id': 'zzz'})[0], 404)

    def test_unknown_fields_and_bad_input(self):
        self.assertEqual(self.call('POST', '/enqueue', dict(self.ENQ, extra=1))[0], 400)
        self.assertEqual(self.call('POST', '/claim', {'consumer': 'r', 'override': True})[0], 400)    # no bypass over the API
        self.assertEqual(self.call('POST', '/claim', {})[0], 400)
        self.assertEqual(self.call('POST', '/claim', {'consumer': 'r', 'lease_s': 5})[0], 400)
        self.assertEqual(self.call('POST', '/enqueue', dict(self.ENQ, title='x' * 300))[0], 400)
        self.assertEqual(self.call('GET', '/list?status=bogus')[0], 400)
        self.assertEqual(self.call('GET', '/nothing')[0], 404)
        self.assertEqual(self.call('POST', '/nothing', {})[0], 404)

    def test_override_over_api_cannot_bypass_gate(self):
        self.pace(3.0)
        self.call('POST', '/enqueue', self.ENQ)
        code, r = self.call('POST', '/claim', {'consumer': 'r'})
        self.assertEqual((code, r['claimed']), (200, False))
        self.assertIn('nothing runs', r['reason'])

    def test_no_secrets_in_output(self):
        self.r.fail = True; self.svc._reading = (0, None, '')
        self.call('POST', '/enqueue', self.ENQ)
        blob = json.dumps([self.call('GET', '/status')[1], self.call('POST', '/claim', {'consumer': 'r'})[1], self.call('GET', '/list')[1]])
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn('secret detail', blob)
        self.assertNotIn('api_token', blob)


if __name__ == '__main__':
    unittest.main()
