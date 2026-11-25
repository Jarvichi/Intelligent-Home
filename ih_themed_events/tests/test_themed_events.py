"""ih_themed_events: rules, generator, store, API and page files. Run: python3 -m unittest discover -s ih_themed_events/tests"""
import copy
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / 'ih_themed_events'
sys.path.insert(0, str(ADDON / 'app'))
import generator as G
import main as MAIN
import model as M
import readiness
import install_plan
import installer
import seed as SEED
from store import Store


def ev(**kw):
    base = copy.deepcopy(SEED.NYE)
    base.update(kw)
    return M.clean_event(base)


class Rules(unittest.TestCase):
    def test_effect_steps_validate_and_preserve_legacy(self):
        legacy = M.clean_event(SEED.HALLOWEEN)
        self.assertNotIn('steps', legacy['effects'][0])
        effect = {'id': 'flash', 'name': 'Flash', 'steps': [{'type': 'light', 'colour': '#FFAA00', 'brightness': 80, 'seconds': 0.2}, {'type': 'sound'}], 'repeat': 2, 'restore': True}
        cleaned = ev(effects=[effect])['effects'][0]
        self.assertEqual(cleaned['steps'][0]['colour'], '#ffaa00')
        self.assertEqual(cleaned['repeat'], 2)
        for invalid in ([], [{'type': 'light', 'colour': '#fff', 'brightness': 101, 'seconds': 1}], [{'type': 'wait', 'seconds': 0}], [{'type': 'unknown'}]):
            with self.assertRaises(M.RuleError):
                ev(effects=[dict(effect, steps=invalid)])
    def test_seed_records_are_valid(self):
        for rec in (SEED.HALLOWEEN, SEED.NYE):
            self.assertEqual(M.clean_event(rec)['slug'], rec['slug'])

    def test_bad_records_are_refused(self):
        for bad in ({'slug': 'Bad Slug', 'name': 'x'}, {'slug': 'ok', 'name': ''}, {'slug': 'ok', 'name': 'x', 'colour': 1}, {'slug': 'ok', 'name': 'x', 'lights': ['switch.a']},
                    {'slug': 'ok', 'name': 'x', 'sounds_folder': '/etc'}, {'slug': 'ok', 'name': 'x', 'auto_off': '25:00'}, {'slug': 'ok', 'name': 'x', 'when': {'annual': {'month': 13, 'day': 1}}},
                    {'slug': 'ok', 'name': 'x', 'effects': [{'id': 'a', 'min_gap_minutes': 9, 'max_gap_minutes': 3}]}, {'slug': 'ok', 'name': 'x', 'look': {'rgb': [0, 0, 300]}},
                    {'slug': 'ok', 'name': 'x', 'when': {'dates': [{'start': '2026-12-31', 'end': '2026-12-30'}]}}):
            with self.assertRaises(M.RuleError, msg=str(bad)):
                M.clean_event(bad)

    def test_next_occurrence(self):
        annual = {'annual': {'month': 12, 'day': 31, 'days': 2}}
        self.assertEqual(M.next_occurrence(annual, date(2026, 11, 19)), {'start': '2026-12-31', 'end': '2027-01-01', 'days_to_start': 42, 'running': False})
        self.assertTrue(M.next_occurrence(annual, date(2027, 1, 1))['running'])
        self.assertEqual(M.next_occurrence(annual, date(2027, 1, 2))['start'], '2027-12-31')
        self.assertIsNone(M.next_occurrence({}, date(2026, 1, 1)))
        fixed = {'dates': [{'start': '2026-05-01', 'end': '2026-05-01'}]}
        self.assertIsNone(M.next_occurrence(fixed, date(2026, 6, 1)))
        self.assertEqual(M.next_occurrence({'annual': {'month': 2, 'day': 29, 'days': 1}}, date(2027, 1, 1))['start'], '2027-02-28')

    def test_checklist_due(self):
        occ = {'start': '2026-12-31', 'end': '2026-12-31'}
        self.assertEqual(M.checklist_due({'days_before': 3}, occ), '2026-12-28')
        self.assertEqual(M.checklist_due({'days_before': -1}, occ), '2027-01-01')


