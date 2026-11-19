"""Text and layout parity with the original Kindle picture (the Lovelace page rules)."""
import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import kindle_render
import render
import source_settings

NS = '{http://www.w3.org/2000/svg}'

def texts(data, alerts=None, now=0):
    root = ET.fromstring(kindle_render.svg(data, alerts, now))
    return [''.join(t.itertext()) for t in root.iter(NS + 'text')]

def weather(condition='rainy', **kw):
    return {'weather': {'current': {'condition': condition, 'temperature': 7},
                        'daily': [{'label': 'Today', 'low': 8, 'high': 10},
                                  {'label': 'Tomorrow', 'low': 2, 'high': 8, 'precipitation': 1.04, 'condition': 'rainy'},
                                  {'label': 'Saturday', 'low': 1, 'high': 10, 'precipitation': 0, 'condition': 'sunny'}],
                        'hours': [{'time': '23:00', 'temperature': 8, 'precipitation': 1.0, 'condition': 'rainy'}],
                        'facts': dict({'wind': '16 km/h', 'next_rain': '22:00 next day', 'sky': 'Cloud 55%'}, **kw)}}

class TextParityTests(unittest.TestCase):
    def test_condition_names_are_the_original_ones(self):
        for condition, name in {'rainy': 'Showers', 'pouring': 'Heavy rain', 'lightning': 'Thunder', 'lightning-rainy': 'Thunder & rain',
                                'snowy': 'Snow', 'clear-night': 'Clear', 'exceptional': 'Warning', 'windy-variant': 'Windy',
                                'partlycloudy': 'Partly cloudy'}.items():
            self.assertIn(name.replace('&', '&'), texts(weather(condition)), condition)

    def test_forecast_ranges_and_rain_read_like_the_original(self):
        t = texts(weather())
        self.assertIn('2–8° 1 mm rain', t)
        self.assertIn('1–10° dry', t)
        self.assertIn('1 mm', t)          # hourly rain 1.0 -> "1 mm"
        self.assertIn('Today 8° – 10°', t)

    def test_next_rain_wording(self):
        self.assertIn('22:00 tmrw', texts(weather()))
        self.assertIn('Dry 3 days', texts(weather(next_rain='None in hourly forecast')))

    def test_header_shows_plug_and_battery_glyphs(self):
        on = {'device': {'fresh': True, 'battery': 87, 'charging': True}}
        off = {'device': {'fresh': True, 'battery': 87, 'charging': False}}
        self.assertTrue(any(x.endswith('⚡ Plugged in') for x in texts(on)))
        self.assertTrue(any(x.endswith('🔋 Kindle 87%') for x in texts(off)))

    def test_tiles_start_with_a_capital_and_doors_say_all_locked(self):
        data = {'panels': [{'label': 'Alarm', 'active': True, 'detail': 'Off'}, {'label': 'Doors locked', 'active': True, 'detail': ''},
                           {'label': 'Heat idle', 'active': True, 'detail': 'Hall 20.7° → 19°'}]}
        t = texts(data)
        self.assertIn('Locked', t); self.assertIn('Idle', t); self.assertIn('all locked', t)
        self.assertNotIn('locked', t); self.assertNotIn('idle', t)

    def test_energy_row_splits_price_and_todays_cost(self):
        data = {'panels': [{'label': 'Energy', 'active': True, 'detail': '31.9p now peak', 'kindle_left': '⚡ 31.9p now (peak) · 13.3p from 00:30',
                            'kindle_cost': 'Today ⚡ £7.68 🔥 £5.92'}]}
        t = texts(data)
        self.assertIn('⚡ 31.9p now (peak) · 13.3p from 00:30', t)
        self.assertIn('Today ⚡ £7.68 🔥 £5.92', t)

    def test_lights_are_listed_floor_by_floor(self):
        floors = [{'floor': 'Ground', 'rooms': []}, {'floor': '1st floor', 'rooms': ['Bedroom']},
                  {'floor': '2nd floor', 'rooms': ['Kids']}, {'floor': 'Outside', 'rooms': ['Garden', 'Bins', 'Shed']}]
        t = texts({'panels': [{'label': 'Lights on', 'active': True, 'detail': 'Bedroom, Kids, Garden, Bins, Shed', 'floors': floors}]})
        for label in ('Ground', '1st floor', '2nd floor', 'Outside'):
            self.assertIn(label, t)
        self.assertIn('–', t); self.assertIn('Garden, Bins, Shed', t)
        flat = texts({'panels': [{'label': 'Lights on', 'active': True, 'detail': 'Kitchen'}]})
        self.assertIn('Kitchen', flat); self.assertNotIn('Ground', flat)

    def test_message_rows_get_an_envelope_and_countdowns_are_lower_case(self):
        tasks = [{'title': 'Example car on 29%', 'detail': 'plug it in', 'display_group': 'message'},
                 {'title': 'Tue 24 Nov · Recycling', 'detail': 'In 5 days', 'display_group': 'collection'},
                 {'title': 'Other', 'detail': 'In 1 day', 'display_group': 'task'}]
        t = texts({'tasks': tasks, 'tasks_configured': True})
        self.assertIn(kindle_render.ENVELOPE + 'Example car on 29%', t)
        self.assertIn('in 5 days', t); self.assertIn('in 1 day', t)

class LightsPanelTests(unittest.TestCase):
    def panel(self, groups, on):
        ha = render.HA('x')
        ha.request = lambda path, body=None: {'state': 'on' if path.rsplit('/', 1)[1] in on else 'off'}
        return ha.lights_panel(groups)

    def test_source_settings_accept_an_optional_floor(self):
        row = {'name': 'Hall', 'entities': ['light.a']}
        self.assertTrue(source_settings.validate({'render_lights': [row]}))
        self.assertTrue(source_settings.validate({'render_lights': [dict(row, floor='Ground')]}))
        self.assertFalse(source_settings.validate({'render_lights': [dict(row, floor='')]}))
        self.assertFalse(source_settings.validate({'render_lights': [dict(row, floor=3)]}))
        self.assertFalse(source_settings.validate({'render_lights': [dict(row, extra='x')]}))

    def test_floor_is_optional_and_floors_are_ordered_by_first_appearance(self):
        groups = [{'name': 'Landing', 'floor': '1st floor', 'entities': ['light.a']}, {'name': 'Landing', 'floor': '2nd floor', 'entities': ['light.b']},
                  {'name': 'Garden', 'floor': 'Outside', 'entities': ['light.c']}]
        panel = self.panel(groups, {'light.b', 'light.c'})
        self.assertEqual(panel['floors'], [{'floor': '1st floor', 'rooms': []}, {'floor': '2nd floor', 'rooms': ['Landing']}, {'floor': 'Outside', 'rooms': ['Garden']}])
        self.assertEqual(panel['detail'], 'Landing, Garden')
        self.assertIsNone(self.panel([{'name': 'A', 'entities': ['light.a']}], {'light.a'})['floors'])

if __name__ == '__main__': unittest.main()
