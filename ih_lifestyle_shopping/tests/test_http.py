import http.client
import json
import pathlib
import sys
import threading
import unittest
from http.server import HTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import layouts
import list_bridge
import main
import service
from test_list_bridge import FakeHA


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.db=layouts.connect(':memory:');self.addCleanup(self.db.close)
        self.ha=FakeHA();self.app=service.App(self.db,list_bridge.ShoppingList(self.ha,'todo.fixture'))

    def server(self, ingress=False, peer='127.0.0.1'):
        server=HTTPServer(('127.0.0.1',0),main.make_handler(self.app,'fixture',ingress,peer))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return server.server_port

    def request(self, port, method, path, body=None, headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        self.addCleanup(conn.close)
        conn.request(method,path,body=body,headers=headers or {})
        response=conn.getresponse();return response.status,json.loads(response.read())

    def test_api_auth_and_no_default_writes(self):
        port=self.server()
        self.assertEqual(self.request(port,'GET','/healthz')[0],200)
        self.assertEqual(self.request(port,'GET','/list')[0],401)
        self.assertEqual(self.request(port,'GET','/list',headers={'Authorization':'Bearer fixture'})[0],200)
        headers={'Authorization':'Bearer fixture','Content-Type':'application/json'}
        self.assertEqual(self.request(port,'POST','/list/add',body='{"name":"Example"}',headers=headers)[0],403)
        self.assertEqual(self.ha.calls,[])

    def test_ingress_gateway_and_json_requirement(self):
        denied=self.server(ingress=True,peer='192.0.2.1')
        self.assertEqual(self.request(denied,'GET','/api/list')[0],403)
        permitted=self.server(ingress=True)
        self.assertEqual(self.request(permitted,'GET','/api/list')[0],200)
        self.assertEqual(self.request(permitted,'POST','/api/list/add',body='{}',headers={'Content-Type':'text/plain'})[0],400)
        self.assertEqual(self.ha.calls,[])

    def test_page_is_available_only_via_ingress_gateway(self):
        denied=self.server(ingress=True,peer='192.0.2.1')
        self.assertEqual(self.request(denied,'GET','/')[0],403)
        permitted=self.server(ingress=True)
        conn=http.client.HTTPConnection('127.0.0.1',permitted,timeout=2)
        self.addCleanup(conn.close)
        conn.request('GET','/')
        response=conn.getresponse()
        self.assertEqual(response.status,200)
        self.assertIn(b'Shopping',response.read())


if __name__ == '__main__': unittest.main()
