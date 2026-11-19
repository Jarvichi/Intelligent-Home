"""Hearth (shared household assistant): bedtime message, awake gate, two-step check, Picard welcome, events, wiring. Fake HA, fake speaker, fake clock."""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import hearth as hm   # noqa: E402
import main as appmain  # noqa: E402

UTC = datetime.timezone.utc


def T(h, m=0, day=18):
    return datetime.datetime(2026, 11, day, h, m, tzinfo=UTC)


class Clock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, n):
        self.t += datetime.timedelta(seconds=n)
        if getattr(self, 'on_sleep', None):
            self.on_sleep(n)


class FakeHA:
    def __init__(self, clock):
        self.c, self.s, self.cal, self.cal_fail = clock, {}, {}, False

    def set(self, e, state, ago_s=3600, **attrs):
        self.s[e] = {'state': state, 'attributes': attrs, 'last_changed': (self.c.t - datetime.timedelta(seconds=ago_s)).isoformat()}

    def get(self, path, timeout=30):
        return self.s[path.split('/', 1)[1]]

    def calendar_events(self, entity, start, end):
        if self.cal_fail:
            raise RuntimeError('boom')
        return [e for e in self.cal.get(entity, []) if self._in(e, start, end)]

    @staticmethod
    def _in(e, start, end):
        s = e['start'].get('dateTime') or e['start'].get('date')
        d = datetime.datetime.fromisoformat(s if 'T' in s else s + 'T00:00:00+00:00')
        return start <= d < end


class FakeSpeaker:
    def __init__(self, status='spoken'):
        self.calls, self.status = [], status

    def __call__(self, req):
        self.calls.append(req)
        return {'status': 'dry' if req.get('dry_run') else self.status, 'reason': ''}


HEARTH = {
    'enabled': True, 'persona': 'hearth', 'speaker': 'bedroom', 'voice': 'hearth', 'alarm_entity': 'alarm_control_panel.ids',
    'bedroom_lamps': ['light.lamp_a', 'light.lamp_b'],
    'locks': ['lock.front=the front door', 'lock.utility=the utility door'],
    'doors': ['binary_sensor.front=the front door', 'binary_sensor.shed=the shed door'],
    'lock_door_pairs': ['lock.front=binary_sensor.front'],
    'lights_downstairs': ['light.hall', 'light.lounge'], 'lights_outside': ['light.porch'],
    'dishwasher_state_entity': 'sensor.dw', 'dishwasher_unload_entity': 'input_boolean.dw_unload',
    'forecast_entity': 'sensor.forecast',
    'workday_calendar': 'calendar.work', 'own_calendar': 'calendar.own', 'event_calendars': ['calendar.family', 'calendar.own'],
    'wake_up_entity': 'input_datetime.wake',
    'picard': {'enabled': True, 'door_entity': 'binary_sensor.front', 'person_switch': 'switch.p_home', 'person_name': 'Pat', 'speaker': 'kitchen', 'lines': ['Hello {name}.']},
}


def make(live=True, now=T(22, 30), speaker=None, store=None):
    clk = Clock(now)
    ha = FakeHA(clk)
    for e, st in (('light.lamp_a', 'off'), ('light.lamp_b', 'off'), ('lock.front', 'locked'), ('lock.utility', 'locked'), ('binary_sensor.front', 'off'),
                  ('binary_sensor.shed', 'off'), ('light.hall', 'off'), ('light.lounge', 'off'), ('light.porch', 'off'), ('sensor.dw', 'bsh.Running'),
                  ('input_boolean.dw_unload', 'off'), ('switch.p_home', 'off')):
        ha.set(e, st)
    ha.set('light.lamp_a', 'on', ago_s=60)
    ha.set('sensor.forecast', 'x', daily=[{'templow': 6.0}])
    ha.set('input_datetime.wake', '06:45:00')
    ha.cal['calendar.work'] = [{'summary': 'Work', 'start': {'date': '2026-11-19'}}]
    spk = speaker or FakeSpeaker()
    cfg = hm.parse_options(HEARTH)
    h = hm.Hearth(ha, cfg, spk, clk.now, clk.sleep, lambda *a: None, lambda: live, store=store)
    return h, ha, spk, clk


