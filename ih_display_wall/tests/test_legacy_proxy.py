import http.client
import pathlib
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import legacy_proxy

PNG=b'\x89PNG\r\n\x1a\nnew'

class Upstream(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        data=self.path.encode();self.send_response(200);self.send_header('Content-Type','text/plain');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_HEAD(self):
        data=self.path.encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers()
    def do_POST(self):
        data=self.rfile.read(int(self.headers['Content-Length']));self.send_response(201);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

class Tests(unittest.TestCase):
    def start(self,handler):
        server=HTTPServer(('127.0.0.1',0),handler);threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown);return server.server_port
    def request(self,port,method,path,body=None):
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3);self.addCleanup(conn.close);conn.request(method,path,body=body);r=conn.getresponse();return r.status,dict(r.getheaders()),r.read()
    def test_switch_preserves_other_routes_and_query(self):
        upstream=self.start(Upstream);proxy=self.start(legacy_proxy.make_handler('http://127.0.0.1:'+str(upstream),lambda:PNG,True))
        status,headers,body=self.request(proxy,'GET','/7?projectivy_house=42');self.assertEqual((status,headers['X-IH-Image-Source'],body),(200,'wall',PNG))
        self.assertEqual(self.request(proxy,'GET','/2?foo=1')[2],b'/2?foo=1')
        self.assertEqual(self.request(proxy,'POST','/other',b'payload')[::2],(201,b'payload'))
        status,headers,body=self.request(proxy,'HEAD','/7?x=1');self.assertEqual((status,int(headers['Content-Length']),body),(200,len(PNG),b''))
    def test_disabled_or_failed_renderer_falls_back_to_legacy(self):
        upstream=self.start(Upstream);url='http://127.0.0.1:'+str(upstream)
        for enabled,renderer in ((False,lambda:PNG),(True,lambda:b'bad')):
            proxy=self.start(legacy_proxy.make_handler(url,renderer,enabled));status,headers,body=self.request(proxy,'GET','/7?x=1')
            self.assertEqual((status,headers['X-IH-Image-Source'],body),(200,'legacy',b'/7?x=1'))
    def test_invalid_upstream_and_absolute_request_are_rejected(self):
        for value in ('http://user:password@example.test','http://example.test/path','https://example.test','http://example.test:bad'):
            self.assertIsNone(legacy_proxy.upstream_origin(value))
        proxy=self.start(legacy_proxy.make_handler('',lambda:PNG,False));self.assertEqual(self.request(proxy,'GET','/7')[0],503)
        self.assertEqual(self.request(proxy,'GET','http://other.test/7')[0],400)

if __name__=='__main__':unittest.main()
