import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import battery_tasks
import registry_snapshot

ID1='a'*32;ID2='b'*32;ID3='c'*32

def state(eid,value,attrs=None):return {'entity_id':eid,'state':value,'attributes':attrs or {}}
def base():
    return {'states':[state('sensor.example_forecast','ok',{'forecast':{}}),state('input_text.example_ignored','')],
            'entities':[],'devices':[],'areas':[{'area_id':'area','name':'Sample room'}]}
def add(snap,eid,value,device_id=ID1,model='EXAMPLE',name='Door',domain='sensor'):
    snap['states'].append(state(eid,value,{'device_class':'battery'}));snap['entities'].append({'entity_id':eid,'device_id':device_id,'area_id':None})
    if not any(d['id']==device_id for d in snap['devices']):snap['devices'].append({'id':device_id,'model':model,'model_id':None,'area_id':'area','name':name})
def config():return {'battery_enabled':True,'battery_forecast_entity':'sensor.example_forecast','battery_ignored_entity':'input_text.example_ignored','battery_exclude_tokens':[],'battery_models':[{'model':'EXAMPLE','type':'2×AA'}],'battery_device_overrides':[],'maintenance_groups':[]}

class Tests(unittest.TestCase):
    def test_forecast_and_urgent_thresholds(self):
        snap=base();add(snap,'sensor.first_battery','30');snap['states'][0]['attributes']['forecast']={'sensor.first_battery':{'d':6.2}}
        result=battery_tasks.BatteryMaintenance(config(),lambda:snap).read();self.assertTrue(result['complete']);item=result['items'][0]
        self.assertIn('~7 days',item['title']);self.assertIn('2×AA',item['title']);self.assertTrue(item['urgent'])
        snap['states'][0]['attributes']['forecast']['sensor.first_battery']['d']=20
        self.assertEqual(battery_tasks.BatteryMaintenance(config(),lambda:snap).read()['items'],[])
        snap['states'][-1]['state']='5';self.assertTrue(battery_tasks.BatteryMaintenance(config(),lambda:snap).read()['items'][0]['urgent'])
    def test_fallback_binary_dropouts_and_offline(self):
        snap=base();add(snap,'sensor.first_battery','15');add(snap,'binary_sensor.second_battery','on',ID2,name='Switch');add(snap,'sensor.third_battery','unavailable',ID3,name='Window')
        out=battery_tasks.BatteryMaintenance(config(),lambda:snap).read();self.assertEqual(len(out['items']),3);self.assertIn('15%',out['items'][0]['title']);self.assertIn('low',out['items'][1]['title']);self.assertIn('offline',out['items'][2]['title'])
        snap['states'][2]['state']='80';snap['states'][0]['attributes']['forecast']={'sensor.first_battery':{'d':None,'f':2}}
        self.assertIn('dropping out',battery_tasks.BatteryMaintenance(config(),lambda:snap).read()['items'][0]['title'])
    def test_ignore_exclude_override_and_rechargeable(self):
        snap=base();add(snap,'sensor.first_battery','3');add(snap,'sensor.second_battery','4',ID2,name='Camera');snap['states'][1]['state']=ID1
        cfg=config();cfg['battery_device_overrides']=[{'device_id':ID2,'name':'Special','type':'recharge'}]
        out=battery_tasks.BatteryMaintenance(cfg,lambda:snap).read();self.assertEqual(len(out['items']),1);self.assertIn('Recharge Special',out['items'][0]['title'])
        cfg['battery_exclude_tokens']=['second'];self.assertEqual(battery_tasks.BatteryMaintenance(cfg,lambda:snap).read()['items'],[])
    def test_no_device_uses_entity_name_and_rechargeable_without_area(self):
        snap=base();snap['states'].append(state('sensor.lonely_battery','10',{'device_class':'battery'}));snap['entities'].append({'entity_id':'sensor.lonely_battery','device_id':None,'area_id':'area'})
        result=battery_tasks.BatteryMaintenance(config(),lambda:snap).read();self.assertIn('sensor.lonely_battery',result['items'][0]['title'])
        snap=base();add(snap,'sensor.camera_battery','10',ID2,model='RECHARGE',name='Camera');snap['devices'][0]['area_id']=None
        cfg=config();cfg['battery_models'].append({'model':'RECHARGE','type':'recharge'})
        result=battery_tasks.BatteryMaintenance(cfg,lambda:snap).read();self.assertIn('Recharge Camera',result['items'][0]['title'])

    def test_maintenance_order_and_winter_storage(self):
        snap=base();add(snap,'sensor.first_battery','10');snap['states'] += [state('sensor.brush','10'),state('sensor.blade','0'),state('input_boolean.storage','on')]
        cfg=config();cfg['maintenance_groups']=[{'name':'Example cleaner','kind':'vacuum','parts':[{'entity':'sensor.brush','label':'brush','threshold':10}]},{'name':'Example mower','kind':'mower','stored_entity':'input_boolean.storage','parts':[{'entity':'sensor.blade','label':'blade','threshold':10}]}]
        out=battery_tasks.BatteryMaintenance(cfg,lambda:snap).read();self.assertEqual(len(out['items']),2);self.assertIn('replace brush',out['items'][0]['title']);self.assertIn('10%',out['items'][1]['title'])
        snap['states'][-1]['state']='unknown';out=battery_tasks.BatteryMaintenance(cfg,lambda:snap).read();self.assertFalse(out['complete'])
    def test_incomplete_sources_do_not_expose_ignored_devices(self):
        snap=base();add(snap,'sensor.first_battery','2');snap['states'][1]['state']='unavailable'
        out=battery_tasks.BatteryMaintenance(config(),lambda:snap).read();self.assertFalse(out['complete']);self.assertEqual(out['items'],[])
        snap['states'][1]['state']='';snap['states'][0]['attributes']={};out=battery_tasks.BatteryMaintenance(config(),lambda:snap).read();self.assertFalse(out['complete']);self.assertTrue(out['items'])
        self.assertFalse(battery_tasks.BatteryMaintenance(config(),lambda:None).read()['complete'])
    def test_truncation_matches_utf16_display_limit(self):
        title='🔋 '+('x'*60)
        shortened=battery_tasks.trim_title(title)
        self.assertTrue(shortened.endswith('…'))
        self.assertLessEqual(len(shortened.encode('utf-16-le'))//2,62)

    def test_invalid_configuration_and_no_configuration(self):
        self.assertFalse(battery_tasks.BatteryMaintenance({},lambda:None).read()['configured'])
        cfg=config();cfg['battery_forecast_entity']='../bad';self.assertFalse(battery_tasks.BatteryMaintenance(cfg,lambda:None).read()['complete'])
        cfg=config();cfg['battery_device_overrides']=[{'device_id':'bad','type':'recharge'}];self.assertFalse(battery_tasks.BatteryMaintenance(cfg,lambda:None).read()['complete'])
    def test_registry_snapshot_cache_and_failure(self):
        class Client:
            calls=0
            def get(self,path,timeout):self.calls+=1;return []
            def ws_many(self,queries):self.calls+=1;return [[],[],[]]
        t=[10];client=Client();snapshot=registry_snapshot.Snapshot('token',client=client,clock=lambda:t[0]);self.assertIsNotNone(snapshot.read());self.assertIsNotNone(snapshot.read());self.assertEqual(client.calls,2)
        t[0]=71;self.assertIsNotNone(snapshot.read());self.assertEqual(client.calls,4)
        class Broken:
            def get(self,*args,**kwargs):raise OSError()
        self.assertIsNone(registry_snapshot.Snapshot('token',client=Broken()).read())

if __name__=='__main__':unittest.main()
