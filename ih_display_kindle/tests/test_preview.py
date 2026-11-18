import http.client
import io
import pathlib
import sys
import threading
import unittest
from http.server import HTTPServer

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import frames
import main

PNG=b'\x89PNG\r\n\x1a\nexample'
class Response(io.BytesIO):
    status=200
class Opener:
    def __init__(self):self.value=PNG
    def open(self,request,timeout):
        if isinstance(self.value,Exception):raise self.value
        return Response(self.value)

class Tests(unittest.TestCase):
    def start(self,store,ingress,peer='127.0.0.1'):
        s=HTTPServer(('127.0.0.1',0),main.make_handler(store,ingress,peer));threading.Thread(target=s.serve_forever,daemon=True).start();self.addCleanup(s.server_close);self.addCleanup(s.shutdown);return s.server_port
    def request(self,port,method,path):
        c=http.client.HTTPConnection('127.0.0.1',port,timeout=3);self.addCleanup(c.close);c.request(method,path);r=c.getresponse();return r.status,r.getheader('Content-Type'),r.read()
    def test_admin_preview_and_health_are_separate(self):
        store=frames.Frames('http://example.test/image',Opener())
        admin=self.start(store,True);denied=self.start(store,True,'192.0.2.1');health=self.start(store,False)
        self.assertEqual(self.request(denied,'GET','/')[0],403)
        self.assertEqual(self.request(health,'GET','/api/frame')[0],404)
        self.assertEqual(self.request(health,'GET','/healthz')[0],200)
        self.assertEqual(self.request(admin,'GET','/api/frame')[0],503)
        self.assertEqual(self.request(admin,'POST','/api/refresh')[0],200)
        self.assertEqual(self.request(admin,'GET','/api/frame?fresh=1'),(200,'image/png',PNG))
    def test_failed_refresh_keeps_and_marks_previous_picture(self):
        o=Opener();s=frames.Frames('http://example.test/image',o,clock=lambda:123)
        self.assertEqual(s.refresh()['state'],'ready');o.value=OSError();v=s.refresh()
        self.assertEqual((v['state'],v['stale'],v['last_success']),( 'unavailable',True,123));self.assertEqual(s.frame(),(PNG,'image/png'))
    def test_rejects_bad_urls_and_responses(self):
        for url in ('','file:///x','http://test@example.com/a'):
            self.assertFalse(frames.valid_url(url))
        o=Opener();o.value=b'bad';self.assertEqual(frames.Frames('http://example.test/',o).refresh()['state'],'unavailable')
        self.assertIsNone(frames.NoRedirect().redirect_request(None,None,302,'Found',{},'http://example.test/'))

if __name__=='__main__':unittest.main()
