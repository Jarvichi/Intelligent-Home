import http.client
import json
import pathlib
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from http.server import HTTPServer

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import frames,kindle_render,main,source_settings

PNG=b'\x89PNG\r\n\x1a\nexample'
class Tests(unittest.TestCase):
    def test_renderer_keeps_sections_and_escapes_data(self):
        weather={'current':{'temperature':10,'condition':'cloudy'},'facts':{'wind':'5 km/h','next_rain':'Later','sky':'Cloud 10%'},'daily':[{'label':'Today','low':4,'high':12},{'label':'Tomorrow','low':3,'high':11}],'hours':[{'time':'10:00','temperature':10,'condition':'cloudy'}]}
        data={'weather':weather,'tasks':[{'title':'Task <one>','urgent':True}],'tasks_configured':True,'panels':[{'available':True,'active':True,'label':'Alarm','detail':'Off'}]}
        image=kindle_render.svg(data,{'complete':True,'alerts':[]},now=0);root=ET.fromstring(image)
        self.assertEqual((root.get('width'),root.get('height')),('1072','1448'))
        for item in (b'Weather',b'HOUSE',b'LIGHTS ON',b'TO DO',b'Task &lt;one&gt;'):
            if item==b'Weather':continue
            self.assertIn(item,image)
        self.assertIn(b'10:00',image);self.assertIn(b'Off',image)
    def test_unavailable_state_is_visible(self):
        image=kindle_render.svg({'tasks':None,'tasks_configured':True,'tasks_partial':True},None,now=0)
        self.assertIn(b'Weather not configured',image);self.assertIn(b'Task sources unavailable',image);self.assertIn(b'Alert sources unavailable',image)
    def test_import_and_png_require_admin(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup);sources=source_settings.SourceSettings(pathlib.Path(directory.name)/'sources.json')
        def server(peer):
            s=HTTPServer(('127.0.0.1',0),main.make_handler(frames.Frames(''),True,peer,sources,lambda:{},lambda:{'complete':True,'alerts':[]},lambda _:PNG));threading.Thread(target=s.serve_forever,daemon=True).start();self.addCleanup(s.server_close);self.addCleanup(s.shutdown);return s.server_port
        def request(port,method,path,body=None):
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=3);self.addCleanup(c.close);c.request(method,path,body=body,headers={'Content-Type':'application/json'} if body else {});r=c.getresponse();return r.status,r.read()
        admin=server('127.0.0.1');other=server('192.0.2.1');body=json.dumps({'render_alarm':'alarm_control_panel.example'})
        self.assertEqual(request(other,'POST','/api/sources/import',body)[0],403);self.assertEqual(request(admin,'POST','/api/sources/import',body)[0],200)
        self.assertEqual(sources.read()['render_alarm'],'alarm_control_panel.example');self.assertEqual(request(admin,'GET','/api/render.png'),(200,PNG));self.assertEqual(request(other,'GET','/api/render.png')[0],403)

if __name__=='__main__':unittest.main()