class Options(unittest.TestCase):
    def test_parse(self):
        c = hm.parse_options(HEARTH)
        self.assertEqual(c['locks'][0], ('lock.front', 'the front door'))
        self.assertEqual(c['lock_door_pairs'], [('lock.front', 'binary_sensor.front')])
        self.assertEqual(c['picard']['home_minutes'], 10)
        self.assertEqual((c['picard']['window_start'], c['picard']['min_gap_min']), ('07:00', 60))

    def test_bad_values_are_dropped_and_off_by_default(self):
        c = hm.parse_options({'bedroom_lamps': ['bad entity', 'light.ok'], 'locks': ['nonsense', 'lock.a=x'], 'off_pattern': '(', 'frost_at': 'cold'})
        self.assertFalse(c['enabled'])
        self.assertEqual(c['bedroom_lamps'], ['light.ok'])
        self.assertEqual(c['locks'], [('lock.a', 'x')])
        self.assertEqual(c['off_pattern'], hm.OFF_PATTERN)
        self.assertEqual(c['frost_at'], 2.0)
        self.assertFalse(hm.parse_options(None)['picard']['enabled'])


class Awake(unittest.TestCase):
    def test_lamp_on(self):
        self.assertTrue(make()[0].awake())

    def test_off_less_than_five_minutes(self):
        h, ha, *_ = make()
        ha.set('light.lamp_a', 'off', ago_s=120)
        self.assertTrue(h.awake())
        ha.set('light.lamp_a', 'off', ago_s=400)
        self.assertFalse(h.awake())

    def test_dark_bedroom_is_silent_but_failed_speaks(self):
        h, ha, spk, _ = make()
        ha.set('light.lamp_a', 'off', ago_s=3600)
        r = h.bedtime('armed')
        self.assertFalse(r['spoke'])
        self.assertEqual(spk.calls, [])
        r = h.bedtime('failed')
        self.assertTrue(r['spoke'])
        self.assertEqual(spk.calls[0]['urgency'], 'alert')
        self.assertIn('failed', spk.calls[0]['text'])


class Message(unittest.TestCase):
    def test_all_good(self):
        h, *_ = make()
        self.assertTrue(h.build('armed').startswith("All is looking good, I've just armed the alarm."))

    def test_items_list(self):
        h, ha, *_ = make()
        ha.set('lock.utility', 'unlocked')
        ha.set('binary_sensor.shed', 'on')
        ha.set('light.hall', 'on'); ha.set('light.lounge', 'on'); ha.set('light.porch', 'on')
        ha.set('sensor.dw', 'bsh.Ready')
        t = h.build('armed')
        self.assertIn("I've armed the alarm, but the utility door is unlocked, the shed door is open, 2 lights are still on downstairs, a light is still on outside, and you haven't scheduled the dishwasher.", t)

    def test_dishwasher_not_mentioned_when_it_needs_unloading(self):
        h, ha, *_ = make()
        ha.set('sensor.dw', 'bsh.Finished')
        ha.set('input_boolean.dw_unload', 'on')
        self.assertNotIn('dishwasher', h.build('armed'))

    def test_unavailable_lock_is_not_a_problem(self):
        h, ha, *_ = make()
        ha.set('lock.utility', 'unavailable')
        self.assertNotIn('unlocked', h.build('armed'))

    def test_frost_line(self):
        h, ha, *_ = make()
        ha.set('sensor.forecast', 'x', daily=[{'templow': 1.2}])
        self.assertIn("There's a frost tonight, going down to 1 degree.", h.build('armed'))
        ha.set('sensor.forecast', 'x', daily=[{'templow': -2.6}])
        self.assertIn('going down to -3 degrees.', h.build('armed'))
        ha.set('sensor.forecast', 'x', daily=[{'templow': 2.0}])
        self.assertIn('frost', h.build('armed'))
        ha.set('sensor.forecast', 'x', daily=[{'templow': 2.1}])
        self.assertNotIn('frost', h.build('armed'))

    def test_work_day(self):
        h, *_ = make()
        self.assertTrue(h.build('armed').endswith('Tomorrow is a work day, your wake-up is at 06:45.'))

    def test_lie_in_with_first_event(self):
        h, ha, *_ = make()
        ha.cal['calendar.work'] = []
        ha.cal['calendar.family'] = [{'summary': 'Dentist', 'start': {'dateTime': '2026-11-19T09:30:00+00:00'}},
                                     {'summary': 'Lunch', 'start': {'dateTime': '2026-11-19T12:30:00+00:00'}},
                                     {'summary': 'Holiday', 'start': {'dateTime': '2026-11-19T08:00:00+00:00'}}]
        self.assertTrue(h.build('armed').endswith('You can have a lie in, but you need to be up for Dentist at 9:30 am.'))

    def test_lie_in_without_event_and_off_work_day(self):
        h, ha, *_ = make()
        ha.cal['calendar.own'] = [{'summary': 'Annual leave', 'start': {'date': '2026-11-19'}}]
        self.assertTrue(h.build('armed').endswith('You can have a lie in.'))

    def test_hour_formats(self):
        h, ha, *_ = make()
        ha.cal['calendar.work'] = []
        ha.cal['calendar.family'] = [{'summary': 'Run', 'start': {'dateTime': '2026-11-19T00:15:00+00:00'}}]
        self.assertIn('Run at 12:15 am.', h.build('armed'))

    def test_after_midnight_talks_about_today(self):
        h, ha, *_ = make(now=T(1, 0, day=19))
        self.assertTrue(h.build('armed').endswith('Tomorrow is a work day, your wake-up is at 06:45.'))

    def test_calendar_failure_gives_no_tomorrow_line(self):
        h, ha, *_ = make()
        ha.cal_fail = True
        self.assertEqual(h.build('armed'), "All is looking good, I've just armed the alarm.")

    def test_failed_text(self):
        h, *_ = make()
        self.assertEqual(h.build('failed'), 'Arming the alarm failed. Please check the doors and windows.')


