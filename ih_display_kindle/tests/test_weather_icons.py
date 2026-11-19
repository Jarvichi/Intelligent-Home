import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import festive_header
import kindle_render
import weather_icons

class WeatherIconsTests(unittest.TestCase):
    def test_legacy_conditions_have_distinct_expected_symbols(self):
        expected = {'sunny':'☀', 'clear-night':'🌙', 'rainy':'🌦',
                    'fog':'🌫', 'windy':'💨', 'lightning':'⚡',
                    'snowy':'❄', 'partlycloudy':'⛅'}
        for condition, value in expected.items():
            self.assertEqual(weather_icons.symbol(condition), value)
        self.assertEqual(weather_icons.symbol('not a condition'), '')
        self.assertEqual(weather_icons.symbol('clear_night'), '🌙')

    def test_render_uses_monochrome_font_for_weather_in_each_section(self):
        data={'weather': {'current': {'condition':'windy'}, 'daily': [
              {'label':'Tomorrow','condition':'fog'}],
              'hours': [{'condition':'clear-night'}]}}
        root=ET.fromstring(kindle_render.svg(data, now=0))
        icons=[node.text for node in root.iter('{http://www.w3.org/2000/svg}text')
               if node.get('font-family')=='Symbola, sans-serif']
        self.assertEqual(icons, ['💨︎','🌫︎','🌙︎'])

    def test_occasion_icons_include_birthday_and_christmas(self):
        self.assertEqual(festive_header.lines({'active':True,'detail':'Happy Halloween!'}), ['🎃 Happy Halloween!'])
        self.assertEqual(festive_header.lines({'active':True,'countdown_days':3,'countdown_name':'Example’s birthday'}), ['3 days:','🎁 Example’s birthday'])
        self.assertEqual(festive_header.lines({'active':True,'countdown_days':2,'countdown_name':'Christmas'}), ['2 sleeps:','🎄 Christmas'])

if __name__=='__main__': unittest.main()
