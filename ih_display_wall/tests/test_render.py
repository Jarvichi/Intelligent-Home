import io
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
    def test_snapshot_is_read_only_and_handles_unavailable(self):
        opener=Opener();ha=render.HA('example-token',opener)
        result=ha.snapshot(['sensor.temperature','sensor.offline'],'todo.example')
        self.assertEqual(result['items'][0]['value'],'21 °C');self.assertEqual(result['items'][1]['value'],'Unavailable');self.assertEqual(result['tasks'],['Example task'])
        self.assertTrue(all(method=='GET' or '/services/todo/get_items?' in url for url,method in opener.requests))
        self.assertEqual(len(opener.requests),3)
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
    def test_missing_or_wrong_profile_fails_closed(self):
        for profile in ({'state':'unavailable'},{'state':'ready','width':True,'height':100}):
            with self.assertRaises(ValueError):render.svg(profile,{})
    def test_empty_and_unavailable_tasks_are_distinct(self):
        p={'state':'ready','width':100,'height':100}
        self.assertIn(b'No open tasks',render.svg(p,{'tasks':[]}))
        self.assertIn(b'Task list unavailable',render.svg(p,{'tasks':None,'tasks_configured':True}))