class Bedtime(unittest.TestCase):
    def test_live_speaks_once_with_the_right_request(self):
        h, ha, spk, _ = make()
        r = h.bedtime('armed')
        self.assertTrue(r['spoke'])
        self.assertEqual(len(spk.calls), 1)
        q = spk.calls[0]
        self.assertEqual((q['persona'], q['speaker'], q['voice'], q['dry_run'], q['urgency']), ('hearth', 'bedroom', 'hearth', False, 'normal'))

    def test_dry_asks_for_a_dry_run(self):
        h, ha, spk, _ = make(live=False)
        r = h.bedtime('armed')
        self.assertFalse(r['spoke'])
        self.assertTrue(spk.calls[0]['dry_run'])

    def test_two_step_check_then_ok(self):
        h, ha, spk, clk = make()
        ha.set('binary_sensor.front', 'on')                     # locked but the sensor reads open
        clk.on_sleep = lambda n: ha.set('binary_sensor.front', 'off')   # the sensor catches up
        r = h.bedtime('armed')
        self.assertEqual(len(spk.calls), 2)
        self.assertIn('just checking', spk.calls[0]['text'])
        self.assertTrue(r['checked'])
        self.assertTrue(spk.calls[1]['text'].startswith("I checked to see if the back door was open, and it's ok. Everything else is looking good too."))

    def test_two_step_check_still_open_waits_at_most_12_seconds(self):
        h, ha, spk, clk = make()
        ha.set('binary_sensor.front', 'on')
        start = clk.t
        h.bedtime('armed')
        self.assertLessEqual((clk.t - start).total_seconds(), 12)
        self.assertIn('it is still open', spk.calls[1]['text'])
        self.assertNotIn('But the front door is open', spk.calls[1]['text'])

    def test_two_step_with_other_problems(self):
        h, ha, spk, clk = make()
        ha.set('binary_sensor.front', 'on')
        ha.set('lock.utility', 'unlocked')
        h.bedtime('armed')
        self.assertIn('and it is still open. But the utility door is unlocked.', spk.calls[1]['text'])

    def test_failed_skips_the_check(self):
        h, ha, spk, _ = make()
        ha.set('binary_sensor.front', 'on')
        h.bedtime('failed')
        self.assertEqual(len(spk.calls), 1)

    def test_speaker_blocked_is_reported(self):
        h, ha, spk, _ = make(speaker=FakeSpeaker('blocked'))
        r = h.bedtime('armed')
        self.assertFalse(r['spoke'])
        self.assertEqual(r['speaker'], 'blocked')


