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
    def test_admin_png_preview_uses_same_renderer(self):
        class Profile:
            def read(self):return {'state':'ready','width':1920,'height':1080}
        seen=[]
        def encoder(svg):seen.append(svg);return PNG
        server=HTTPServer(('127.0.0.1',0),main.make_handler(frames.Frames(''),True,'127.0.0.1',Profile(),lambda:{},png_encoder=encoder))
        threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(conn.close);conn.request('GET','/api/render.png');r=conn.getresponse()
        self.assertEqual((r.status,r.getheader('Content-Type'),r.read()),(200,'image/png',PNG));self.assertIn(b'<svg',seen[0])
    def test_optional_url_capability_and_cache_busting(self):
        class Profile:
            def read(self):return {'state':'ready','width':1920,'height':1080}
        token='a'*32
        server=HTTPServer(('127.0.0.1',0),main.make_handler(frames.Frames(''),profile=Profile(),render_data=lambda:{},image_token=token,png_encoder=lambda _:PNG,allow_url_token=True))
        threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(path):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(c.close);c.request('GET',path);r=c.getresponse();return r.status,r.read()
        self.assertEqual(request('/v1/render/'+token+'.png?refresh=1'),(200,PNG))
        self.assertEqual(request('/v1/render/'+'b'*32+'.png')[0],401)
        self.assertEqual(request('/v1/render.png?token='+token)[0],401)
    def test_source_diagnostics_is_private_and_reports_partial(self):
        class Profile:
            def alerts(self):return {'configured':True,'complete':False,'alerts':[]}
        data=lambda:{'weather_configured':True,'weather':None,'tasks_configured':True,'tasks':[],'tasks_partial':True,'panels':[{'available':True},{'available':False}]}
        server=HTTPServer(('127.0.0.1',0),main.make_handler(frames.Frames(''),True,'127.0.0.1',Profile(),data,True))
        threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(conn.close);conn.request('GET','/api/diagnostics');response=conn.getresponse();result=__import__('json').loads(response.read())
        self.assertEqual(response.status,200);self.assertEqual(result['weather'],'unavailable');self.assertEqual(result['tasks'],'partial');self.assertEqual(result['alerts'],'partial');self.assertEqual(result['unavailable_panels'],1)
    def test_rendered_image_requires_auth_and_a_valid_profile(self):
        store=frames.Frames('',Opener())
        class Profile:
            def read(self):return {'state':'ready','width':1920,'height':1080}
        calls=[]
        def encode(data):calls.append(data);return PNG
        server=HTTPServer(('127.0.0.1',0),main.make_handler(store,profile=Profile(),render_data=lambda:{},image_token='test-token',png_encoder=encode))
        threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(auth=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(conn.close)
            conn.request('GET','/v1/render.png',headers={'Authorization':auth} if auth else {});response=conn.getresponse();return response.status,response.getheader('Content-Type'),response.read()
        self.assertEqual(request()[0],401);self.assertEqual(calls,[])
        status,kind,data=request('Bearer test-token');self.assertEqual((status,kind,data),(200,'image/png',PNG));self.assertIn(b'<svg',calls[0])
    def test_private_image_feed_auth_and_stale_frame(self):
        opener=Opener();store=frames.Frames('http://display.example/image',opener)
        server=HTTPServer(('127.0.0.1',0),main.make_handler(store,image_token='test-token'))
        threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(auth=None):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(c.close)
            c.request('GET','/v1/frame',headers={'Authorization':auth} if auth else {});r=c.getresponse();return r.status,r.getheader('X-IH-Frame-Stale'),r.read()
        self.assertEqual(request()[0],401);self.assertEqual(opener.calls,0)
        self.assertEqual(request('Bearer incorrect')[0],401);self.assertEqual(opener.calls,0)
        opener.value=OSError();self.assertEqual(request('Bearer test-token')[0],503)
        opener.value=PNG
        self.assertEqual(request('Bearer test-token'),(200,'false',PNG))
        opener.value=OSError();self.assertEqual(request('Bearer test-token'),(200,'true',PNG))
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
    def test_render_is_admin_only_and_requires_profile(self):
        store=frames.Frames('',Opener())
        class Profile:
            def __init__(self,ready):self.ready=ready
            def read(self):return {'state':'ready','width':1920,'height':1080} if self.ready else {'state':'unavailable'}
        def server(peer,ready):
            s=HTTPServer(('127.0.0.1',0),main.make_handler(store,True,peer,Profile(ready),lambda:{'items':[],'tasks':[]}));threading.Thread(target=s.serve_forever,daemon=True).start();self.addCleanup(s.server_close);self.addCleanup(s.shutdown);return s.server_port
        def request(port):
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=3);self.addCleanup(c.close);c.request('GET','/api/render.svg');r=c.getresponse();return r.status,r.getheader('Content-Type'),r.read()
        self.assertEqual(request(server('192.0.2.1',True))[0],403)
        self.assertEqual(request(server('127.0.0.1',False))[0],503)
        status,kind,data=request(server('127.0.0.1',True));self.assertEqual(status,200);self.assertEqual(kind,'image/svg+xml');self.assertIn(b'<svg',data)

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