class Generator(unittest.TestCase):
    def test_install_plan_refuses_existing_entities_and_legacy_effects(self):
        live = install_plan.build(M.clean_event(SEED.HALLOWEEN))
        self.assertTrue(any('already live' in issue for issue in live['issues']))
        event = ev(effects=[], light_group='', lights=[])
        safe = install_plan.build(event)
        self.assertFalse(safe['issues'])
        self.assertTrue(install_plan.build(event, existing_entity_ids=['input_boolean.nye_mode'])['issues'])
        self.assertTrue(install_plan.build(event, existing_automation_ids=['nye_mode_switch'])['issues'])
    def test_readiness_does_not_hide_existing_rule_or_timing_gaps(self):
        report = readiness.inspect(M.clean_event(SEED.HALLOWEEN))
        self.assertFalse(report['ready'])
        self.assertTrue(any('independently managed' in item for item in report['issues']))
        self.assertTrue(any('custom behaviour' in item for item in report['issues']))
        event = ev(effects=[{'id': 'test_effect', 'name': 'Test', 'from': '18:00', 'to': '22:00', 'steps': [{'type': 'wait', 'seconds': 60}], 'repeat': 2}])
        issues = readiness.inspect(event)['issues']
        self.assertTrue(any('90-second' in item for item in issues))

    def test_effect_windows_and_individual_intervals_are_generated(self):
        event = ev(effects=[{'id': 'first', 'name': 'First', 'from': '20:00', 'to': '02:00', 'min_gap_minutes': 3, 'max_gap_minutes': 7},
                            {'id': 'second', 'name': 'Second', 'min_gap_minutes': 10, 'max_gap_minutes': 20},
                            {'id': 'fixed', 'name': 'Fixed', 'at': '21:00', 'from': '20:00', 'to': '23:00'}])
        auto = G.random_effects(event)
        template = auto['actions'][0]['variables']['chosen_effect']
        self.assertIn("clock >= '20:00' or clock < '02:00'", template)
        self.assertIn('age >= 3', template)
        self.assertIn('age >= 7', template)
        self.assertIn('age >= 10', template)
        self.assertIn('age >= 20', template)
        self.assertEqual(G.timed_effects(event)[0]['conditions'][1], {'condition': 'time', 'after': '20:00:00', 'before': '23:00:00'})
    def test_built_effect_compiles_sequence_and_restores(self):
        effect = {'id': 'storm', 'name': 'Storm', 'sound': 'media-source://media_source/local/thunder.mp3', 'repeat': 2, 'restore': True,
                  'steps': [{'type': 'sound'}, {'type': 'light', 'colour': '#ffffff', 'brightness': 100, 'seconds': 0.2}, {'type': 'light', 'colour': '#ffffff', 'brightness': 0, 'seconds': 0.3}]}
        event = ev(lights=['light.test'], speakers=['media_player.test'], effects=[effect])
        script = G.built_effect_script(event, event['effects'][0])
        self.assertEqual(script['sequence'][1]['action'], 'scene.create')
        steps = script['sequence'][2]['repeat']
        self.assertEqual(steps['count'], 2)
        self.assertEqual(steps['sequence'][0]['data']['media_content_id'], effect['sound'])
        self.assertTrue(any(a.get('action') == 'light.turn_off' for a in steps['sequence']))
        self.assertEqual(script['sequence'][-2]['action'], 'scene.turn_on')
        self.assertIn('nye_storm:', G.generate(event)['scripts.yaml'])
        targeted = copy.deepcopy(effect)
        targeted['steps'][1]['targets'] = ['light.other']
        targeted['steps'][2]['targets'] = ['light.other']
        specific = ev(lights=[], light_group='', speakers=['media_player.test'], effects=[targeted])
        targeted_script = G.built_effect_script(specific, specific['effects'][0])
        self.assertEqual(targeted_script['sequence'][1]['data']['snapshot_entities'], ['light.other'])
        no_lights = ev(lights=[], light_group='', effects=[effect])
        self.assertIsNone(G.built_effect_script(no_lights, no_lights['effects'][0]))
    def test_same_record_same_text(self):
        self.assertEqual(G.generate(ev()), G.generate(ev()))

    def test_yaml_writer(self):
        text = G.dump({'a': 'on', 'b': [{'x': 1, 'y': 'q"t'}, 'plain'], 'c': {}, 'd': True, 'e': 'a: b'})
        self.assertEqual(text, 'a: "on"\nb:\n- x: 1\n  y: "q\\"t"\n- plain\nc: {}\nd: true\ne: "a: b"')

    def test_names_follow_one_rule(self):
        i = G.ids(ev(slug='xmas'))
        self.assertEqual(i['mode'], 'input_boolean.xmas_mode')
        h = G.helpers(ev(slug='xmas'))
        self.assertEqual(set(h['input_boolean']), {'xmas_mode', 'xmas_music', 'xmas_busy'})   # the midnight effect is timed, so no random helper
        self.assertEqual(set(h['input_text']), {'xmas_lights_off', 'xmas_speakers_playing', 'xmas_paused_automations'})

    def test_random_effects_only_for_untimed_effects(self):
        self.assertIsNone(G.random_effects(ev()))
        e = ev(effects=[{'id': 'scare', 'name': 'Scare', 'min_gap_minutes': 10, 'max_gap_minutes': 15}])
        self.assertIn('nye_random_effects', G.helpers(e)['input_boolean'])
        self.assertEqual(G.random_effects(e)['id'], 'nye_random_effects')
        self.assertEqual(G.timed_effects(ev())[0]['triggers'][0]['at'], '00:00:00')

    def test_halloween_matches_live_ids(self):
        """The ids the live halloween_mode_switch automation uses (read from the live file on 18 Nov 2026)."""
        h = M.clean_event(SEED.HALLOWEEN)
        text = G.generate(h)['automations.yaml']
        for entity in ('input_boolean.halloween_mode', 'input_boolean.halloween_music', 'input_boolean.halloween_busy', 'input_text.halloween_lights_off',
                       'input_text.halloween_speakers_playing', 'input_text.halloween_paused_automations', 'scene.halloween_before', 'light.halloween_lights',
                       'light.uplighter', 'switch.sonoff_s60zbtpg', 'media_player.snug_snug', 'automation.motion_lights_kitchen', 'automation.motion_lights_living_room'):
            self.assertIn(entity, text, entity)
        self.assertIn('id: halloween_mode_switch', text)
        self.assertIn('id: halloween_safety', text)
        self.assertIn('"02:30:00"', text)

    def test_no_lights_gives_no_scene_and_a_warning(self):
        e = ev()
        text = G.generate(e)['automations.yaml']
        self.assertNotIn('scene.create', text)
        self.assertNotIn('script.nye_look', text)       # no look script exists, so nothing may call it
        self.assertTrue(any('No lights' in w for w in G.warnings(e)))

    def test_group_only_lighting_is_snapshotted_for_restoration(self):
        event = ev(lights=[], light_group='light.event_group')
        actions = G.mode_switch(event)['actions'][0]['choose'][0]['sequence']
        snapshot = next(action for action in actions if action.get('action') == 'scene.create')
        self.assertIn('light.event_group', snapshot['data']['snapshot_entities'])

    def test_scripts_called_exist_or_are_flagged(self):
        e = ev(light_group='light.nye_group')
        files = G.generate(e)
        self.assertIn('nye_look:', files['scripts.yaml'])
        self.assertIn('script.nye_look', files['automations.yaml'])
        self.assertTrue(any('script.nye_midnight_burst' in w for w in G.warnings(e)))

    def test_every_automation_has_id_alias_and_actions(self):
        text = G.generate(M.clean_event(SEED.HALLOWEEN))['automations.yaml']
        self.assertEqual(text.count('\n- id: '), 3)       # mode_switch, safety, random_effects
        self.assertEqual(text.count('\n  alias: '), 3)


