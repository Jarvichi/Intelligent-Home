import io
import datetime
import json
import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import render

class Response(io.BytesIO):status=200
class Opener:
    def __init__(self):self.requests=[]
    def open(self,req,timeout):
        self.requests.append((req.full_url,req.get_method()))
        if '/states/' in req.full_url:
            name=req.full_url.rsplit('/',1)[-1]
            if name=='sensor.offline':raise OSError()
            return Response(json.dumps({'state':'21','attributes':{'friendly_name':'Example temperature','unit_of_measurement':'°C'}}).encode())
        return Response(json.dumps({'service_response':{'todo.example':{'items':[{'summary':'Example task'}]}}}).encode())
class Tests(unittest.TestCase):
    def test_forecast_primary_fallback_stale_and_escaping(self):
        now=datetime.datetime(2026,11,17,12,tzinfo=datetime.timezone.utc).timestamp()
        def row(offset,condition='cloudy'):
            return {'datetime':datetime.datetime.fromtimestamp(now+offset,datetime.timezone.utc).isoformat(),'temperature':9.4,'condition':condition}
        class Forecast(render.HA):
            def __init__(self,values):self.values=values;self.calls=[]
            def request(self,path,body=None):self.calls.append(path);return self.values[path.rsplit('/',1)[-1]]
        valid={'state':'ok','last_updated':row(0)['datetime'],'attributes':{'hourly':[row(0,'cloudy <test>'),row(3600)]}}
        old={'state':'ok','last_updated':row(0)['datetime'],'attributes':{'hourly':[row(-7200)]}}
        ha=Forecast({'sensor.primary':valid,'sensor.fallback':old})
        data=ha.forecast('sensor.primary','sensor.fallback',now);self.assertEqual(data['source'],'primary');self.assertEqual(len(ha.calls),1)
        self.assertTrue(data['current']['current']);self.assertEqual(data['daily'],[])
        picture=render.svg({'state':'ready','width':1920,'height':1080},{'weather_configured':True,'weather':data})
        self.assertIn(b'cloudy &lt;test&gt;',picture)
        ha=Forecast({'sensor.primary':old,'sensor.fallback':valid});data=ha.forecast('sensor.primary','sensor.fallback',now);self.assertEqual(data['source'],'fallback')
        self.assertIn(b'fallback',render.svg({'state':'ready','width':1920,'height':1080},{'weather_configured':True,'weather':data}))
        ha=Forecast({'sensor.primary':old,'sensor.fallback':old});self.assertIsNone(ha.forecast('sensor.primary','sensor.fallback',now))
        stale=dict(valid,last_updated=row(-10801)['datetime']);ha=Forecast({'sensor.primary':stale,'sensor.fallback':valid});self.assertEqual(ha.forecast('sensor.primary','sensor.fallback',now)['source'],'fallback')
        self.assertIn(b'Hourly forecast unavailable',render.svg({'state':'ready','width':1920,'height':1080},{'weather_configured':True,'weather':None}))
    def test_daily_forecast_and_future_not_labelled_now(self):
        now=datetime.datetime(2026,11,17,12,tzinfo=datetime.timezone.utc).timestamp()
        def date(offset):return datetime.datetime.fromtimestamp(now+offset,datetime.timezone.utc).isoformat()
        value={'state':'ok','last_updated':date(0),'attributes':{'hourly':[{'datetime':date(1800),'temperature':8,'condition':'rainy'}],'daily':[{'datetime':date(86400),'temperature':12,'templow':3,'condition':'cloudy'},{'datetime':date(0),'temperature':10,'templow':2,'condition':'rainy'}]}}
        class Forecast(render.HA):
            def __init__(self):pass
            def request(self,path,body=None):return value
        weather=Forecast().forecast('sensor.primary','',now);self.assertFalse(weather['current']['current']);self.assertEqual([d['label'] for d in weather['daily']],['Today','Tomorrow'])
        picture=render.svg({'state':'ready','width':1920,'height':1080},{'weather_configured':True,'weather':weather})
        self.assertIn(b'Next 8',picture);self.assertNotIn(b'Now 8',picture);self.assertIn(b'Tomorrow 12',picture)
        value['attributes']['daily']=[{'datetime':date(0),'temperature':float('nan'),'templow':0,'condition':'rainy'}]
        weather=Forecast().forecast('sensor.primary','',now);self.assertEqual(weather['daily'],[])
        self.assertIn(b'Daily forecast unavailable',render.svg({'state':'ready','width':1920,'height':1080},{'weather_configured':True,'weather':weather}))
    def test_weather_details_and_rain_are_computed_from_forecast(self):
        now=datetime.datetime(2026,11,17,12,tzinfo=datetime.timezone.utc).timestamp()
        def date(offset):return datetime.datetime.fromtimestamp(now+offset,datetime.timezone.utc).isoformat()
        value={'state':'ok','last_updated':date(0),'attributes':{'hourly':[{'datetime':date(0),'temperature':8,'condition':'cloudy','wind_speed':15.5,'wind_bearing':180,'uv_index':0.2,'cloud_coverage':80},{'datetime':date(3600),'temperature':9,'condition':'rainy'}]}}
        class Forecast(render.HA):
            def __init__(self):pass
            def request(self,path,body=None):return value
        result=Forecast().forecast('sensor.primary','',now);facts=result['facts']
        self.assertEqual(facts['wind'],'16 km/h');self.assertEqual(facts['bearing'],180);self.assertEqual(facts['sky'],'Cloud 80%');self.assertNotEqual(facts['next_rain'],'None in hourly forecast')
        picture=render.svg({'state':'ready','width':1920,'height':1080},{'weather_configured':True,'weather':result});self.assertIn(b'Wind 16 km/h',picture);self.assertIn(b'Cloud 80%',picture)
        value['attributes']['hourly'][0]['uv_index']=3.2;value['attributes']['hourly'][0]['precipitation']=0.2
        facts=Forecast().forecast('sensor.primary','',now)['facts'];self.assertEqual(facts['next_rain'],'Due now');self.assertEqual(facts['sky'],'UV 3')
    def test_snapshot_is_read_only_and_handles_unavailable(self):
        opener=Opener();ha=render.HA('example-token',opener)
        result=ha.snapshot(['sensor.temperature','sensor.offline'],'todo.example')
        self.assertEqual(result['items'][0]['value'],'21 °C');self.assertEqual(result['items'][1]['value'],'Unavailable');self.assertEqual(result['tasks'][0]['title'],'Example task')
        self.assertTrue(all(method=='GET' or '/services/todo/get_items?' in url for url,method in opener.requests))
        self.assertEqual(len(opener.requests),3)
    def test_structured_feed_combines_and_reports_unavailable_sources(self):
        class Sources(render.HA):
            def __init__(self,feed,todo):self.feed_value=feed;self.todo_value=todo
            def task_feed(self,entity):return self.feed_value
            def todos(self,entity):return self.todo_value
        feed=[{'title':'Routine','detail':'detail','priority':'normal'},{'title':'Urgent','detail':'','priority':'high'}]
        data=Sources(feed,['Other']).snapshot([],'todo.example','sensor.feed')
        self.assertEqual([i['title'] for i in data['tasks']],['Urgent','Routine','Other']);self.assertFalse(data['tasks_partial'])
        data=Sources(None,['Other']).snapshot([],'todo.example','sensor.feed')
        self.assertTrue(data['tasks_partial']);self.assertEqual(data['tasks'][0]['title'],'Other')
        data=Sources([],None).snapshot([],'todo.example','sensor.feed')
        self.assertTrue(data['tasks_partial']);self.assertEqual(data['tasks'],[])
        self.assertIn(b'Some task sources are unavailable',render.svg({'state':'ready','width':1920,'height':1080},data))
        data=Sources(None,None).snapshot([],'todo.example','sensor.feed');self.assertIsNone(data['tasks'])
    def test_shared_messages_partial_is_visible(self):
        ha=render.HA('')
        message={'title':'Example message','detail':'Example detail','priority':'high'}
        result=ha.snapshot([],'','',lambda:{'configured':True,'complete':False,'items':[message]})
        self.assertTrue(result['tasks_partial']);self.assertEqual(result['tasks'][0]['title'],'Example message')
        picture=render.svg({'state':'ready','width':1920,'height':1080},result)
        self.assertIn(b'Example message',picture);self.assertIn(b'Some task sources are unavailable',picture)
        failed=ha.snapshot([],'','',lambda:None);self.assertIsNone(failed['tasks']);self.assertTrue(failed['tasks_partial'])

    def test_feed_validation_and_escaping(self):
        class Feed(render.HA):
            def __init__(self,items):self.items=items
            def request(self,path,body=None):return {'state':'ok','attributes':{'items':self.items}}
        row={'title':'Task <bad>','detail':'Detail & more','priority':'high'}
        feed=Feed([row]).task_feed('sensor.example');self.assertEqual(feed,[row])
        svg=render.svg({'state':'ready','width':1920,'height':1080},{'tasks':feed})
        self.assertIn(b'Task &lt;bad&gt;',svg);self.assertIn(b'Detail &amp; more',svg)
        for invalid in ([{'title':''}],[{'title':'X','priority':'urgent'}],[{'title':'X','detail':7}]):self.assertIsNone(Feed(invalid).task_feed('sensor.example'))

    def test_invalid_entity_does_not_make_request(self):
        opener=Opener();ha=render.HA('example',opener)
        self.assertEqual(ha.state('../invalid')['value'],'Unavailable');self.assertIsNone(ha.todos('sensor.other'));self.assertEqual(opener.requests,[])
    def test_picture_dimensions_and_escapes_data(self):
        profile={'state':'ready','width':1920,'height':1080}
        data={'items':[{'label':'Example <script>','value':'1 & 2'}],'tasks':['Task <x>'],'tasks_configured':True}
        blob=render.svg(profile,data);root=ET.fromstring(blob)
        self.assertEqual(root.attrib['width'],'1920');self.assertEqual(root.attrib['height'],'1080');self.assertNotIn(b'<script>',blob);self.assertIn(b'&lt;script&gt;',blob)
        self.assertIn(b'Task &lt;x&gt;',blob)
        self.assertNotIn(b'Unavailable',blob)
        for child in root:
            if child.tag.endswith('text'):self.assertLessEqual(float(child.attrib['y']),810)
            if child.tag.endswith('rect') and child.attrib.get('x'):
                self.assertLessEqual(float(child.attrib['x'])+float(child.attrib['width']),1536)
                self.assertLessEqual(float(child.attrib['y'])+float(child.attrib['height']),810)
    def test_urgent_task_is_highlighted(self):
        p={'state':'ready','width':1920,'height':1080}
        image=render.svg(p,{'tasks':[{'title':'Example urgent','detail':'','priority':'normal','urgent':True}]})
        self.assertIn(b'fill="#ffd88c"',image);self.assertIn(b'Example urgent',image)

    def test_alerts_never_imply_safe_when_unavailable(self):
        p={'state':'ready','width':1920,'height':1080}
        self.assertIn(b'Alert preview not connected',render.svg(p,{}))
        self.assertIn(b'Alert preview not configured',render.svg(p,{}, {'configured':False,'complete':False,'alerts':[]}))
        image=render.svg(p,{}, {'configured':True,'complete':False,'alerts':[]});self.assertIn(b'Alert sources unavailable',image);self.assertNotIn(b'No active alerts',image)
        alerts={'configured':True,'complete':False,'alerts':[{'kind':'smoke','title':'SMOKE <ALARM>','short':'SMOKE','dismissible':False}]}
        image=render.svg(p,{},alerts);self.assertIn(b'INPUTS INCOMPLETE',image);self.assertIn(b'SMOKE &lt;ALARM&gt;',image)
    def test_alert_paging_and_selected_source_label(self):
        p={'state':'ready','width':1920,'height':1080}
        alerts={'configured':True,'complete':True,'alerts':[{'title':'Warning '+str(i)} for i in range(5)]}
        first=render.svg(p,{},alerts,now=0);second=render.svg(p,{},alerts,now=300)
        self.assertIn(b'Warning 0',first);self.assertNotIn(b'Warning 4',first);self.assertIn(b'Warning 4',second)
        self.assertIn(b'No active alerts from selected sources',render.svg(p,{}, {'configured':True,'complete':True,'alerts':[]}))

    def test_missing_or_wrong_profile_fails_closed(self):
        for profile in ({'state':'unavailable'},{'state':'ready','width':True,'height':100}):
            with self.assertRaises(ValueError):render.svg(profile,{})
    def test_empty_and_unavailable_tasks_are_distinct(self):
        p={'state':'ready','width':100,'height':100}
        picture=render.svg(p,{'tasks':[]});self.assertIn(b'No items from selected sources',picture);self.assertIn(b'Only selected sources are shown',picture);self.assertNotIn(b'No open tasks',picture)
        self.assertIn(b'Task list unavailable',render.svg(p,{'tasks':None,'tasks_configured':True}))
