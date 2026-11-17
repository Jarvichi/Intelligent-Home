import datetime
import pathlib
import sys
import time
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import alerts

UTC=datetime.timezone.utc
NOW=datetime.datetime(2026,11,17,12,tzinfo=UTC).timestamp()
def iso(t):return datetime.datetime.fromtimestamp(t,UTC).isoformat()
def state(value,attrs=None,changed=None):return {'state':value,'attributes':attrs or {},'last_changed':iso(NOW if changed is None else changed)}
SMOKE='binary_sensor.smoke';CO='binary_sensor.co';SAVE='calendar.saving';POWER='event.power';DISMISS='input_text.dismiss';STORE='input_boolean.storage'
ROBOT={'name':'Example robot','kind':'vacuum','entity':'vacuum.example','last_completed':'sensor.completed','error':'sensor.error','last_error':'sensor.last_error','battery':'sensor.battery'}
MOWER={'name':'Example mower','kind':'mower','entity':'lawn_mower.example','last_completed':'sensor.mower_completed','error':'sensor.mower_error','last_error':'sensor.mower_last_error','battery':'sensor.mower_battery','blade':'sensor.mower_blade'}
def base():return {SMOKE:state('off'),CO:state('off')}
def make(values,robots=None,stored='',saving='',power='',dismissed='',now=NOW,smoke=None,co=CO):
    return alerts.Alerts(True,[SMOKE] if smoke is None else smoke,co,robots or [],stored,saving,power,dismissed,values.get,clock=lambda:now,localtime=time.gmtime)

