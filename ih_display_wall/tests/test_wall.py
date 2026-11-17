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
    def __init__(self):self.value=PNG;self.calls=0
    def open(self,request,timeout):
        self.calls+=1
        if isinstance(self.value,Exception):raise self.value
        return Response(self.value)
class TestFrames(unittest.TestCase):
    def test_frame_retained_and_marked_stale(self):
        opener=Opener();store=frames.Frames('http://display.example/image',opener,clock=lambda:123)
        self.assertEqual(store.refresh()['state'],'ready');self.assertEqual(store.frame(),(PNG,'image/png'))
        opener.value=OSError();status=store.refresh();self.assertTrue(status['stale']);self.assertEqual(status['last_success'],123);self.assertEqual(store.frame(),(PNG,'image/png'))
        opener.value=b'not an image';self.assertTrue(store.refresh()['stale']);self.assertEqual(store.frame()[0],PNG)
        opener.value=PNG+b'x'*frames.LIMIT;self.assertTrue(store.refresh()['stale']);self.assertEqual(store.frame()[0],PNG)
    def test_url_credentials_and_invalid_schemes_are_rejected(self):
        for url in ('','file:///test','http://user:pass'+'@'+'example.test/image','https://example.test/image#fragment','http://host:bad/'):
            opener=Opener();store=frames.Frames(url,opener);self.assertEqual(store.refresh()['state'],'not_configured');self.assertEqual(opener.calls,0)
    def test_no_redirect(self):
        self.assertIsNone(frames.NoRedirect().redirect_request(None,None,302,'Found',{},'http://example.test/'))
    def test_private_ingress_and_health(self):
        store=frames.Frames('http://example.test/image',Opener())
        def server(ingress,peer):
            s=HTTPServer(('127.0.0.1',0),main.make_handler(store,ingress,peer));threading.Thread(target=s.serve_forever,daemon=True).start();self.addCleanup(s.server_close);self.addCleanup(s.shutdown);return s.server_port
        def req(port,method,path):
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3);self.addCleanup(conn.close);conn.request(method,path);r=conn.getresponse();return r.status,r.read()
        denied=server(True,'192.0.2.1');allowed=server(True,'127.0.0.1');health=server(False,'127.0.0.1')
        self.assertEqual(req(denied,'GET','/api/status')[0],403)
        self.assertEqual(req(health,'GET','/api/frame')[0],404)
        self.assertEqual(req(health,'GET','/healthz')[0],200)
        self.assertEqual(req(allowed,'GET','/api/frame')[0],404)
        self.assertEqual(req(allowed,'POST','/api/refresh')[0],200)
        self.assertEqual(req(allowed,'GET','/api/frame'),(200,PNG))

if __name__=='__main__':unittest.main()
