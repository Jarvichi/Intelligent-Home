import http.client
import io
import tempfile
import threading
from http.server import HTTPServer
import json
import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import cooking_client,cooking_render,device_state,frames,kindle_render,main,popup_render,source_settings

class HA:
    def __init__(self,value):self.value=value
    def optional_state(self,entity):return self.value

class Response(io.BytesIO):status=200
class Opener:
    def __init__(self,value):self.value=value;self.calls=[]
    def open(self,request,timeout):self.calls.append(request);return Response(json.dumps(self.value).encode())

class Tests(unittest.TestCase):
    def test_cooking_screen_displays_steps_and_safety_alert(self):
        cooking={'running':True,'revision':'a'*64,'title':'Example <meal>','eat':'19:00','oven_target':180,'next':[{'id':'x','at':'18:30','text':'Start'}], 'prep':[{'id':'y','text':'Prepare'}], 'steps':[{'at':'18:30','text':'Start','done':False}]}
        image=kindle_render.svg({'cooking':cooking},{'configured':True,'complete':True,'alerts':[{'kind':'smoke','title':'SMOKE ALARM'}]},now=0)
        ET.fromstring(image)
        for value in (b'Example &lt;meal&gt;',b'SMOKE ALARM',b'19:00',b'Prepare',b'STOP COOKING'):self.assertIn(value,image)
    def test_cooking_does_not_hide_incomplete_safety_alert_sources(self):
        cooking={'running':True,'title':'Example','next':[],'prep':[],'steps':[]}
        image=kindle_render.svg({'cooking':cooking},{'configured':True,'complete':False,'alerts':[{'kind':'smoke','title':'SMOKE ALARM'}]})
        self.assertIn(b'SMOKE ALARM',image);self.assertIn(b'Alert sources unavailable',image)

    def test_popup_screens_are_bounded_and_escape_private_values(self):
        cases=[('dinner',{'items':[{'title':'Meal <one>','eat':'19:00','lead':30,'tonight':True}],'page':0,'pages':1},b'Meal &lt;one&gt;'),('home',{'members':[{'name':'Example <one>','home':True}]},b'Example &lt;one&gt;'),('birthdays',{'items':[{'name':'Example <one>','date':'Tomorrow','countdown':'TOMORROW','today':True}]},b'Example &lt;one&gt;')]
        for screen,data,expected in cases:
            image=popup_render.svg(screen,data);ET.fromstring(image);self.assertIn(expected,image)
    def test_dinner_preserves_meal_and_selection_details(self):
        menu={'items':[{'title':'Example meal','eat':'18:25','lead':45,'tonight':True}],'page':0,'pages':2,'selected_eat':'18:30'}
        image=popup_render.svg('dinner',menu)
        root=ET.fromstring(image);ns={'s':'http://www.w3.org/2000/svg'}
        chips=[r for r in root.findall('s:rect',ns) if r.get('y')=='112']
        self.assertEqual([r.get('fill') for r in chips],['white','white','white','black'])
        self.assertIn('TONIGHT’S PLAN · ready from 18:25 · 45 min'.encode(),image)
        self.assertIn(b'1/2',image)
        back=next(r for r in root.findall('s:rect',ns) if r.get('x')=='312' and r.get('y')=='1240')
        self.assertEqual(back.get('fill'),'white')
    def test_home_and_birthdays_use_original_visual_treatment(self):
        home=popup_render.svg('home',{'members':[{'name':'Example','home':False}]})
        self.assertIn(b'url(#home-stripes)',home);self.assertIn(b'fill="#666"',home)
        birthday=popup_render.svg('birthdays',{'items':[{'name':'Example','today':True}]})
        root=ET.fromstring(birthday);ns={'s':'http://www.w3.org/2000/svg'}
        boxes=root.findall('s:rect',ns)
        self.assertEqual(next(r for r in boxes if r.get('y')=='140').get('fill'),'black')
        self.assertEqual(next(r for r in boxes if r.get('y')=='1240').get('fill'),'white')
    def test_birthday_source_keeps_age_and_full_name(self):
        value={'attributes':{'items':[{'n':'Example','full':'Example Person','fam':False,'d':'Mon 1 Jan','k':0,'age':50}]}}
        result=device_state.birthdays(HA(value),'sensor.example')
        self.assertEqual(result['items'][0]['name'],'Example Person');self.assertEqual(result['items'][0]['countdown'],'TODAY');self.assertIn('turns 50',result['items'][0]['date'])
    def test_new_source_mapping_rejects_wrong_types(self):
        self.assertTrue(source_settings.validate({'kindle_birthdays_entity':'sensor.example','kindle_presence_times_entity':'sensor.presence','render_presence':[{'name':'Example','entity':'switch.example','presence_key':'example'}]}))
        self.assertFalse(source_settings.validate({'kindle_birthdays_entity':'input_text.example'}))
        self.assertFalse(source_settings.validate({'render_presence':[{'name':'Example','entity':'switch.example','presence_key':'bad key'}]}))
    def test_admin_can_preview_all_screens_without_exposing_them_publicly(self):
        data={'panels':[{'label':'Home','members':[{'name':'Example','home':True}]}],'birthdays':{'items':[{'name':'Example','date':'Monday','countdown':'2 days'}]}}
        menu=lambda page:{'items':[{'title':'Example recipe','eat':'19:00','lead':30}],'page':page,'pages':1}
        captured=[]
        def encode(svg):captured.append(svg);return b'PNG fixture'
        server=HTTPServer(('127.0.0.1',0),main.make_handler(frames.Frames(''),True,'127.0.0.1',snapshot=lambda:data,png_encoder=encode,menu=menu));threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        for screen in ('main','dinner','home','birthdays'):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(c.close);c.request('GET','/api/render.png?screen='+screen);r=c.getresponse();self.assertEqual(r.status,200);self.assertEqual(r.read(),b'PNG fixture')
        self.assertIn(b'Example recipe',captured[1]);self.assertIn(b'Who&#x27;s home',captured[2]);self.assertIn(b'Birthdays',captured[3])
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(c.close);c.request('GET','/api/render.png?screen=other');r=c.getresponse();self.assertEqual(r.status,400);r.read()

    def test_cooking_client_is_read_only_and_uses_bearer(self):
        opener=Opener({'running':True,'revision':'a'*64,'next':[],'prep':[],'steps':[]});client=cooking_client.CookingClient('http://example.test','fixture',opener)
        self.assertTrue(client.read()['running']);self.assertEqual(opener.calls[0].get_method(),'GET');self.assertEqual(opener.calls[0].get_header('Authorization'),'Bearer fixture')
        opener.value={'revision':'a'*64,'page':0,'pages':1,'items':[]};self.assertEqual(client.menu(0)['pages'],1);self.assertIsNone(client.menu(-1))

if __name__=='__main__':unittest.main()
