"""Exercise the real bearer and ingress contract with temporary recipes only."""
import json
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import main
import recipeadmin

class RecipeHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = pathlib.Path(self.temp.name); (self.data / 'recipes').mkdir()
        self.file = self.data / 'recipes/sample.json'
        self.file.write_text(json.dumps({'title':'Sample','steps':[{'before':0,'text':'Serve'}]}))
        def recipe(slug):
            f=self.data / 'recipes' / (slug+'.json')
            if not f.exists(): return None
            r=json.loads(f.read_text());r['_revision']=recipeadmin.revision(r);return r
        self.st=types.SimpleNamespace(data=str(self.data),opts={},clock=time.time,authed=lambda h:h=='Bearer fixture',
            stopped=lambda:False,rate_ok=lambda:True,invocation_line=lambda *a:None,
            recipes=lambda:recipeadmin.listing(str(self.data)),recipe=recipe,publish=lambda:{'ok':True})
        handler=main.make_handler(self.st);handler.log_message=lambda *a:None
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,method,path,body=None,authed=True):
        request=urllib.request.Request('http://127.0.0.1:%d%s'%(self.server.server_port,path),method=method,
            data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer fixture'} if authed else {})
        try: response=urllib.request.urlopen(request,timeout=3)
        except urllib.error.HTTPError as e: response=e
        with response:return response.status,json.load(response)
    def test_snapshot_auth_and_actual_listing_shape(self):
        self.assertEqual(self.request('GET','/recipe-snapshot',authed=False)[0],401)
        self.assertIn('recipes',self.request('GET','/recipes')[1])
        snapshot=self.request('GET','/recipe-snapshot')[1]
        self.assertEqual(snapshot['sample']['_revision'],recipeadmin.revision(json.loads(self.file.read_text())))
    def test_bearer_and_ingress_require_revision_and_reject_stale(self):
        original=self.file.read_text()
        self.assertEqual(self.request('POST','/recipes/sample',{'title':'Missing revision'})[0],409)
        self.assertEqual(main.ingress_api(self.st,'PUT','/recipes/sample',{'title':'Missing revision'})[0],409)
        self.assertEqual(self.file.read_text(),original)
        revision=self.request('GET','/recipes/sample')[1]['_revision']
        self.assertEqual(self.request('POST','/recipes/sample',{'title':'First','_revision':revision})[0],200)
        self.assertEqual(self.request('POST','/recipes/sample',{'title':'Stale','_revision':revision})[0],422)
        self.assertEqual(json.loads(self.file.read_text())['title'],'First')

if __name__=='__main__':unittest.main()