class Picard(unittest.TestCase):
    def arrive(self, ha, ago=120):
        ha.set('switch.p_home', 'on', ago_s=ago)

    def test_welcome(self):
        h, ha, spk, _ = make(now=T(15))
        self.arrive(ha)
        r = h.picard()
        self.assertTrue(r['spoke'])
        q = spk.calls[0]
        self.assertEqual((q['text'], q['speaker'], q['voice'], q['volume'], q['skip_if_playing']), ('Hello Pat.', 'kitchen', 'picard', 'keep', True))

    def test_not_just_arrived(self):
        h, ha, spk, _ = make(now=T(15))
        self.arrive(ha, ago=700)
        self.assertFalse(h.picard()['spoke'])
        ha.set('switch.p_home', 'off', ago_s=10)
        self.assertFalse(h.picard()['spoke'])
        self.assertEqual(spk.calls, [])

    def test_window(self):
        for hh in (6, 22, 23):
            h, ha, spk, _ = make(now=T(hh, 30 if hh == 6 else 0))
            self.arrive(ha)
            self.assertFalse(h.picard()['spoke'], hh)
            self.assertEqual(spk.calls, [])

    def test_once_per_hour_even_across_restart(self):
        with tempfile.TemporaryDirectory() as d:
            from store import PersonaStore
            st = PersonaStore(d, 'hearth', lambda: 0)
            h, ha, spk, clk = make(now=T(15), store=st)
            self.arrive(ha)
            self.assertTrue(h.picard()['spoke'])
            clk.t += datetime.timedelta(minutes=30)
            self.arrive(ha, ago=60)
            self.assertFalse(h.picard()['spoke'])
            h2, ha2, spk2, clk2 = make(now=T(15, 30), store=st)       # a new process reads the datastore
            self.arrive(ha2, ago=60)
            self.assertFalse(h2.picard()['spoke'])
            h3, ha3, spk3, clk3 = make(now=T(16, 5), store=st)
            self.arrive(ha3, ago=60)
            self.assertTrue(h3.picard()['spoke'])

    def test_skipped_while_playing_does_not_use_up_the_hour(self):
        h, ha, spk, clk = make(now=T(15), speaker=FakeSpeaker('skipped'))
        self.arrive(ha)
        self.assertFalse(h.picard()['spoke'])
        spk.status = 'spoken'
        self.assertTrue(h.picard()['spoke'])

    def test_dry_does_not_play_and_defaults_lines(self):
        c = dict(HEARTH, picard=dict(HEARTH['picard'], lines=[]))
        clk = Clock(T(15))
        ha = FakeHA(clk)
        ha.set('switch.p_home', 'on', ago_s=60)
        spk = FakeSpeaker()
        h = hm.Hearth(ha, hm.parse_options(c), spk, clk.now, clk.sleep, lambda *a: None, lambda: False)
        r = h.picard()
        self.assertTrue(spk.calls[0]['dry_run'])
        self.assertIn('Pat', r['text'])
        self.assertNotIn('{name}', r['text'])


class Events(unittest.TestCase):
    def ev(self, h, e, old, new):
        t = h.on_state_change({'entity_id': e, 'old_state': {'state': old}, 'new_state': {'state': new}})
        if t:
            t.join(5)
        return t

    def test_alarm_events(self):
        h, ha, spk, _ = make()
        self.assertIsNotNone(self.ev(h, 'alarm_control_panel.ids', 'arming', 'armed_night'))
        self.assertIsNotNone(self.ev(h, 'alarm_control_panel.ids', 'disarmed', 'armed_home'))
        self.assertIsNotNone(self.ev(h, 'alarm_control_panel.ids', 'arming', 'disarmed'))
        self.assertEqual(len(spk.calls), 3)
        self.assertIn('failed', spk.calls[2]['text'])

    def test_ignored_changes(self):
        h, ha, spk, _ = make()
        self.assertIsNone(self.ev(h, 'alarm_control_panel.ids', 'armed_home', 'triggered'))
        self.assertIsNone(self.ev(h, 'alarm_control_panel.ids', 'armed_home', 'disarmed'))
        self.assertIsNone(self.ev(h, 'alarm_control_panel.other', 'arming', 'armed_home'))
        self.assertIsNone(self.ev(h, 'binary_sensor.front', 'on', 'off'))
        self.assertEqual(spk.calls, [])

    def test_door_opening_starts_the_welcome(self):
        h, ha, spk, _ = make(now=T(15))
        ha.set('switch.p_home', 'on', ago_s=30)
        self.assertIsNotNone(self.ev(h, 'binary_sensor.front', 'off', 'on'))
        self.assertEqual(spk.calls[0]['text'], 'Hello Pat.')

    def test_disabled_does_nothing(self):
        h, ha, spk, _ = make()
        h.cfg['enabled'] = False
        self.assertIsNone(self.ev(h, 'alarm_control_panel.ids', 'arming', 'armed_home'))