class Tests(unittest.TestCase):
    def test_smoke_and_co_are_never_energy_dismissible(self):
        values=base();values.update({SMOKE:state('on'),CO:state('on'),POWER:state('ok',{'events':[{'start':iso(NOW-60),'end':iso(NOW+3600)}]}),DISMISS:state(iso(NOW-60))})
        result=make(values,power=POWER,dismissed=DISMISS).read();self.assertTrue(result['complete']);self.assertEqual([i['kind'] for i in result['alerts']],['smoke','co']);self.assertTrue(all(not i['dismissible'] for i in result['alerts']))
    def test_energy_banners_merge_order_and_dismissal_boundary(self):
        values=base();values.update({SAVE:state('on',{'start_time':iso(NOW-60),'end_time':iso(NOW+30*60)}),POWER:state('ok',{'events':[{'start':iso(NOW-60),'end':iso(NOW+3600)},{'start':iso(NOW+3600),'end':iso(NOW+7200)}]}),DISMISS:state(iso(NOW-5400))})
        result=make(values,saving=SAVE,power=POWER,dismissed=DISMISS).read();self.assertEqual([x['kind'] for x in result['alerts']],['free','saving']);self.assertIn('14:00',result['alerts'][0]['title']);self.assertTrue(all(x['dismissible'] for x in result['alerts']))
        values[DISMISS]=state(iso(NOW-5399));self.assertEqual(make(values,saving=SAVE,power=POWER,dismissed=DISMISS).read()['alerts'],[])
    def test_missing_dismissal_suppresses_only_energy_and_warns(self):
        values=base();values.update({SMOKE:state('on'),POWER:state('ok',{'events':[{'start':iso(NOW-60),'end':iso(NOW+3600)}]})})
        result=make(values,power=POWER,dismissed=DISMISS).read();self.assertFalse(result['complete']);self.assertEqual([i['kind'] for i in result['alerts']],['smoke'])
    def test_current_error_and_unknown_code(self):
        values=base();values.update({'vacuum.example':state('idle'),'sensor.completed':state(iso(NOW-86400)),'sensor.error':state('520'),'sensor.last_error':state('unknown'),'sensor.battery':state('90')})
        result=make(values,[ROBOT]).read();self.assertTrue(result['complete']);self.assertEqual(result['alerts'][0]['title'],'Example robot: stuck');self.assertFalse(result['alerts'][0]['dismissible'])
        values['sensor.error']=state('222');self.assertEqual(make(values,[ROBOT]).read()['alerts'],[])
        values['sensor.error']=state('222',{'description':'Water tank empty'});self.assertEqual(make(values,[ROBOT]).read()['alerts'][0]['title'],'Example robot: water tank empty')
    def test_idle_alone_not_alert_and_lost_boundary(self):
        values=base();values.update({'vacuum.example':state('idle'),'sensor.completed':state(iso(NOW-86400)),'sensor.error':state('0'),'sensor.last_error':state('unknown'),'sensor.battery':state('100')})
        self.assertEqual(make(values,[ROBOT]).read()['alerts'],[])
        values['vacuum.example']=state('cleaning',changed=NOW-9000);self.assertEqual(make(values,[ROBOT]).read()['alerts'],[])
        values['vacuum.example']=state('cleaning',changed=NOW-9001);self.assertIn('lost?',make(values,[ROBOT]).read()['alerts'][0]['title'])
    def test_stopped_requires_recent_new_error_and_battery_guard(self):
        values=base();values.update({'vacuum.example':state('error'),'sensor.completed':state(iso(NOW-86400)),'sensor.error':state('0'),'sensor.last_error':state(iso(NOW-3600),{'code':123}),'sensor.battery':state('98')})
        self.assertEqual(make(values,[ROBOT]).read()['alerts'],[])
        values['sensor.battery']=state('97');self.assertIn('stopped, error 123',make(values,[ROBOT]).read()['alerts'][0]['title'])
        values['sensor.last_error']=state(iso(NOW-86400),{'code':123});self.assertEqual(make(values,[ROBOT]).read()['alerts'],[])
    def test_mower_winter_and_blade_fallback(self):
        values=base();values.update({STORE:state('on'),'lawn_mower.example':state('error'),'sensor.mower_completed':state('unknown'),'sensor.mower_error':state('unknown'),'sensor.mower_last_error':state('unknown'),'sensor.mower_battery':state('90'),'sensor.mower_blade':state('3')})
        self.assertEqual(make(values,[MOWER],stored=STORE).read()['alerts'],[])
        values[STORE]=state('off');self.assertIn('blades worn out',make(values,[MOWER],stored=STORE).read()['alerts'][0]['title'])
        values[STORE]=state('unavailable');result=make(values,[MOWER],stored=STORE).read();self.assertFalse(result['complete']);self.assertEqual(result['alerts'],[])
    def test_unconfigured_and_missing_safety_input(self):
        self.assertFalse(alerts.Alerts(False,[], '',[],'','','','',lambda _:None).read()['configured'])
        result=make({SMOKE:state('on')}).read();self.assertFalse(result['complete']);self.assertEqual(result['alerts'][0]['kind'],'smoke')
        self.assertFalse(make(base(),smoke=[]).read()['complete'])
        self.assertFalse(make(base(),co='bad/path').read()['complete'])
    def test_invalid_other_sources_cannot_hide_smoke(self):
        values=base();values[SMOKE]=state('on')
        for kwargs in ({'robots':[{'name':'invalid'}]}, {'power':'bad/path'}, {'stored':'bad/path'}, {'smoke':[SMOKE,'bad/path']}):
            with self.subTest(kwargs=kwargs):
                result=make(values,**kwargs).read()
                self.assertFalse(result['complete']);self.assertEqual([x['kind'] for x in result['alerts']],['smoke'])
    def test_unavailable_sources_never_report_complete(self):
        values=base();values.update({SMOKE:state('on'),POWER:state('unavailable',{'events':[]})})
        result=make(values,power=POWER).read();self.assertFalse(result['complete']);self.assertEqual(result['alerts'][0]['kind'],'smoke')
        values[POWER]=state('ok',{'events':[{'start':iso(NOW-60),'end':iso(NOW+60)}]});values[DISMISS]=state('unavailable')
        result=make(values,power=POWER,dismissed=DISMISS).read();self.assertFalse(result['complete']);self.assertEqual([x['kind'] for x in result['alerts']],['smoke'])

if __name__=='__main__':unittest.main()
