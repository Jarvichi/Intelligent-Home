import datetime, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
from main import Assistant

HOME = (51.0, -2.0)


class FakeHA:
    def __init__(self, states): self.states = states
    def get(self, path):
        e = path.split('states/', 1)[1]
        if e not in self.states: raise OSError('404')
        return self.states[e]
    def calendar_events(self, ent, a, b):
        return [{'summary': 'Dentist', 'location': 'Bristol', 'start': {'dateTime': '2026-11-06T10:00:00+00:00'}}] if ent == 'calendar.mine' else []


def st(state, **a): return {'state': state, 'attributes': a, 'last_changed': '2026-11-06T08:00:00+00:00'}


class WhereTests(unittest.TestCase):
    def make(self, states):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory()
        a.log = lambda *x: None; a.clock = lambda: 1793955600.0; a.tz = None
        a.cfg = {'aa': {'location_entities': ['person.a', 'switch.a_home'], 'presence_entity': '', 'calendars': [], 'calendar_read': ['calendar.mine'], 'calendar_write': []},
                 'bb': {'location_entities': ['switch.b_home'], 'presence_entity': '', 'calendars': [], 'calendar_read': [], 'calendar_write': []}}
        a.stores = {k: PersonaStore(self.tmp.name, k, a.clock) for k in ('aa', 'bb')}
        a.stores['bb'].merge_profile({'person_name': 'Bea'})
        a.ha = FakeHA(dict(states, **{'zone.home': st('zoning', latitude=HOME[0], longitude=HOME[1])}))
        return a

    def test_gps_first_then_switch_and_distance(self):
        a = self.make({'person.a': st('not_home', latitude=51.1, longitude=-2.0, gps_accuracy=12.0), 'switch.a_home': st('on'), 'switch.b_home': st('off')})
        c, r = a.where_view('aa')
        self.assertEqual(c, 200)
        y = r['you']
        self.assertEqual((y['source'], y['home']), ('gps', False))
        self.assertAlmostEqual(y['distance_km'], 11.1, delta=0.3)
        self.assertIn('conflict', y)   # GPS says away, the switch says home
        self.assertEqual(r['household'], [{'who': 'Bea', 'home': False}])   # others: home/away only
        self.assertEqual(r['calendar_now'][0]['location'], 'Bristol')

    def test_fallback_to_switch_when_gps_unavailable(self):
        a = self.make({'person.a': st('unavailable'), 'switch.a_home': st('on'), 'switch.b_home': st('on')})
        y = a.where_view('aa')[1]['you']
        self.assertEqual((y['source'], y['home']), ('presence', True))
        self.assertNotIn('conflict', y)

    def test_named_place_and_unknown_person(self):
        a = self.make({'person.a': st('Work', latitude=51.0, longitude=-2.5)})
        y = a.where_view('aa')[1]
        self.assertEqual(y['you']['place'], 'Work')
        self.assertEqual(y['household'], [{'who': 'Bea', 'home': None}])   # no signal -> unknown, not a guess

    def test_no_ha(self):
        a = self.make({}); a.ha = None
        self.assertEqual(a.where_view('aa')[0], 503)


if __name__ == '__main__':
    unittest.main()
