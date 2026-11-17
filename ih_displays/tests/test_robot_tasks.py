import datetime
import pathlib
import sys
import time
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import robot_tasks

UTC=datetime.timezone.utc
NOW=datetime.datetime(2026,7,10,12,tzinfo=UTC).timestamp()
def iso(ts):return datetime.datetime.fromtimestamp(ts,UTC).isoformat()
def entity(value,attrs=None):return {'state':value,'attributes':attrs or {}}
VAC={'name':'Example cleaner','kind':'vacuum','entity':'vacuum.example','last_completed':'sensor.completed','last_active':'sensor.active','error':'sensor.error','last_error':'sensor.last_error','battery':'sensor.battery','blade':''}
MOWER={'name':'Example mower','kind':'mower','entity':'lawn_mower.example','last_completed':'sensor.completed','last_active':'sensor.active','error':'sensor.error','last_error':'sensor.last_error','battery':'sensor.battery','blade':'sensor.blade'}
def make(values,robot=VAC,now=NOW,stored='',weather='',forecast='',rain='',fallback=''):
    return robot_tasks.Robots([robot],weather,forecast,rain,stored,values.get,clock=lambda:now,localtime=time.gmtime,forecast_fallback=fallback)
def basic(robot=VAC,elapsed=27*3600):return {robot['entity']:entity('docked'),'sensor.completed':entity(iso(NOW-elapsed)),'sensor.active':entity('unknown')}

class Tests(unittest.TestCase):
    def test_missed_run_and_recent_activity(self):
        values=basic();out=make(values).read();self.assertTrue(out['complete']);self.assertIn('hasn’t run today',out['items'][0]['title'])
        values['sensor.active']=entity(iso(NOW-60));self.assertEqual(make(values).read()['items'],[])
        values['sensor.active']=entity('unknown');values['sensor.completed']=entity(iso(NOW-26*3600));self.assertEqual(make(values).read()['items'],[])
    def test_offline_winter_storage_and_season(self):
        values=basic(MOWER);values[MOWER['entity']]=entity('unavailable');values['input_boolean.store']=entity('on')
        self.assertEqual(make(values,MOWER,stored='input_boolean.store').read()['items'],[])
        values['input_boolean.store']=entity('off');self.assertIn('offline',make(values,MOWER,stored='input_boolean.store').read()['items'][0]['title'])
        values['input_boolean.store']=None;out=make(values,MOWER,stored='input_boolean.store').read();self.assertFalse(out['complete']);self.assertEqual(out['items'],[])
        values[MOWER['entity']]=entity('docked');winter=datetime.datetime(2026,12,10,tzinfo=UTC).timestamp();self.assertEqual(make(values,MOWER,now=winter).read()['items'],[])
    def test_autumn_mower_five_day_threshold(self):
        now=datetime.datetime(2026,9,20,tzinfo=UTC).timestamp();values=basic(MOWER);values['sensor.completed']=entity(iso(now-4*86400));self.assertEqual(make(values,MOWER,now).read()['items'],[])
        values['sensor.completed']=entity(iso(now-6*86400));self.assertIn('6 days',make(values,MOWER,now).read()['items'][0]['title'])
    def test_error_blade_battery_reason_precedence(self):
        values=basic(MOWER);values.update({'sensor.error':entity('520'),'sensor.battery':entity('10'),'sensor.blade':entity('4')})
        self.assertEqual(make(values,MOWER).read()['items'][0]['detail'],'stuck · error 520')
        values['sensor.error']=entity('0');self.assertEqual(make(values,MOWER).read()['items'][0]['detail'],'blades worn out')
        values['sensor.blade']=entity('30');self.assertEqual(make(values,MOWER).read()['items'][0]['detail'],'battery low 10%')
        values['sensor.battery']=entity('80');self.assertEqual(make(values,MOWER).read()['items'][0]['detail'],'cause unknown')
    def test_last_error_and_unknown_error_code(self):
        values=basic();values['sensor.error']=entity('222');values['sensor.last_error']=entity(iso(NOW-100),{'code':520})
        self.assertEqual(make(values).read()['items'][0]['detail'],'stuck · error 520')
        values['sensor.last_error']=entity('unknown');self.assertEqual(make(values).read()['items'][0]['detail'],'cause unknown')
    def test_weather_reasons_and_fallback(self):
        values=basic(MOWER);values['weather.example']=entity('rainy');values['sensor.forecast']=entity('ok',{'daily':[{'precipitation':3}]})
        self.assertEqual(make(values,MOWER,weather='weather.example',forecast='sensor.forecast').read()['items'][0]['detail'],'raining now')
        values['weather.example']=entity('sunny');self.assertEqual(make(values,MOWER,weather='weather.example',forecast='sensor.forecast').read()['items'][0]['detail'],'rain today · 3 mm')
        values['sensor.forecast']=None;values['sensor.backup']=entity('ok',{'daily':[{'precipitation':1}]})
        self.assertEqual(make(values,MOWER,forecast='sensor.forecast',fallback='sensor.backup').read()['items'][0]['detail'],'rain today · 1 mm')
        values['sensor.backup']=entity('ok',{'daily':[{'precipitation':0}]});values['sensor.rain']=entity(iso(NOW-1800))
        self.assertEqual(make(values,MOWER,fallback='sensor.backup',rain='sensor.rain').read()['items'][0]['detail'],'wet lawn? rain <1h ago')
    def test_missing_and_invalid_sources_are_not_silent(self):
        values=basic();values['sensor.completed']=None;out=make(values).read();self.assertFalse(out['complete']);self.assertEqual(out['items'],[])
        self.assertFalse(make(values,dict(VAC,entity='../bad')).read()['complete'])
        self.assertFalse(robot_tasks.Robots([], '', '', '', '',values.get).read()['configured'])

if __name__=='__main__':unittest.main()
