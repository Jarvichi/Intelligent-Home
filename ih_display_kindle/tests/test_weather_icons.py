import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import festive_header
import kindle_render
import weather_icons

class WeatherIconsTests(unittest.TestCase):
    def test_every_home_assistant_condition_has_its_own_drawn_icon(self):
        conditions=['clear-night','cloudy','exceptional','fog','hail','lightning','lightning-rainy','partlycloudy','pouring','rainy','snowy','snowy-rainy','sunny','windy','windy-variant']
        drawn={c:weather_icons.svg(c,0,0,100) for c in conditions}
        for condition,value in drawn.items():
            self.assertTrue(value.startswith('<g '), condition)
            ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg">'+value+'</svg>')   # well-formed
        # windy and windy-variant share a drawing; every other condition looks different
        self.assertEqual(len({v.replace(k,'') for k,v in drawn.items() if k!='windy-variant'}), len(conditions)-1)
        self.assertEqual(weather_icons.svg('not a condition',0,0,100), '')
        self.assertEqual(weather_icons.svg('clear_night',0,0,100).replace('clear-night',''), drawn['clear-night'].replace('clear-night',''))

    def test_icons_come_from_the_vendored_set_in_greys(self):
        import re
        for condition in weather_icons.FILES:
            body=weather_icons.ICONS[condition]()
            self.assertNotIn('filter',body)
            self.assertFalse(set(re.findall(r'#[0-9a-fA-F]{3,6}\b',body))-{'#fff','#000','#1d1d1d','#222','#333','#444','#555','#777'}, condition)
        self.assertTrue((weather_icons.HERE/'LICENSE').exists())

    def test_render_draws_icons_without_needing_a_font(self):
        data={'weather': {'current': {'condition':'windy'}, 'daily': [
              {'label':'Tomorrow','condition':'fog'}],
              'hours': [{'condition':'clear-night'}]}}
        root=ET.fromstring(kindle_render.svg(data, now=0))
        ns='{http://www.w3.org/2000/svg}'
        labels=[g.get('aria-label') for g in root.iter(ns+'g') if g.get('aria-label')]
        self.assertEqual(labels, ['windy','fog','clear-night'])
        self.assertFalse([t for t in root.iter(ns+'text') if t.get('font-family')=='Symbola, sans-serif'])

    def test_occasion_icons_include_birthday_and_christmas(self):
        self.assertEqual(festive_header.lines({'active':True,'detail':'Happy Halloween!'}), ['🎃 Happy Halloween!'])
        self.assertEqual(festive_header.lines({'active':True,'countdown_days':3,'countdown_name':'Example’s birthday'}), ['3 days:','🎁 Example’s birthday'])
        self.assertEqual(festive_header.lines({'active':True,'countdown_days':2,'countdown_name':'Christmas'}), ['2 sleeps:','🎄 Christmas'])

if __name__=='__main__': unittest.main()
