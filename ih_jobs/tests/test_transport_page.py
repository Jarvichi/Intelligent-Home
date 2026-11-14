"""transport_page plugin: the page it builds from the bus_watch entities, the save/skip/dry behaviour. Fictional values, fake Home Assistant."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import ihlib.state
from plugins import transport_page as P

SCHED = {'mon': [['First Stop', '07:30'], ['Bus home: Town Stop', '15:20']], 'tue': [['First Stop', '07:30']], 'sun': []}


def settings(**over):
    s = dict(P.DEFAULTS)
    s.update({'morning_entity': 'sensor.t_morning', 'home_entity': 'sensor.t_home', 'promise_entity': 'sensor.t_promise', 'schedule_entity': 'sensor.t_sched', 'route_label': 'no. 9'})
    s.update(over)
    return s


class FakeHA:
    def __init__(self, dashboards=(), rows=SCHED):
        self.dashboards, self.rows, self.sent = list(dashboards), rows, []

    def get(self, path, timeout=30):
        return {'state': '3', 'attributes': {'rows': self.rows}}

    def ws(self, msg):
        self.sent.append(msg)
        if msg['type'] == 'lovelace/dashboards/list':
            return [{'url_path': u} for u in self.dashboards]
        if msg['type'] == 'lovelace/dashboards/create':
            self.dashboards.append(msg['url_path'])
        return None


class Ctx:
    def __init__(self, folder, s, dry=False, ha=None):
        self.settings, self.dry, self.ha, self.logs = s, dry, ha or FakeHA(), []
        self.state = ihlib.state.Store(folder)
        self.now = dt.datetime(2026, 11, 14, 22, 0, tzinfo=dt.timezone.utc)

    def log(self, m):
        self.logs.append(m)


class TestBuild(unittest.TestCase):
    def test_views_and_entities(self):
        cfg = P.build_config(settings(), SCHED)
        self.assertEqual([v['path'] for v in cfg['views']], ['college-bus', 'double-decker'])
        text = json.dumps(cfg)
        for e in ('sensor.t_morning', 'sensor.t_home', 'sensor.t_promise'):
            self.assertIn(e, text)
        self.assertNotIn('ENT', text.replace('ENTITIES', '').replace('CENT', ''))      # every template placeholder was replaced
        self.assertIn('The no. 9 is about', text)
        self.assertIn('Your bus home (the no. 9)', text)
        self.assertTrue(all(v['theme'] == 'House' for v in cfg['views']))

    def test_week_rows_split_home_journeys(self):
        rows = P.week_rows(SCHED, 'Bus home: ')
        self.assertEqual([(r['day'], r['journey'], r['stop'], r['time']) for r in rows],
                         [('Mon', 'To college', 'First Stop', '07:30'), ('Mon', 'To home', 'Town Stop', '15:20'), ('Tue', 'To college', 'First Stop', '07:30')])
        self.assertEqual(len({r['id'] for r in rows}), 3)
        self.assertEqual(P.week_rows(None, 'x'), [])
        self.assertEqual(P.week_rows({'mon': ['bad', [1]]}, 'x'), [])

    def test_optional_parts_are_left_out(self):
        cfg = P.build_config(settings(home_entity='', schedule_entity=''), None)
        self.assertEqual([s['heading'] for s in cfg['views'][0]['sections']], ['College bus'])
        cards = cfg['views'][1]['sections'][0]['cards']
        self.assertFalse([c for c in cards if c.get('type') == 'entities'])
        cfg2 = P.build_config(settings(type_entities=['input_select.x', 'script.y'], promise_intro='Intro text'), SCHED)
        cards2 = cfg2['views'][1]['sections'][0]['cards']
        self.assertEqual(cards2[0]['content'], 'Intro text')
        self.assertEqual([c for c in cards2 if c.get('type') == 'entities'][0]['entities'], ['input_select.x', 'script.y'])

    def test_history_table_uses_the_published_row_fields(self):
        card = P._history_card('sensor.t_promise')
        keys = {c['key'] for c in card['columns']}
        self.assertTrue({'id', 'date', 'text', 'bus', 'label', 'verdict', 'journey'} <= keys)
        self.assertEqual((card['entity'], card['attribute'], card['row_key']), ('sensor.t_promise', 'rows', 'id'))

    def test_defaults_hold_no_house_values(self):
        d = json.dumps(P.DEFAULTS)
        for word in ('sensor.', 'script.', 'input_'):
            self.assertNotIn(word, d)

    def test_digest_is_stable_and_changes_with_the_schedule(self):
        a = P.digest(P.build_config(settings(), SCHED))
        self.assertEqual(a, P.digest(P.build_config(settings(), json.loads(json.dumps(SCHED)))))
        self.assertNotEqual(a, P.digest(P.build_config(settings(), dict(SCHED, tue=[['First Stop', '07:45']]))))


class TestRun(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_not_configured(self):
        c = Ctx(self.dir, dict(P.DEFAULTS))
        self.assertFalse(P.run(c))
        self.assertEqual(c.ha.sent, [])

    def test_dry_saves_nothing_and_writes_no_state(self):
        c = Ctx(self.dir, settings(), dry=True)
        self.assertTrue(P.run(c))
        self.assertEqual(c.ha.sent, [])
        self.assertEqual(ihlib.state.Store(self.dir).load('state'), {})
        self.assertIn('[DRY]', ' '.join(c.logs))

    def test_live_creates_then_saves_and_then_skips_until_it_changes(self):
        ha = FakeHA()
        c = Ctx(self.dir, settings(), ha=ha)
        self.assertTrue(P.run(c))
        self.assertEqual([m['type'] for m in ha.sent], ['lovelace/dashboards/list', 'lovelace/dashboards/create', 'lovelace/config/save'])
        self.assertFalse(ha.sent[1]['require_admin'])
        self.assertEqual(ha.sent[2]['url_path'], 'transport')
        ha.sent.clear()
        self.assertTrue(P.run(Ctx(self.dir, settings(), ha=ha)))
        self.assertEqual(ha.sent, [])                                   # unchanged: nothing saved
        ha.rows = dict(SCHED, tue=[['First Stop', '07:45']])
        self.assertTrue(P.run(Ctx(self.dir, settings(), ha=ha)))
        self.assertEqual([m['type'] for m in ha.sent], ['lovelace/dashboards/list', 'lovelace/config/save'])   # exists now: no create

    def test_existing_dashboard_is_not_created_again(self):
        ha = FakeHA(dashboards=['transport'])
        P.run(Ctx(self.dir, settings(), ha=ha))
        self.assertNotIn('lovelace/dashboards/create', [m['type'] for m in ha.sent])

    def test_schedule_read_failure_keeps_the_old_page(self):
        class Down(FakeHA):
            def get(self, path, timeout=30):
                raise OSError('down')
        ha = Down()
        self.assertFalse(P.run(Ctx(self.dir, settings(), ha=ha)))
        self.assertEqual(ha.sent, [])


if __name__ == '__main__':
    unittest.main()