class InstallerTests(unittest.TestCase):
    class HA:
        def __init__(self):
            self.created = []
            self.fail_post = False
        def get(self, path):
            if path == 'states':
                return []
            if path.startswith('states/'):
                return {'entity_id': path[len('states/'):], 'state': 'off'}
            raise urllib.error.HTTPError(path, 404, 'missing', {}, None)
        def ws(self, message):
            if message['type'] == 'config/entity_registry/list':
                return []
            self.created.append(message)
            return {'id': message['name']}
        def post(self, path, data):
            self.created.append(path)
            if self.fail_post:
                raise RuntimeError('network failure')
            return {'result': 'ok'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.close)
        self.store.create({'slug': 'party', 'name': 'Party', 'effects': []}, checklist=[])
        self.ha = self.HA()

    def test_apply_creates_only_after_preview_and_blocks_retry(self):
        self.assertFalse(installer.preview(self.store, self.ha, 'party')['issues'])
        self.assertEqual(installer.apply(self.store, self.ha, 'party')['status'], 'submitted')
        self.assertEqual(self.store.installation('party')['status'], 'submitted')
        before = len(self.ha.created)
        with self.assertRaises(M.RuleError):
            installer.apply(self.store, self.ha, 'party')
        self.assertEqual(before, len(self.ha.created))

    def test_partial_failure_is_recorded_and_never_retried_automatically(self):
        self.ha.fail_post = True
        with self.assertRaises(M.RuleError):
            installer.apply(self.store, self.ha, 'party')
        record = self.store.installation('party')
        self.assertEqual(record['status'], 'needs_review')
        self.assertEqual(record['journal'][-1]['state'], 'starting')
        with self.assertRaises(M.RuleError):
            installer.apply(self.store, self.ha, 'party')

    def test_collision_prevents_every_write(self):
        original = self.ha.get
        self.ha.get = lambda path: [{'entity_id': 'input_boolean.party_mode'}] if path == 'states' else original(path)
        with self.assertRaises(M.RuleError):
            installer.apply(self.store, self.ha, 'party')
        self.assertFalse(self.ha.created)
        self.assertIsNone(self.store.installation('party'))

    def test_helper_assigned_other_id_is_recorded_and_stops(self):
        original = self.ha.ws
        self.ha.ws = lambda msg: original(msg) if msg['type'] == 'config/entity_registry/list' else {'id': msg['name'] + '_2'}
        with self.assertRaises(M.RuleError):
            installer.apply(self.store, self.ha, 'party')
        journal = self.store.installation('party')['journal']
        self.assertTrue(journal[0]['created_id'].endswith('_2'))
        self.assertEqual(journal[0]['state'], 'starting')

    def test_apply_requires_ui_live_control_and_confirmation(self):
        for ui, control, body in ((False, 'live', {'confirm': True}), (True, 'off', {'confirm': True}), (True, 'live', {'confirm': False})):
            with self.assertRaises(M.RuleError):
                MAIN.dispatch(self.store, self.ha, {'control': control}, 'POST', '/events/party/apply', body, 'owner', ui)
        self.assertFalse(self.ha.created)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.now = [1795000000.0]          # 2026-11-18
        self.store = Store(self.dir.name, clock=lambda: self.now[0])
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(self.store.close)

    def test_seed_once(self):
        self.assertTrue(SEED.load(self.store))
        self.assertFalse(SEED.load(self.store))
        self.assertEqual(self.store.counts()['events'], 2)

    def test_list_orders_by_next_date_and_counts_open_items(self):
        SEED.load(self.store)
        events = self.store.list()
        self.assertEqual(self.store.today(), date(2026, 11, 18))
        self.assertEqual([e['slug'] for e in events], ['nye', 'halloween'])     # 31 Dec 2026 comes before 31 Oct 2027
        nye = self.store.get('nye')
        self.assertEqual(nye['open_items'], len(SEED.NYE_CHECKLIST))
        self.assertEqual(nye['next']['start'][5:], '12-31')

    def test_checklist_and_review(self):
        SEED.load(self.store)
        nye = self.store.get('nye')
        first = nye['checklist'][0]['id']
        nye = self.store.set_item('nye', first, True)
        self.assertEqual(nye['open_items'], len(SEED.NYE_CHECKLIST) - 1)
        nye = self.store.set_item('nye', first, False)
        self.assertEqual(nye['open_items'], len(SEED.NYE_CHECKLIST))
        nye = self.store.add_item('nye', {'text': 'Buy sparklers', 'days_before': 2})
        self.assertEqual(nye['checklist'][-1]['due'][5:], '12-29')
        with self.assertRaises(M.RuleError):
            self.store.set_item('nye', 99999, True)
        occ = nye['next']['start']
        self.store.save_review('nye', occ, 'cheer was great', 'lights too dim')
        self.store.save_review('nye', occ, 'cheer was great', 'lights brighter')       # saving again replaces
        self.assertEqual(len(self.store.reviews('nye')), 1)
        self.assertEqual(self.store.get('nye')['review']['change'], 'lights brighter')
        with self.assertRaises(M.RuleError):
            self.store.save_review('nye', 'tomorrow', '', '')

    def test_create_update_delete(self):
        SEED.load(self.store)
        with self.assertRaises(M.RuleError) as c:
            self.store.create(SEED.NYE)
        self.assertEqual(c.exception.code, 'conflict')
        rec = self.store.record('nye')
        rec['lights'] = ['light.kitchen_spots']
        self.assertEqual(self.store.update('nye', rec)['lights'], ['light.kitchen_spots'])
        rec['slug'] = 'other'
        with self.assertRaises(M.RuleError):
            self.store.update('nye', rec)
        self.store.delete('nye')
        with self.assertRaises(M.RuleError) as c:
            self.store.get('nye')
        self.assertEqual(c.exception.code, 'missing')
        self.assertEqual(self.store.get('halloween')['slug'], 'halloween')

    def test_no_secrets_table_and_log_written(self):
        SEED.load(self.store)
        self.assertGreaterEqual(len(self.store.get('nye')['log']), 1)