class Client(unittest.TestCase):
    def test_posts_json_with_the_token(self):
        seen = {}

        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return b'{"status": "spoken"}'

        def opener(req, timeout=0):
            seen['url'], seen['auth'], seen['body'] = req.full_url, req.headers.get('Authorization'), json.loads(req.data)
            return R()
        out = hm.make_speaker_client('http://spk:8831/', 'tok', opener)({'text': 'hi'})
        self.assertEqual(out['status'], 'spoken')
        self.assertEqual((seen['url'], seen['auth'], seen['body']), ('http://spk:8831/speak', 'Bearer tok', {'text': 'hi'}))

    def test_failure_is_an_error_status_not_an_exception(self):
        def opener(req, timeout=0):
            raise OSError('down')
        self.assertEqual(hm.make_speaker_client('http://spk:8831', 'tok', opener)({})['status'], 'error')
        self.assertEqual(hm.make_speaker_client('', 'tok')({})['status'], 'error')
        self.assertEqual(hm.make_speaker_client('http://spk', '')({})['status'], 'error')


class Wiring(unittest.TestCase):
    def opts(self, tmp, persona_extra=None, enabled=True):
        p = {'key': 'hearth', 'household': True, 'household_users': ['a', 'b']}
        p.update(persona_extra or {})
        o = {'mode': 'live', 'api_token': 'tok-12345', 'speaker_url': 'http://spk:8831', 'speaker_token': 'spk-token-1',
             'hearth': dict(HEARTH, enabled=enabled), 'personas': [p]}
        path = os.path.join(tmp, 'options.json')
        json.dump(o, open(path, 'w'))
        return appmain.load_options(path)

    def test_household_persona_without_a_bot_and_hearth_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts, problems = self.opts(tmp, {'live': True})
            self.assertEqual(problems, [])
            clk = Clock(T(22, 30))
            a = appmain.Assistant(opts, tmp, lambda *x: None, clock=lambda: clk.t.timestamp(), tz=UTC, ha=FakeHA(clk))
            self.assertTrue(a.cfg['hearth']['household'])
            self.assertEqual(a.cfg['hearth']['household_users'], ['a', 'b'])
            self.assertNotIn('hearth', a.discord)
            self.assertIsNotNone(a.hearth)
            a.tick('hearth')                                       # a persona without a bot must tick without errors
            self.assertTrue(a.status()['personas']['hearth']['household'])
            self.assertEqual(a.status()['version'], appmain.VERSION)

    def test_off_when_not_enabled_or_no_household_persona(self):
        with tempfile.TemporaryDirectory() as tmp:
            clk = Clock(T(22, 30))
            opts, _ = self.opts(tmp, enabled=False)
            self.assertIsNone(appmain.Assistant(opts, tmp, lambda *x: None, tz=UTC, ha=FakeHA(clk)).hearth)
            opts, _ = self.opts(tmp, {'household': False})
            self.assertIsNone(appmain.Assistant(opts, tmp, lambda *x: None, tz=UTC, ha=FakeHA(clk)).hearth)

    def test_preview_plays_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts, _ = self.opts(tmp, {'live': True})
            clk = Clock(T(22, 30))
            ha = FakeHA(clk)
            for e, s in make()[1].s.items():
                ha.s[e] = dict(s)
            ha.cal = make()[1].cal
            a = appmain.Assistant(opts, tmp, lambda *x: None, clock=lambda: clk.t.timestamp(), tz=UTC, ha=ha)
            calls = []
            a.hearth.speak = lambda r: calls.append(r)
            code, body = a.hearth_preview('armed')
            self.assertEqual(code, 200)
            self.assertTrue(body['awake'])
            self.assertIn('armed the alarm', body['text'])
            self.assertEqual(calls, [])
            self.assertEqual(a.hearth_preview('x')[0], 400)


if __name__ == '__main__':
    unittest.main()
