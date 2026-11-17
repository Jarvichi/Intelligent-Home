import importlib.util
import io
import tempfile
import threading
from http.server import HTTPServer
import json
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
from profile_client import ProfileClient

IDENT='a'*32
PROFILE={'id':IDENT,'kind':'wall','name':'Example','width':1920,'height':1080,'refresh_seconds':60,'touch':False}
class Response(io.BytesIO): status=200
class Opener:
    def __init__(self,body): self.body=body;self.calls=0;self.authorization=None
    def open(self,request,timeout):
        self.calls+=1;self.authorization=request.get_header('Authorization')
        if isinstance(self.body,Exception):raise self.body
        return Response(json.dumps(self.body).encode())
class Tests(unittest.TestCase):
    def test_ready_and_credential_not_in_response(self):
        opener=Opener(PROFILE);result=ProfileClient('http://profiles.example','secret-example',IDENT,opener).read()
        self.assertEqual(opener.authorization,'Bearer secret-example');self.assertEqual(result['state'],'ready');self.assertNotIn('secret-example',json.dumps(result));self.assertEqual(result['width'],1920)
    def test_missing_bad_and_unavailable(self):
        for url,token,ident in (('','',''),('http://profiles.example','','a'*32),('http://profiles.example?x=y','token',IDENT),('http://profiles.example','token','not-an-id')):
            opener=Opener(PROFILE);self.assertEqual(ProfileClient(url,token,ident,opener).read()['state'],'not_configured');self.assertEqual(opener.calls,0)
        self.assertEqual(ProfileClient('http://profiles.example','token',IDENT,Opener(OSError())).read()['state'],'unavailable')
    def test_against_shared_display_api(self):
        root=pathlib.Path(__file__).resolve().parents[2]/'ih_displays'/'app'
        sys.path.insert(0,str(root));self.addCleanup(sys.path.remove,str(root))
        spec=importlib.util.spec_from_file_location('shared_displays_main',root/'main.py');shared=importlib.util.module_from_spec(spec);spec.loader.exec_module(shared)
        with tempfile.TemporaryDirectory() as folder:
            path=str(pathlib.Path(folder)/'profiles.sqlite');db=shared.profiles.connect(path)
            doc={'name':'Example screen','kind':'wall','width':1920,'height':1080,'colour':'colour','touch':False,'power':'mains','transport':'image','refresh_seconds':60}
            profile=shared.profiles.create(db,doc);db.close()
            server=HTTPServer(('127.0.0.1',0),shared.make_handler(path,'example-token'));threading.Thread(target=server.serve_forever,daemon=True).start()
            try:
                client=ProfileClient('http://127.0.0.1:'+str(server.server_port),'example-token',profile['id'])
                self.assertEqual(client.read()['state'],'ready')
                self.assertEqual(ProfileClient(client.url,'wrong',profile['id']).read()['state'],'unavailable')
            finally:server.shutdown();server.server_close()

    def test_wrong_type_or_identity_rejected(self):
        for profile in (dict(PROFILE,kind='eink'),dict(PROFILE,id='b'*32),dict(PROFILE,width=True)):
            self.assertEqual(ProfileClient('http://profiles.example','token',IDENT,Opener(profile)).read()['state'],'invalid')

if __name__=='__main__':unittest.main()
