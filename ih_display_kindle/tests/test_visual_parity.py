import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import kindle_render


class VisualParityTests(unittest.TestCase):
    def render(self, data, alerts=None):
        return ET.fromstring(kindle_render.svg(data, alerts or {'configured': True, 'complete': True, 'alerts': []}, now=0))

    def test_wind_arrow_points_towards_direction_and_missing_bearing_has_no_arrow(self):
        weather = {'current': {}, 'facts': {'bearing': 90}, 'daily': [], 'hours': []}
        root = self.render({'weather': weather})
        arrow = root.find(".//*[@aria-label='Wind direction']")
        self.assertEqual(arrow.get('transform'), 'translate(585 289) rotate(270)')
        weather['facts']['bearing'] = None
        self.assertIsNone(self.render({'weather': weather}).find(".//*[@aria-label='Wind direction']"))

    def test_unlocked_door_is_inverted_but_locked_door_is_not(self):
        for urgent, expected in ((True, 'black'), (False, 'white')):
            root = self.render({'panels': [{'label': 'Doors unlocked' if urgent else 'Doors locked', 'urgent': urgent, 'detail': 'Example' if urgent else ''}]})
            tile = root.find(".//{*}rect[@x='544'][@y='726']")
            self.assertEqual(tile.get('fill'), expected)
            title = root.find(".//{*}text[@x='564'][@y='761']")
            self.assertEqual(title.get('fill'), 'white' if urgent else 'black')

    def test_warning_keeps_safety_messages_and_dismiss_button(self):
        alerts = {'configured': True, 'complete': True, 'alerts': [{'kind': 'smoke', 'title': 'SMOKE ALARM'}, {'kind': 'free', 'title': 'FREE ELECTRICITY', 'dismissible': True}]}
        root = self.render({'allow_alert_dismiss': True}, alerts)
        content = ''.join(root.itertext())
        for value in ('SMOKE ALARM', 'FREE ELECTRICITY', 'DISMISS'):
            self.assertIn(value, content)
        self.assertIsNotNone(root.find(".//{*}rect[@stroke-dasharray='14 10']"))


if __name__ == '__main__':
    unittest.main()
