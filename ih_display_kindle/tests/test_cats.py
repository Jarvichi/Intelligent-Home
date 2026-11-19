import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import cat_render


class CatParityTests(unittest.TestCase):
    def test_original_five_minute_hash_and_spots(self):
        self.assertEqual(cat_render.selection(300, None), (None, None, {'tux': 'c'}))
        self.assertEqual(cat_render.selection(599, None), (None, None, {'tux': 'c'}))
        self.assertEqual(cat_render.selection(600, None), (None, None, {}))
        self.assertEqual(cat_render.selection(1700000000, None), (None, None, {'calico': 'a'}))

    def test_weather_conditions_hourly_breeds_and_halloween(self):
        for condition, scene in cat_render.CONDITIONS.items():
            self.assertEqual(cat_render.selection(0, condition)[:2], (scene, 'calico'))
            self.assertEqual(cat_render.selection(3600, condition)[:2], (scene, 'tux'))
        self.assertEqual(cat_render.selection(0, 'cloudy')[:2], (None, None))
        self.assertEqual(cat_render.selection(0, 'rainy', True)[:2], (None, None))
        self.assertIn('rotate(12)', cat_render.render(300, 'rainy', True))
        self.assertNotIn('weather-cat', cat_render.render(300, 'rainy', True))

    def test_breeds_do_not_repeat_and_spots_do_not_collide(self):
        for slot in range(10000):
            _, breed, visitors = cat_render.selection(slot * 300, 'rainy')
            self.assertNotIn(breed, visitors)
            self.assertEqual(len(set(visitors.values())), len(visitors))

    def test_all_weather_scenes_are_valid_svg_and_have_original_art(self):
        for condition in cat_render.CONDITIONS:
            for timestamp in (0, 3600):
                fragment = cat_render.render(timestamp, condition)
                root = ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg">' + fragment + '</svg>')
                self.assertTrue(any(e.attrib.get('id') == 'weather-cat' for e in root.iter()))
                self.assertIn('class="wcat"', fragment)
        self.assertIn('cat_tux_c', cat_render.P)
        self.assertIn('translate(330,1148)', cat_render.render(300))


if __name__ == '__main__':
    unittest.main()
