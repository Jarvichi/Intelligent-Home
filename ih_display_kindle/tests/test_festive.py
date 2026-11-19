import datetime
import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import render, kindle_render

class FestiveTests(unittest.TestCase):
    def panel(self,day,states=None,fixed=None):
        ha=render.HA('')
        ha.optional_state=lambda entity: {'state':(states or {}).get(entity,'off')}
        return ha.festive_panel('sensor.birthdays' if states and 'sensor.birthdays' in states else '',
                                'input_boolean.halloween' if states and 'input_boolean.halloween' in states else '',
                                fixed,datetime.datetime.fromisoformat(day+'T12:00:00+00:00').timestamp())
    def test_special_days_and_themes(self):
        for day,theme in [('2026-10-31','halloween'),('2026-11-05','bonfire'),('2026-12-25','christmas'),('2027-01-01','newyear'),('2027-02-14','valentine'),('2027-03-28','other')]:
            with self.subTest(day=day):
                event=self.panel(day)
                self.assertTrue(event['event_today']);self.assertEqual(event['theme'],theme)
                image=kindle_render.svg({'panels':[event]},now=datetime.datetime.fromisoformat(day+'T12:00:00+00:00').timestamp())
                ET.fromstring(image);self.assertIn(b'height="214" fill="black"',image)
    def test_countdowns_nearest_first_and_split(self):
        event=self.panel('2026-12-10',{'sensor.birthdays':'Example|2'})
        self.assertEqual((event['countdown_days'],event['countdown_name']),(2,'Example’s birthday'))
        image=kindle_render.svg({'panels':[event]},now=0)
        self.assertIn(b'2 days:',image);self.assertIn('Example’s birthday'.encode(),image)
        self.assertNotIn(b'height="214" fill="black"',image)
        self.assertEqual(self.panel('2026-12-01')['countdown_days'],24)
    def test_toggle_and_halloween_day_are_separate(self):
        off=self.panel('2026-10-31',{'input_boolean.halloween':'off'})
        on=self.panel('2026-10-30',{'input_boolean.halloween':'on'})
        self.assertFalse(off['halloween_mode']);self.assertTrue(off['event_today'])
        self.assertTrue(on['halloween_mode']);self.assertEqual(on['detail'],'Haunted house')
        invalid=self.panel('2026-10-31',{'input_boolean.halloween':'unavailable'})
        self.assertFalse(invalid['available']);self.assertTrue(invalid['event_today'])
    def test_fixed_birthday_wraps_year(self):
        event=self.panel('2026-12-28',fixed=[{'name':'Example','month':1,'day':3}])
        self.assertEqual((event['countdown_days'],event['countdown_name']),(6,'Example’s birthday'))
    def test_halloween_art_and_weather_cat_follow_day_or_switch(self):
        halloween=datetime.datetime(2026,10,31,12,tzinfo=datetime.timezone.utc).timestamp()
        normal=datetime.datetime(2026,11,2,12,tzinfo=datetime.timezone.utc).timestamp()
        data={'weather':{'current':{'condition':'rainy'}}}
        for stamp,state in ((halloween,'off'),(normal,'on')):
            image=kindle_render.svg(dict(data,halloween_mode=state),now=stamp)
            ET.fromstring(image)
            self.assertIn(b'id="k_hw_h"',image);self.assertIn(b'id="k_hw_b"',image)
            self.assertIn(b'HAUNTED HOUSE',image);self.assertNotIn(b'id="weather-cat"',image)
        image=kindle_render.svg(dict(data,halloween_mode='off'),now=normal)
        self.assertNotIn(b'id="k_hw_h"',image);self.assertIn(b'id="weather-cat"',image)
