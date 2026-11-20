"""Revision and row-bound cooking actions; all state is a temporary fixture."""
import http.client
import datetime
import json
import pathlib
import threading
import time
import types
from http.server import ThreadingHTTPServer
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import cookengine
import main

class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.time=[1000]
        self.engine=cookengine.Engine({'mode':'dry'},tmp.name,ts=lambda:self.time[0]);self.engine.thread=type('Running',(),{'is_alive':lambda self:True})()
        self.keys=['12:00 First step','12:00 Second step'];self.plan={'title':'Example','eat':'13:00','steps':[{'at':'12:00','text':'First step','short':'First'},{'at':'12:00','text':'Second step','short':'Second'}],'prep':[{'text':'Example prep'}]}
        self.engine.dump('plan.json',self.plan);self.engine.dump('state.json',{'done':[],'told':self.keys});self.engine.dump('shownext.json',self.keys);self.engine.dump('shown.json',['Example prep'])
    def test_snapshot_and_exact_action(self):
        snap=self.engine.display_snapshot();self.assertTrue(snap['running']);self.assertEqual([r['text'] for r in snap['next']],['First','Second'])
        result=self.engine.display_action(snap['revision'],'done',snap['next'][0]['id']);self.assertTrue(result['ok']);self.assertEqual(result['key'],self.keys[0])
        self.assertEqual(self.engine.display_action(snap['revision'],'done',snap['next'][0]['id'])['why'],'stale display')
    def test_reordered_rows_and_replaced_plan_are_rejected(self):
        snap=self.engine.display_snapshot();self.engine.dump('shownext.json',list(reversed(self.keys)))
        self.assertEqual(self.engine.display_action(snap['revision'],'done',snap['next'][0]['id'])['why'],'stale display')
        snap=self.engine.display_snapshot();self.engine.dump('plan.json',dict(self.plan,title='Different meal'))
        self.assertEqual(self.engine.display_action(snap['revision'],'done',snap['next'][0]['id'])['why'],'stale display')
    def test_background_rewrites_do_not_stale_the_display(self):
        snap=self.engine.display_snapshot();self.engine.dump('plan.json',dict(self.plan,oven_target=180));self.engine.dump('state.json',{'done':[],'told':self.keys,'snap':{'keys':self.keys,'ts':5}})
        self.assertEqual(self.engine.display_snapshot()['revision'],snap['revision'])
        self.assertTrue(self.engine.display_action(snap['revision'],'done',snap['next'][0]['id'])['ok'])
    def test_batch_applies_every_tap_against_one_picture(self):
        snap=self.engine.display_snapshot();ids=[r['id'] for r in snap['next']]
        result=self.engine.display_actions(snap['revision'],[{'action':'done','item_id':ids[0]},{'action':'done','item_id':ids[1]}])
        self.assertTrue(result['ok']);self.assertEqual(result['applied'],2);self.assertEqual(result['failed'],[])
        self.assertEqual(self.engine.load('state.json',{})['done'],self.keys)
        again=self.engine.display_actions(snap['revision'],[{'action':'done','item_id':ids[0]}])
        self.assertFalse(again['ok'])
    def test_batch_rejects_stale_picture_and_bad_input(self):
        snap=self.engine.display_snapshot();rid=snap['next'][0]['id']
        self.assertEqual(self.engine.display_actions('0'*64,[{'action':'done','item_id':rid}])['why'],'stale display')
        self.assertFalse(self.engine.display_actions(snap['revision'],[{'action':'stop','item_id':rid}])['ok'])
        self.assertFalse(self.engine.display_actions(snap['revision'],[])['ok'])
    def test_invalid_and_ambiguous_targets_are_rejected(self):
        snap=self.engine.display_snapshot();self.assertFalse(self.engine.display_action(snap['revision'],'delete',snap['next'][0]['id'])['ok'])
        self.assertEqual(self.engine.display_action(snap['revision'],'done','x')['why'],'stale item')
        self.engine.dump('shown.json',['Example prep','Example prep']);snap=self.engine.display_snapshot()
        self.assertEqual(self.engine.display_action(snap['revision'],'tick',snap['prep'][0]['id'])['why'],'ambiguous item')

    def test_stale_prep_projection_does_not_target_a_duplicate(self):
        self.plan['prep']=[{'text':'Example prep'},{'text':'Example prep'}]
        self.engine.dump('plan.json',self.plan)
        self.assertEqual(self.engine.display_snapshot()['prep'],[])

    def test_invalid_start_does_not_consume_request_id(self):
        self.engine.thread=None
        self.engine.cfg['mode']='off'
        self.assertFalse(self.engine.start_from_display({},'example','19:00','a'*32)['ok'])
        self.assertEqual(self.engine.load('display-requests.json',[]),[])

    def test_corrupt_request_journal_fails_closed(self):
        self.engine.thread=None
        pathlib.Path(self.engine.p('display-requests.json')).write_text('{broken')
        self.assertEqual(self.engine.start_from_display({},'example','19:00','a'*32)['why'],'request journal unavailable')

    def test_duplicate_step_keys_have_no_actionable_rows(self):
        self.plan['steps'].append(dict(self.plan['steps'][0]))
        self.engine.dump('plan.json',self.plan)
        self.assertEqual([r['text'] for r in self.engine.display_snapshot()['next']],['Second'])

    def test_stopped_plan_is_not_restored_by_an_inflight_gate_tick(self):
        self.plan['steps'][0]['gate']=True;self.engine.dump('plan.json',self.plan)
        self.engine.now=lambda:datetime.datetime(2030,1,7,12,0)
        self.engine._sync_fact=lambda plan:self.engine.stop_cook()
        with patch.object(cookengine,'gate_status',return_value=(True,5,None)):
            self.assertTrue(self.engine.tick())
        self.assertFalse(pathlib.Path(self.engine.p('plan.json')).exists())

    def test_stale_publish_cannot_replace_new_row_mapping(self):
        old=self.plan.copy();self.plan['prep']=[{'text':'Changed'}];self.engine.dump('plan.json',self.plan)
        self.engine.publish(old,{'done':[]})
        self.assertEqual(self.engine.load('shown.json',[]),['Example prep'])

    def test_stopped_session_publishes_off_after_completed_steps(self):
        self.engine.dump('state.json',{'done':[self.keys[0]]})
        self.engine.stop_cook()
        self.assertEqual(self.engine.publish(None,{'done':[]},False)['state'],'off')
    def test_legacy_snapshot_cannot_target_different_row(self):
        state={'done':[],'told':self.keys,'snap':{'keys':list(reversed(self.keys)),'ts':self.time[0]}}
        self.engine.dump('state.json',state);snap=self.engine.display_snapshot()
        self.assertEqual(self.engine.display_action(snap['revision'],'done',snap['next'][0]['id'])['why'],'stale item')
        self.assertEqual(self.engine.load('state.json',{})['done'],[])
    def test_completed_row_cannot_be_acknowledged_or_shifted(self):
        self.engine.dump('state.json',{'done':[self.keys[0]],'told':self.keys})
        snap=self.engine.display_snapshot()
        for action in ('ack','delay','early','done'):
            self.assertEqual(self.engine.display_action(snap['revision'],action,snap['next'][0]['id'])['why'],'stale item')

    def test_concurrent_duplicate_taps_cannot_complete_two_steps(self):
        snap=self.engine.display_snapshot();barrier=threading.Barrier(3);results=[]
        def run():
            barrier.wait();results.append(self.engine.display_action(snap['revision'],'done',snap['next'][0]['id']))
        threads=[threading.Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        barrier.wait()
        for t in threads:t.join(timeout=3)
        self.assertEqual(sum(r.get('ok') is True for r in results),1)
        self.assertEqual(self.engine.load('state.json',{})['done'],[self.keys[0]])

    def test_stopped_thread_does_not_expose_an_old_plan(self):
        self.engine.thread=None;snap=self.engine.display_snapshot()
        self.assertFalse(snap['running']);self.assertIsNone(snap['revision'])

    def test_http_auth_and_stale_action(self):
        st=types.SimpleNamespace(engine=self.engine,opts={},clock=time.time,authed=lambda value:value=='Bearer fixture',stopped=lambda:False,rate_ok=lambda:True,invocation_line=lambda *a:None)
        server=ThreadingHTTPServer(('127.0.0.1',0),main.make_handler(st));threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(method,path,body=None,auth=True):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(c.close)
            c.request(method,path,body=json.dumps(body) if body is not None else None,headers={'Authorization':'Bearer fixture'} if auth else {});r=c.getresponse();return r.status,json.loads(r.read())
        self.assertEqual(request('GET','/cook/display',auth=False)[0],401)
        status,snap=request('GET','/cook/display');self.assertEqual(status,200)
        action={'revision':snap['revision'],'action':'done','item_id':snap['next'][0]['id']}
        self.assertEqual(request('POST','/cook/display-action',action,auth=False)[0],401)
        self.assertEqual(request('POST','/cook/display-action',action)[0],200)
        self.assertEqual(request('POST','/cook/display-action',action)[0],409)

    def test_display_start_is_idempotent_and_preserves_active_session(self):
        called=[];self.engine.running=lambda:False
        self.engine.start_cook=lambda recipe,name,eat,allow_replace=True,lock_timeout=None:(called.append((name,eat,allow_replace)) or {'ok':True})
        request='a'*32;self.assertTrue(self.engine.start_from_display({},'example','19:00',request)['ok'])
        self.assertEqual(self.engine.start_from_display({},'example','19:00',request)['why'],'request already handled')
        self.assertEqual(called,[('example','19:00',False)])
        self.engine.running=lambda:True
        self.assertEqual(self.engine.start_from_display({},'example','19:00','b'*32)['why'],'cooking already active')
        self.assertNotIn('b'*32,self.engine.load('display-requests.json',[]))

    def test_concurrent_display_starts_never_replace_a_running_cook(self):
        self.engine.thread=None
        gate=threading.Event();self.addCleanup(gate.set)
        self.engine._loop=lambda:gate.wait(3)
        self.engine.guard=lambda force=False:(False,'dry')
        recipe={'title':'Example','steps':[{'before':10,'text':'Prepare'}]}
        barrier=threading.Barrier(3);results=[]
        def run(request):
            barrier.wait();results.append(self.engine.start_from_display(recipe,'example','19:00',request))
        threads=[threading.Thread(target=run,args=(v*32,)) for v in ('a','b')]
        for thread in threads:thread.start()
        barrier.wait()
        for thread in threads:thread.join(timeout=3)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sum(result.get('ok') is True for result in results),1)
        self.assertEqual(sum(result.get('why')=='cooking already active' for result in results),1)
    def test_display_start_http_checks_recipe_revision_and_authentication(self):
        calls=[];self.engine.start_from_display=lambda recipe,name,eat,rid:(calls.append((name,eat,rid)) or {'ok':True});self.engine.now=lambda:datetime.datetime(2030,1,7,18,1)
        st=types.SimpleNamespace(engine=self.engine,opts={},clock=time.time,authed=lambda value:value=='Bearer fixture',stopped=lambda:False,rate_ok=lambda:True,invocation_line=lambda *a:None,recipe=lambda name:{'title':'Example','steps':[{'before':40}], '_revision':'a'*64} if name=='example' else None)
        server=ThreadingHTTPServer(('127.0.0.1',0),main.make_handler(st));threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(body,auth=True):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(c.close);c.request('POST','/cook/menu-start',body=json.dumps(body),headers={'Authorization':'Bearer fixture'} if auth else {});r=c.getresponse();return r.status,json.loads(r.read())
        body={'slug':'example','recipe_revision':'b'*64,'eat':'19:00','request_id':'c'*32}
        self.assertEqual(request(body,False)[0],401);self.assertEqual(request(body)[0],409);self.assertEqual(calls,[])
        body['recipe_revision']='a'*64;self.assertEqual(request(body)[0],200);self.assertEqual(len(calls),1)
        body['eat']='18:00';body['request_id']='d'*32;self.assertEqual(request(body)[0],200);self.assertEqual(calls[-1][1],'18:45')
        body['slug']='../example';self.assertEqual(request(body)[0],400)

    def test_missing_plan_has_no_actions(self):
        self.engine.stop_cook();snap=self.engine.display_snapshot();self.assertFalse(snap['running']);self.assertIsNone(snap['revision'])

if __name__=='__main__':unittest.main()
