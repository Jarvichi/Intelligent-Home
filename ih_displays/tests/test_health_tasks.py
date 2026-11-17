import datetime
import pathlib
import sys
import time
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import health_tasks

UTC=datetime.timezone.utc
NOW=datetime.datetime(2026,11,17,14,tzinfo=UTC).timestamp()
def iso(t):return datetime.datetime.fromtimestamp(t,UTC).isoformat()
def val(state,attributes=None):return {'state':state,'attributes':attributes or {}}
def make(keys,values,now=NOW):return health_tasks.Health(keys,'Example display',values.get,clock=lambda:now,localtime=time.gmtime)

class Tests(unittest.TestCase):
    def test_dns_and_smoke_flags_are_urgent(self):
        keys={'dns':'binary_sensor.dns','smoke':'binary_sensor.smoke'};values={'binary_sensor.dns':val('on'),'binary_sensor.smoke':val('on')}
        feed=make(keys,values).read();self.assertTrue(feed['complete']);self.assertEqual(len(feed['items']),2);self.assertTrue(all(i['urgent'] for i in feed['items']))
        values['binary_sensor.smoke']=val('unavailable');feed=make(keys,values).read();self.assertFalse(feed['complete']);self.assertEqual(len(feed['items']),1)
    def test_backup_uses_strict_three_day_boundary(self):
        keys={'backup':'sensor.backup'};values={'sensor.backup':val(iso(NOW-3*86400))}
        self.assertEqual(make(keys,values).read()['items'],[])
        values['sensor.backup']=val(iso(NOW-3*86400-1));feed=make(keys,values).read();self.assertIn('3 days',feed['items'][0]['title']);self.assertTrue(feed['items'][0]['urgent'])
        values['sensor.backup']=val('invalid');self.assertFalse(make(keys,values).read()['complete'])
    def test_frost_times_and_forecast_fallback(self):
        keys={'forecast':'sensor.primary','forecast_fallback':'sensor.backup'}
        values={'sensor.primary':None,'sensor.backup':val('ok',{'daily':[{'templow':1},{'templow':3}]})}
        morning=datetime.datetime(2026,11,17,8,tzinfo=UTC).timestamp();self.assertIn('this morning',make(keys,values,morning).read()['items'][0]['title'])
        self.assertEqual(make(keys,values,NOW).read()['items'],[])
        evening=datetime.datetime(2026,11,17,18,tzinfo=UTC).timestamp();self.assertIn('tonight',make(keys,values,evening).read()['items'][0]['title'])
        values['sensor.backup']=val('ok',{'daily':[]});self.assertFalse(make(keys,values,evening).read()['complete'])
        midmorning=datetime.datetime(2026,11,17,10,tzinfo=UTC).timestamp();self.assertTrue(make(keys,values,midmorning).read()['complete'])
    def test_health_conditions_combine_in_original_order(self):
        keys={'disk':'sensor.disk','temperature':'sensor.temp','offline':'sensor.offline'}
        values={'sensor.disk':val('9'),'sensor.temp':val('81'),'sensor.offline':val('16')}
        feed=make(keys,values).read();self.assertEqual(feed['items'][0]['title'],'⚠ Home Assistant · disk almost full');self.assertEqual(feed['items'][0]['detail'],'Pi too hot · 16 devices offline')
        self.assertTrue(feed['items'][0]['urgent']);values['sensor.disk']=val('10');values['sensor.temp']=val('80');values['sensor.offline']=val('14');self.assertEqual(make(keys,values).read()['items'],[])
    def test_display_heartbeat_and_charge_respect_modes(self):
        keys={'display_seen':'sensor.seen','display_battery':'sensor.battery'}
        values={'sensor.seen':val(iso(NOW-31*60)),'sensor.battery':val('9',{'charging':False})}
        feed=make(keys,values).read();self.assertEqual(len(feed['items']),1);self.assertIn('hasn’t checked in',feed['items'][0]['title'])
        values['sensor.seen']=val(iso(NOW-60));feed=make(keys,values).read();self.assertIn('Charge the',feed['items'][0]['title']);self.assertTrue(feed['items'][0]['urgent'])
        values['sensor.battery']=val('25.9',{'charging':False});self.assertIn('25%',make(keys,values).read()['items'][0]['title'])
        values['sensor.battery']=val('9',{'charging':True});self.assertEqual(make(keys,values).read()['items'],[])
        night=datetime.datetime(2026,11,18,0,10,tzinfo=UTC).timestamp();values['sensor.seen']=val(iso(night-60*60));values['sensor.battery']=val('80',{'charging':False});self.assertEqual(make(keys,values,night).read()['items'],[])
    def test_invalid_and_empty_configuration(self):
        self.assertFalse(make({},{}).read()['configured'])
        self.assertFalse(make({'disk':'../bad'},{}).read()['complete'])
        self.assertFalse(make({'disk':'sensor.disk'},{'sensor.disk':val('unknown')}).read()['complete'])
        one=make({'display_seen':'sensor.seen'},{'sensor.seen':val(iso(NOW-7200))}).read();self.assertFalse(one['complete']);self.assertEqual(one['items'],[])

if __name__=='__main__':unittest.main()
