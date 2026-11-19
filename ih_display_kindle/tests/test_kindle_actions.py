import datetime
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import kindle_actions
import source_settings

NOW=2000000000
class HA:
    def __init__(self):
        self.values={};self.calls=[]
    def set(self,entity,state,changed='2030-01-01T00:00:00+00:00',attrs=None):
        self.values[entity]={'state':state,'last_changed':changed,'attributes':attrs or {}}
    def optional_state(self,entity):return self.values.get(entity)
    def request(self,path,body):self.calls.append((path,body));return []

class Tests(unittest.TestCase):
    def setUp(self):
        self.ha=HA();self.cfg={'kindle_battery_entity':'sensor.battery','kindle_last_seen_entity':'sensor.seen','kindle_message_entities':['input_text.message'],'kindle_dishwasher_entity':'input_boolean.dishwasher','kindle_wheel_entity':'input_text.wheel','kindle_warning_dismissed_entity':'input_text.dismissed','kindle_page_entity':'input_text.page','kindle_lights_off_entities':['light.one','light.two']}
        self.ha.set('sensor.battery','80',attrs={'charging':True});self.ha.set('sensor.seen',datetime.datetime.fromtimestamp(NOW,datetime.timezone.utc).isoformat())
        self.ha.set('input_text.message','Example | Details');self.ha.set('input_boolean.dishwasher','on');self.ha.set('input_text.wheel','spun 1');self.ha.set('input_text.dismissed','');self.ha.set('input_text.page','0');self.ha.set('light.one','on');self.ha.set('light.two','off')
        self.warning={'kind':'free','title':'Example','short':'Example','dismissible':True}
        self.alerts={'configured':True,'complete':True,'alerts':[self.warning]};self.actions=kindle_actions.KindleActions(self.ha,self.cfg,alerts=lambda:self.alerts,clock=lambda:NOW)
    def test_messages_only_match_unique_and_reject_changed_value(self):
        descriptor=self.actions.message_for('Example','Details');self.assertEqual(descriptor['kind'],'message')
        self.ha.set('input_text.message','Replacement | Details','2030-01-02T00:00:00+00:00')
        self.assertFalse(self.actions.dispatch(descriptor)['ok']);self.assertEqual(self.ha.calls,[])
        self.ha.set('input_text.message','Example | Details');self.cfg['kindle_message_entities'].append('input_text.other');self.ha.set('input_text.other','Example | Details')
        self.assertIsNone(self.actions.message_for('Example','Details'))
    def test_mains_rules_rechecked_and_dishwasher_works_on_battery(self):
        message=self.actions.describe('message',index=0);dishwasher=self.actions.describe('dishwasher')
        self.ha.set('sensor.battery','80',attrs={'charging':False})
        self.assertEqual(self.actions.dispatch(message)['why'],'not charging')
        self.assertTrue(self.actions.dispatch(dishwasher)['ok']);self.assertEqual(self.ha.calls[0][0],'services/input_boolean/turn_off')
    def test_mains_lights_only_allow_captured_configured_entities(self):
        descriptor=self.actions.describe('lights');self.assertTrue(self.actions.dispatch(descriptor)['ok'])
        self.assertEqual(self.ha.calls[0][1]['entity_id'],['light.one'])
        self.ha.set('light.two','on','2030-01-02T00:00:00+00:00');self.assertFalse(self.actions.dispatch(descriptor)['ok'])
        self.cfg['kindle_lights_off_entities']=['light.one',{}];self.assertIsNone(self.actions.describe('lights'))
    def test_dynamic_floor_selection_rechecks_membership_and_states(self):
        self.cfg['kindle_lights_off_floors']=['ground_floor','upper_floor'];self.cfg['kindle_lights_off_exclude']=['seasonal','clock']
        self.assertTrue(source_settings.validate({'kindle_lights_off_floors':['ground_floor'],'kindle_lights_off_exclude':['seasonal']}))
        self.assertFalse(source_settings.validate({'kindle_lights_off_floors':['bad\"floor']}))
        original=self.ha.request;calls=[]
        def request(path,body):
            if path=='template':
                calls.append(body['template'])
                return [e for e in ('light.one','light.two') if self.ha.optional_state(e)['state']=='on']
            return original(path,body)
        self.ha.request=request
        first=self.actions.describe('lights');self.assertEqual(set(first['states']),{'light.one'})
        self.assertIn('floor_entities',calls[0]);self.assertIn('seasonal|clock',calls[0])
        self.ha.set('light.two','on','2030-01-02T00:00:00+00:00')
        self.assertFalse(self.actions.dispatch(first)['ok']);self.assertEqual(self.ha.calls,[])
        second=self.actions.describe('lights');self.assertTrue(self.actions.dispatch(second)['ok'])
        self.assertEqual(self.ha.calls[0][1]['entity_id'],['light.one','light.two'])
    def test_dynamic_floor_selection_rejects_malformed_or_unavailable_sources(self):
        self.cfg['kindle_lights_off_floors']=['ground_floor'];self.cfg['kindle_lights_off_exclude']=[]
        self.ha.request=lambda path,body:[{}]
        self.assertIsNone(self.actions.describe('lights'))
        self.cfg['kindle_lights_off_floors']=[{}]
        self.assertIsNone(self.actions.describe('lights'))
    def test_only_matching_energy_alert_can_be_dismissed(self):
        self.assertIsNone(self.actions.describe('alert',alert={'kind':'smoke','dismissible':True}))
        descriptor=self.actions.describe('alert',alert=self.warning)
        self.alerts['alerts']=[{'kind':'co','title':'CO','short':'CO','dismissible':False}]
        self.assertFalse(self.actions.dispatch(descriptor)['ok']);self.assertEqual(self.ha.calls,[])
        self.alerts['alerts']=[self.warning];self.assertTrue(self.actions.dispatch(descriptor)['ok'])
    def test_page_rechecks_current_value_and_bounds_direction(self):
        self.assertIsNone(self.actions.describe('page',delta=20));descriptor=self.actions.describe('page',delta=-1)
        self.assertTrue(self.actions.dispatch(descriptor)['ok']);self.assertEqual(self.ha.calls[-1][1]['value'],'2519')
        self.ha.set('input_text.page','1','2030-01-02T00:00:00+00:00');self.assertFalse(self.actions.dispatch(descriptor)['ok'])
    def test_unknown_target_and_bad_descriptor_are_rejected(self):
        self.assertIsNone(self.actions.describe('message',index=2));self.assertFalse(self.actions.dispatch({'kind':'message','index':0,'expected':{'state':[]}})['ok'])
        self.assertIsNone(self.actions.message_for('Example','Details',source='input_text.other'))
        self.assertIsNone(self.actions.describe('dishwasher',source='input_boolean.other'))
        old=self.actions.describe('dishwasher');self.cfg['kindle_dishwasher_entity']='input_boolean.other';self.ha.set('input_boolean.other','on')
        self.assertFalse(self.actions.dispatch(old)['ok'])
        self.cfg['kindle_dishwasher_entity']='script.bad';self.assertIsNone(self.actions.describe('dishwasher'))

if __name__=='__main__':unittest.main()