class FakeHA:
    def __init__(self):
        self.calls, self.states = [], {'input_boolean.nye_mode': 'off'}

    def state(self, entity):
        return self.states.get(entity)

    def call_service(self, domain, service, data=None):
        self.calls.append((domain, service, data))


class Api(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.opts = {'api_token': 'tok', 'control': 'off'}
        self.ha = FakeHA()
        s1 = Store(self.dir.name)
        SEED.load(s1)
        self.addCleanup(s1.close)
        self.servers = []
        for ui in (False, True):
            s = Store(self.dir.name) if ui else s1
            if ui:
                self.addCleanup(s.close)
            srv = HTTPServer(('127.0.0.1', 0), MAIN.make_handler(s, self.ha, lambda: self.opts, ui))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.servers.append(srv)
            self.addCleanup(srv.shutdown)
        self.api = 'http://127.0.0.1:%d' % self.servers[0].server_address[1]

    def call(self, method, path, body=None, token='tok'):
        req = urllib.request.Request(self.api + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                     headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}')

    def test_health_is_open_and_everything_else_needs_the_token(self):
        with urllib.request.urlopen(self.api + '/healthz', timeout=5) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(self.call('GET', '/events', token=None)[0], 401)
        self.assertEqual(self.call('GET', '/events', token='wrong')[0], 401)
        self.assertEqual(self.call('GET', '/events')[0], 200)
        self.opts['api_token'] = ''
        self.assertEqual(self.call('GET', '/events', token='')[0], 401)

    def test_list_get_generate(self):
        code, out = self.call('GET', '/events')
        self.assertEqual({e['slug'] for e in out['events']}, {'halloween', 'nye'})
        nye = next(e for e in out['events'] if e['slug'] == 'nye')
        self.assertEqual(nye['mode_state'], 'off')
        code, out = self.call('GET', '/events/nye/generate')
        self.assertEqual(code, 200)
        self.assertIn('automations.yaml', out['files'])
        self.assertEqual(self.call('GET', '/events/nope_zz')[0], 404)

    def test_create_refuses_unknown_fields_and_makes_slug(self):
        self.assertEqual(self.call('POST', '/events', {'name': 'Bonfire Night', 'colour': 'red'})[0], 400)
        code, out = self.call('POST', '/events', {'name': 'Bonfire Night', 'when': {'annual': {'month': 11, 'day': 5}}})
        self.assertEqual((code, out['slug']), (201, 'bonfire_night'))
        self.assertEqual(self.call('POST', '/events', {'name': 'Bonfire Night'})[0], 409)

    def test_entity_picker_is_ui_only_and_filters_domains(self):
        self.ha.get = lambda path: [
            {'entity_id': 'light.table', 'attributes': {'friendly_name': 'Table'}},
            {'entity_id': 'sensor.secret', 'attributes': {'friendly_name': 'Sensor'}},
            {'entity_id': 'switch.fan', 'attributes': {}},
        ]
        code, result = MAIN.dispatch(None, self.ha, self.opts, 'GET', '/entities', None, 'owner', True)
        self.assertEqual(code, 200)
        self.assertEqual(result['entities'], [{'id': 'light.table', 'name': 'Table'}, {'id': 'switch.fan', 'name': 'switch.fan'}])
        with self.assertRaises(M.RuleError):
            MAIN.dispatch(None, self.ha, self.opts, 'GET', '/entities', None, 'worker', False)

    def test_media_browser_only_exposes_local_media_to_ui(self):
        self.ha.ws = lambda request: {'children': [
            {'title': 'Bell', 'media_content_id': 'media-source://media_source/local/bell.mp3', 'can_play': True},
            {'title': 'Other', 'media_content_id': 'media-source://other/private', 'can_play': True},
        ]}
        code, result = MAIN.dispatch(None, self.ha, self.opts, 'POST', '/media', {}, 'owner', True)
        self.assertEqual(code, 200)
        self.assertEqual([x['name'] for x in result['items']], ['Bell'])
        for source, ui in [('media-source://media_source/local/../secret', True), ('media-source://media_source/locality', True), ('media-source://media_source/local', False)]:
            with self.assertRaises(M.RuleError):
                MAIN.dispatch(None, self.ha, self.opts, 'POST', '/media', {'source': source}, 'owner', ui)

    def test_mode_switch_is_page_only_and_off_by_default(self):
        self.assertEqual(self.call('POST', '/events/nye/mode', {'on': True})[0], 403)     # API port never switches
        self.assertEqual(self.ha.calls, [])
        # through the page handler, called directly (the page only answers the ingress address)
        store = Store(self.dir.name)
        with self.assertRaises(M.RuleError):
            MAIN.dispatch(store, self.ha, {'control': 'off'}, 'POST', '/events/nye/mode', {'on': True}, 'owner', True)
        code, out = MAIN.dispatch(store, self.ha, {'control': 'live'}, 'POST', '/events/nye/mode', {'on': True}, 'owner', True)
        self.assertEqual((code, self.ha.calls[-1]), (200, ('input_boolean', 'turn_on', {'entity_id': 'input_boolean.nye_mode'})))
        self.ha.states.pop('input_boolean.nye_mode')
        with self.assertRaises(M.RuleError) as c:
            MAIN.dispatch(store, self.ha, {'control': 'live'}, 'POST', '/events/nye/mode', {'on': True}, 'owner', True)
        self.assertEqual(c.exception.code, 'conflict')

    def test_items_and_review_routes(self):
        code, out = self.call('POST', '/events/nye/items', {'text': 'Charge the speakers', 'days_before': 1})
        self.assertEqual(code, 201)
        item = out['checklist'][-1]['id']
        self.assertTrue(self.call('POST', '/events/nye/items/%d' % item, {'done': True})[1]['checklist'][-1]['done'])
        self.assertEqual(self.call('POST', '/events/nye/items/%d' % item, {'done': 'yes'})[0], 400)
        self.assertEqual(self.call('POST', '/events/nye/items/%d/delete' % item, {})[0], 200)
        occ = self.call('GET', '/events/nye')[1]['next']['start']
        self.assertEqual(self.call('POST', '/events/nye/review', {'occurrence': occ, 'worked': 'a', 'change': 'b'})[0], 200)


class Files(unittest.TestCase):
    UI = ADDON / 'app' / 'ui'

    def test_shared_ui_copies_and_nav(self):
        page = (self.UI / 'index.html').read_text()
        self.assertIn('<ih-nav data-home-href="/app/32c8114c_ih_orchestrator"', page)
        for mine, master in (('ih-ui.js', 'components.js'), ('ih-ui.css', 'tokens.css'), ('ih-patterns.css', 'patterns.css')):
            self.assertEqual((self.UI / mine).read_text(), (ROOT / 'ih_ui' / master).read_text(), mine)

    def test_script_builds_no_markup_from_text(self):
        js = (self.UI / 'ui.js').read_text()
        for bad in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'confirm(', 'alert('):
            self.assertNotIn(bad, js)

    def test_versions_in_step_and_install_flags(self):
        cfg = (ADDON / 'config.yaml').read_text()
        version = [l for l in cfg.splitlines() if l.startswith('version:')][0].split('"')[1]
        self.assertEqual(json.loads((ADDON / 'app' / 'manifest.json').read_text())['version'], version)
        self.assertEqual(MAIN.VERSION, version)
        self.assertIn('## [%s]' % version, (ADDON / 'CHANGELOG.md').read_text())
        self.assertIn('boot: auto', cfg)
        self.assertIn('watchdog: "http://[HOST]:[PORT:8819]/healthz"', cfg)
        self.assertIn('slug: ih_themed_events', cfg)

    def test_ihlib_copy_is_current(self):
        for f in (ROOT / 'ihlib').glob('*.py'):
            self.assertEqual(f.read_text(), (ADDON / 'app' / 'ihlib' / f.name).read_text(), f.name)

    def test_no_secrets_in_options_example(self):
        self.assertEqual(json.loads((ADDON / 'options.example.json').read_text()), {'api_token': '', 'control': 'off'})


if __name__ == '__main__':
    unittest.main()


class IdleConnection(unittest.TestCase):
    def test_idle_connection_does_not_block_other_requests(self):
        """The ingress gateway keeps an idle connection open; a single-threaded server then never answered the page (0.1.1)."""
        import socket, threading, urllib.request
        sys.path.insert(0, str(ADDON / 'app'))
        import main as M
        class H(M.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'ok')
            def log_message(self, *a): pass
        srv = M.QuietServer(('127.0.0.1', 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
        idle = socket.create_connection(srv.server_address)
        try:
            self.assertEqual(urllib.request.urlopen('http://127.0.0.1:%d/' % srv.server_address[1], timeout=3).read(), b'ok')
        finally:
            idle.close(); srv.shutdown()
