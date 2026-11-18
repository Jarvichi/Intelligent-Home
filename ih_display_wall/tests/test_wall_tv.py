import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import wall_tv

PROFILE={'state':'ready','width':1920,'height':1080}
CLEAR={'configured':True,'complete':True,'alerts':[]}

class Tests(unittest.TestCase):
    def test_coloured_task_icon_and_birthday_countdown(self):
        data={'tasks':[{'title':'🔋 Charge <battery>'}], 'panels':[{'active':True,'available':True,'slot':'occasion','detail':'Example birthday in 3 days'}]}
        picture=wall_tv.svg(PROFILE,data,CLEAR,now=0)
        ET.fromstring(picture)
        self.assertIn(b'#49b92d',picture);self.assertIn(b'Charge &lt;battery&gt;',picture)
        self.assertIn(b'3 days:',picture);self.assertIn(b'Example birthday',picture);self.assertIn(b'#ed343f',picture)
    def test_picture_has_separate_weather_and_task_panels(self):
        weather={'source':'primary','current':{'current':True,'temperature':0,'condition':'partlycloudy'},
                 'daily':[{'label':'Today','low':0,'high':4,'condition':'cloudy'},{'label':'Tomorrow','low':-1,'high':3,'condition':'rainy','precipitation':1.3}],
                 'facts':{'wind':'12 km/h','next_rain':'15:00','sky':'Cloud 60%'},
                 'hours':[{'time':'12:00','temperature':0,'condition':'cloudy'}]}
        data={'weather_configured':True,'weather':weather,'tasks':[{'title':'Task <one>','detail':'Example','urgent':True}],'tasks_configured':True}
        picture=wall_tv.svg(PROFILE,data,CLEAR,now=12*3600);root=ET.fromstring(picture)
        self.assertEqual(root.attrib['width'],'1920');self.assertIn(b'Today 0',picture);self.assertIn(b'1.3 mm rain',picture);self.assertIn(b'Task &lt;one&gt;',picture);self.assertIn(b'#4a0c10',picture)
    def test_alerts_are_prioritised_and_incomplete_is_visible(self):
        data={'tasks':[],'panels':[{'available':True,'active':True,'kind':'cooking','label':'Dinner','detail':'Example'}]}
        alert={'configured':True,'complete':False,'alerts':[{'title':'SMOKE ALARM'}]}
        picture=wall_tv.svg(PROFILE,data,alert,now=0)
        self.assertIn(b'SMOKE ALARM',picture);self.assertIn(b'Alert sources incomplete',picture);self.assertNotIn(b'Dinner: Example',picture)
        cooking=wall_tv.svg(PROFILE,data,CLEAR,now=0);self.assertIn(b'Dinner: Example',cooking);self.assertNotIn(b'#781b21',cooking)
    def test_tasks_page_and_nonlocal_images_rejected(self):
        data={'tasks':[{'title':'Item '+str(i)} for i in range(6)],'background':'https://example.test/picture.png'}
        first=wall_tv.svg(PROFILE,data,CLEAR,now=0);second=wall_tv.svg(PROFILE,data,CLEAR,now=60)
        self.assertIn(b'Item 0',first);self.assertNotIn(b'Item 5',first);self.assertIn(b'Item 5',second);self.assertNotIn(b'https://example.test',first)
        for bad in ({'state':'ready','width':5000,'height':100},{'state':'unavailable'}):
            with self.assertRaises(ValueError):wall_tv.svg(bad,{})

if __name__=='__main__':unittest.main()
