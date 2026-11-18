import importlib.util
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('legacy_source_import',ROOT/'tools'/'import_legacy_sources.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class Tests(unittest.TestCase):
    def test_extracts_and_validates_a_private_mapping(self):
        def item(name,body,entity=None):
            result={'element':name,'state_action':{'service':'floorplan.text_set','service_data':body}}
            if entity:result['entity']=entity
            return result
        rules=[item('k_alarm_v','', 'alarm_control_panel.example'),item('k_sun','', 'sun.example'),
               item('k_doors_v',"var DS=[['Entry','lock.entry','binary_sensor.door']];var bad=[];"),
               item('k_elec',"calendar.example event.example var E='example';"),
               item('k_cost','sensor.electric_current_accumulative_cost sensor.gas_current_accumulative_cost'),
               item('k_np_title','', 'media_player.example'),item('k_ps_t1','', 'media_player.game'),item('k_fest',"input_boolean.example sensor.example [D(1,2),'Happy Birthday Example!']"),
               item('k_ck_title','', 'sensor.cooking'),item('k_rt0','', 'climate.example')]
        rules.extend(item('k_lt'+str(i),'var R=[["Room",["light.example"]]]; var on=[];') for i in range(4))
        dashboard={'views':[{'cards':[{'config':{'rules':rules}}]}]}
        script="HE = ['binary_sensor.heat','climate.example']\nIE = ['input_boolean.alert','input_text.log','switch.example']\nW_ = 'weather.example'\nvar BF=hass.states['sensor.primary'];\nvar f=hass.states['sensor.primary']||hass.states['sensor.fallback'];"
        template="var PEOPLE = [['Example','switch.example']];\nvar ROOMS = ['Room'];"
        result=module.extract(dashboard,script,template)
        self.assertEqual(result['render_alarm'],'alarm_control_panel.example');self.assertEqual(result['render_rooms'][0]['entity'],'climate.example');self.assertEqual(result['render_energy']['current'],'sensor.example_current_rate');self.assertEqual(result['render_birthdays_fixed'],[{'month':1,'day':2,'name':'Example'}])
        with self.assertRaises((ValueError,StopIteration)):module.extract({'views':[]},script,template)

if __name__=='__main__':unittest.main()
