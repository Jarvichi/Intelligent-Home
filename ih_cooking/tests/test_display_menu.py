import datetime
import http.client
import threading
import time
from http.server import ThreadingHTTPServer
import json
import pathlib
import sys
import tempfile
import types
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import display_menu
import main
import recipeadmin

class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.data=pathlib.Path(tmp.name);(self.data/'recipes').mkdir()
        for slug,title,minutes in [('a','Example first',25),('b','Example second',130),('c','Example third',40)]:
            (self.data/'recipes'/f'{slug}.json').write_text(json.dumps({'title':title,'steps':[{'before':minutes,'text':'Step'}]}))
        self.plan={'mon':'Example second','tue':'a'}
        def recipe(slug):return {'title':{'a':'Example first','b':'Example second','c':'Example third'}[slug], '_revision':'fixture-'+slug}
        self.store=types.SimpleNamespace(data=str(self.data),plan=lambda:(self.plan,'fixture'),recipe=recipe)
    def test_tonight_then_week_then_others_with_rounded_eat_time(self):
        now=datetime.datetime(2030,1,7,18,1);r=display_menu.read(self.store,now=now)
        self.assertEqual([x['slug'] for x in r['items']],['b','a','c']);self.assertTrue(r['items'][0]['tonight']);self.assertEqual(r['items'][1]['eat'],'18:30')
    def test_revision_ignores_time_but_tracks_plan(self):
        now=datetime.datetime(2030,1,7,18,1);a=display_menu.read(self.store,now=now);b=display_menu.read(self.store,now=now+datetime.timedelta(minutes=1));self.assertEqual(a['revision'],b['revision'])
        self.plan['mon']='a';self.assertNotEqual(a['revision'],display_menu.read(self.store,now=now)['revision'])
    def test_menu_read_holds_owner_write_lock_for_the_entire_snapshot(self):
        entered=threading.Event();release=threading.Event();writer_entered=threading.Event();self.addCleanup(release.set)
        original=self.store.recipe
        def read_recipe(slug):
            entered.set();release.wait(2);return original(slug)
        self.store.recipe=read_recipe
        reader=threading.Thread(target=display_menu.read,args=(self.store,));reader.start();self.assertTrue(entered.wait(1))
        def writer():
            with recipeadmin._WRITE_LOCK:writer_entered.set()
        other=threading.Thread(target=writer);other.start()
        self.assertFalse(writer_entered.wait(.1));release.set();reader.join(2);other.join(2)
        self.assertTrue(writer_entered.is_set());self.assertFalse(reader.is_alive())
    def test_menu_http_requires_existing_owner_token(self):
        self.store.opts={};self.store.clock=time.time;self.store.authed=lambda value:value=='Bearer fixture';self.store.stopped=lambda:False;self.store.rate_ok=lambda:True;self.store.invocation_line=lambda *a:None
        server=ThreadingHTTPServer(('127.0.0.1',0),main.make_handler(self.store));threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(auth):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(c.close);c.request('GET','/cook/menu?page=0',headers={'Authorization':'Bearer fixture'} if auth else {});r=c.getresponse();return r.status,json.loads(r.read())
        self.assertEqual(request(False)[0],401);status,value=request(True);self.assertEqual(status,200);self.assertEqual(len(value['items']),3)

    def test_page_bounds_and_empty_plan(self):
        self.plan.clear();r=display_menu.read(self.store,now=datetime.datetime(2030,1,7),page=999);self.assertEqual((r['page'],r['pages']),(0,1))

if __name__=='__main__':unittest.main()
