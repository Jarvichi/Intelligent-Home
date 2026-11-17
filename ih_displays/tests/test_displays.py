import http.client
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import main
import profiles

PROFILE={'name':'Example display','kind':'kindle','width':600,'height':800,'colour':'grayscale','touch':True,'power':'battery','transport':'image','refresh_seconds':300}

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=str(pathlib.Path(self.tmp.name)/'db.sqlite')
        db=profiles.connect(self.path);db.close()
    def server(self,ingress=False,peer='127.0.0.1',token='example-token'):
        s=HTTPServer(('127.0.0.1',0),main.make_handler(self.path,token,ingress,peer));threading.Thread(target=s.serve_forever,daemon=True).start();self.addCleanup(s.server_close);self.addCleanup(s.shutdown);return s.server_port
    def req(self,port,method,path,body=None,auth=None):
        c=http.client.HTTPConnection('127.0.0.1',port,timeout=3);self.addCleanup(c.close);headers={}
        if body is not None: body=json.dumps(body);headers['Content-Type']='application/json'
        if auth: headers['Authorization']='Bearer '+auth
        c.request(method,path,body,headers);r=c.getresponse();raw=r.read();return r.status,json.loads(raw)
    def test_private_api_and_concurrent_edits(self):
        admin=self.server(True);api=self.server()
        self.assertEqual(self.req(api,'GET','/healthz')[0],200)
        self.assertEqual(self.req(api,'GET','/v1/profiles')[0],401)
        code,created=self.req(admin,'POST','/api/v1/profiles',PROFILE);self.assertEqual(code,201)
        self.assertEqual(self.req(api,'GET','/v1/profiles',auth='example-token')[1]['profiles'],[created])
        self.assertEqual(self.req(api,'POST','/v1/profiles',PROFILE,'example-token')[0],405)
        path='/api/v1/profiles/'+created['id']
        changed=dict(PROFILE,name='Another display')
        code,updated=self.req(admin,'PUT',path,{'profile':changed,'revision':created['revision']});self.assertEqual(code,200)
        self.assertEqual(self.req(admin,'PUT',path,{'profile':PROFILE,'revision':created['revision']})[0],409)
        self.assertEqual(self.req(admin,'DELETE',path,{'revision':created['revision']})[0],409)
        self.assertEqual(self.req(admin,'DELETE',path,{'revision':updated['revision']})[0],200)
        self.assertEqual(self.req(api,'GET','/v1/profiles',auth='example-token')[1]['profiles'],[])
    def test_ingress_rejects_untrusted_peer_and_empty_token_disables_api(self):
        denied=self.server(True,peer='192.0.2.1');api=self.server(token='')
        self.assertEqual(self.req(denied,'GET','/api/v1/profiles')[0],403)
        self.assertEqual(self.req(api,'GET','/v1/profiles',auth='example-token')[0],401)
    def test_rejects_invalid_or_unexpected_values(self):
        admin=self.server(True)
        for change in ({'width':True},{'refresh_seconds':0},{'kind':[]},{'address':'example'},{'touch':'yes'}):
            self.assertEqual(self.req(admin,'POST','/api/v1/profiles',dict(PROFILE,**change))[0],400)
        self.assertEqual(self.req(admin,'GET','/api/v1/profiles')[1]['profiles'],[])

if __name__=='__main__': unittest.main()
