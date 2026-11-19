import datetime
import pathlib
import sys
import time
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import household_tasks

UTC=datetime.timezone.utc
NOW=datetime.datetime(2026,11,17,18,0,tzinfo=UTC).timestamp()
def iso(t):return datetime.datetime.fromtimestamp(t,UTC).isoformat()
def value(state,changed=NOW,attributes=None):return {'state':state,'last_changed':iso(changed),'attributes':attributes or {}}
def make(values,now=NOW):return household_tasks.EnergyReminders('input_text.booking','input_text.wheel','input_boolean.oven','event.power',values.get,clock=lambda:now,localtime=time.gmtime,provider='Example')

class Tests(unittest.TestCase):
    def test_booking_failure_expires_at_three_days(self):
        values={'input_text.booking':value('failed something',NOW-3*86400+1),'input_text.wheel':value(''),'input_boolean.oven':value('off')}
        feed=make(values).read();self.assertTrue(feed['complete']);self.assertEqual(len(feed['items']),1);self.assertEqual(feed['items'][0]['detail'],'Book in the Example app')
        values['input_text.booking']['last_changed']=iso(NOW-3*86400);self.assertEqual(make(values).read()['items'],[])
    def test_wheel_same_day_positive_count(self):
        values={'input_text.booking':value(''),'input_text.wheel':value('spun:3'),'input_boolean.oven':value('off')}
        out=make(values).read();self.assertEqual(out['items'][0]['title'],'🎡 Spun the Example wheel ×3');self.assertEqual(out['items'][0]['action_identity'],{'state':'spun:3','last_changed':iso(NOW)})
        values['input_text.wheel']['last_changed']=iso(NOW-86400);self.assertEqual(make(values).read()['items'],[])
        values['input_text.wheel']=value('spun:0');self.assertEqual(make(values).read()['items'],[])
    def test_oven_chooses_earliest_active_or_future_event(self):
        events=[{'start':iso(NOW+3600),'end':iso(NOW+7200)},{'start':iso(NOW-600),'end':iso(NOW+600)}]
        values={'input_text.booking':value(''),'input_text.wheel':value(''),'input_boolean.oven':value('on'),'event.power':value('ok',attributes={'events':events})}
        out=make(values).read();self.assertEqual(out['items'][0]['title'],'🔥 Oven needs a clean');self.assertEqual(out['items'][0]['detail'],'Run it in the free hour · today 17:50')
        values['event.power']=None;out=make(values).read();self.assertFalse(out['complete']);self.assertEqual(out['items'][0]['detail'],'Run it in the next free hour')
    def test_all_reminders_in_order_and_invalid_sources(self):
        values={'input_text.booking':value('failed'),'input_text.wheel':value('spun:2'),'input_boolean.oven':value('on'),'event.power':value('ok',attributes={'events':[]})}
        out=make(values).read();self.assertEqual(len(out['items']),3);self.assertIn('booking failed',out['items'][0]['title']);self.assertIn('wheel',out['items'][1]['title']);self.assertIn('Oven',out['items'][2]['title'])
        values['event.power']=value('ok',attributes={'events':[{'start':'invalid'}]});self.assertFalse(make(values).read()['complete'])
        values['input_text.booking']=value('unavailable');self.assertFalse(make(values).read()['complete'])
        self.assertFalse(household_tasks.EnergyReminders('bad/path','','','',values.get).read()['complete'])
    def test_tomorrow_time_all_day_and_length(self):
        items={'sensor.event':value('09:00|Example meeting')};factory=lambda now:household_tasks.Tomorrow('sensor.event',items.get,clock=lambda:now,localtime=time.gmtime)
        self.assertEqual(factory(NOW-1).read()['items'],[])
        self.assertEqual(factory(NOW).read()['items'][0]['title'],'📅 Tomorrow 09:00 · Example meeting')
        items['sensor.event']=value('All day|Example event');self.assertEqual(factory(NOW).read()['items'][0]['title'],'📅 Tomorrow Example event')
        items['sensor.event']=value('10:00|'+('x'*100));title=factory(NOW).read()['items'][0]['title'];self.assertTrue(title.endswith('…'));self.assertLessEqual(len(title.encode('utf-16-le'))//2,62)
    def test_empty_missing_and_no_writes(self):
        calls=[]
        def read(entity):calls.append(entity);return None
        source=household_tasks.Tomorrow('sensor.event',read,clock=lambda:NOW,localtime=time.gmtime)
        self.assertFalse(source.read()['complete']);self.assertEqual(calls,['sensor.event'])
        self.assertFalse(household_tasks.Tomorrow('',read).read()['configured'])
        self.assertFalse(household_tasks.EnergyReminders('','','','',read).read()['configured'])

if __name__=='__main__':unittest.main()
