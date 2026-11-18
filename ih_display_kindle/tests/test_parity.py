import datetime
import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import device_state,kindle_render,source_settings

class HA:
    def __init__(self,values):self.values=values
    def optional_state(self,key):return self.values.get(key)
class Tests(unittest.TestCase):
    def test_messages_and_collections_stay_on_every_five_minute_page(self):
        rows=[{'title':'Message','display_group':'message'}]+[{'title':f'Task {n}','display_group':'task'} for n in range(5)]+[{'title':'Collection','display_group':'collection'}]
        for now,expected in [(0,['Message','Task 0','Task 1','Collection']),(299,['Message','Task 0','Task 1','Collection']),(300,['Message','Task 2','Task 3','Collection']),(600,['Message','Task 4','Collection'])]:
            page,_,pages,grouped=kindle_render.task_page(rows,0,now);self.assertEqual([x['title'] for x in page],expected);self.assertEqual(pages,3);self.assertTrue(grouped)
        page,_,_,_=kindle_render.task_page(rows,-1,0);self.assertEqual(page[1]['title'],'Task 4')
    def test_old_feed_degrades_without_falsely_claiming_grouping(self):
        rows=[{'title':str(i)} for i in range(6)];page,index,count,grouped=kindle_render.task_page(rows,0,300)
        self.assertEqual((len(page),index,count,grouped),(1,1,2,False))
    def test_power_status_requires_recent_heartbeat(self):
        now=datetime.datetime(2030,1,1,12,tzinfo=datetime.timezone.utc).timestamp();battery={'state':'63','attributes':{'charging':True}}
        def check(age):
            seen=datetime.datetime.fromtimestamp(now-age,datetime.timezone.utc).isoformat()
            return device_state.read(HA({'sensor.b':battery,'sensor.s':{'state':seen},'input_text.p':{'state':'-1'}}),'sensor.b','sensor.s','input_text.p',now)
        self.assertEqual((check(10)['charging'],check(10)['battery'],check(10)['page_offset']),(True,63,-1))
        self.assertFalse(check(3600)['charging']);self.assertIsNone(check(3600)['battery'])
    def test_night_heartbeat_allows_display_but_not_stale_controls(self):
        seen=datetime.datetime(2030,1,2,0,tzinfo=datetime.timezone.utc);now=seen.timestamp()+45*60
        ha=HA({'sensor.b':{'state':'63.9','attributes':{'charging':'true'}},'sensor.s':{'state':seen.isoformat()}})
        result=device_state.read(ha,'sensor.b','sensor.s',now=now)
        self.assertTrue(result['fresh']);self.assertEqual(result['battery'],63);self.assertFalse(result['charging'])

    def test_controls_follow_power_and_short_list_shows_rooms(self):
        rows=[{'title':'Example','display_group':'task'}];base={'tasks':rows,'panels':[{'kind':'room','label':'Room','detail':'19.2°'}]}
        cold=kindle_render.svg(dict(base,device={'charging':False}),{'configured':True,'complete':True,'alerts':[]},now=0)
        active=kindle_render.svg(dict(base,device={'charging':True,'fresh':True,'battery':80}),{'configured':True,'complete':True,'alerts':[]},now=0)
        for image in (cold,active):ET.fromstring(image);self.assertIn(b'ROOMS',image);self.assertIn('19.2°'.encode(),image)
        self.assertNotIn(b'ALL OFF',cold);self.assertIn(b'ALL OFF',active);self.assertIn(b'Plugged in',active)
    def test_media_and_game_overlays_and_safety_precedence(self):
        weather={'current':{'temperature':10},'facts':{},'daily':[{'label':'Tomorrow'}],'hours':[{'time':'11:00'}]}
        data={'weather':weather,'panels':[{'active':True,'kind':'media','label':'Speaker','detail':'Example song · Example artist'},{'active':True,'kind':'game','label':'Console','detail':'Example game'}]}
        image=kindle_render.svg(data,{'configured':True,'complete':True,'alerts':[]},now=0);ET.fromstring(image)
        self.assertIn(b'Example song',image);self.assertIn(b'Example artist',image);self.assertIn(b'Example game',image);self.assertNotIn(b'11:00',image)
        image=kindle_render.svg(data,{'configured':True,'complete':True,'alerts':[{'kind':'smoke','title':'SMOKE ALARM'}]},now=0)
        self.assertIn(b'SMOKE ALARM',image)

    def test_media_and_game_remain_visible_when_weather_is_unavailable(self):
        data={'weather':None,'weather_configured':True,'panels':[{'active':True,'kind':'media','detail':'Example song'},{'active':True,'kind':'game','detail':'Example game'}]}
        image=kindle_render.svg(data,{'configured':True,'complete':True,'alerts':[]},now=0)
        self.assertIn(b'Weather unavailable',image);self.assertIn(b'Example song',image);self.assertIn(b'Example game',image)

    def test_source_mapping_validates_device_sources(self):
        self.assertTrue(source_settings.validate({'kindle_battery_entity':'sensor.example','kindle_last_seen_entity':'sensor.heartbeat','kindle_page_entity':'input_text.page'}))
        self.assertFalse(source_settings.validate({'kindle_battery_entity':'input_text.invalid'}))

if __name__=='__main__':unittest.main()
