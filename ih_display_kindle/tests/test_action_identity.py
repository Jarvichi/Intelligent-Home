"""Source identity survives the display feed and binds actionable frames."""
import datetime
import io
import json
import pathlib
import sys
import time
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ih_displays'/'app'))
sys.path.insert(0,str(ROOT/'ih_display_kindle'/'app'))
import device_surface, dishwasher, household_tasks, kindle_actions, messages, profile_client, render

NOW=2000000000
CHANGED=datetime.datetime.fromtimestamp(NOW-1,datetime.timezone.utc).isoformat()
CHANGED_AGAIN=datetime.datetime.fromtimestamp(NOW,datetime.timezone.utc).isoformat()

class Response(io.BytesIO):
    status=200
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

class SourceOpener:
    def __init__(self,states):self.states=states
    def open(self,req,timeout):return Response(json.dumps(self.states[req.full_url.rsplit('/',1)[-1]]).encode())

class FeedOpener:
    def __init__(self,feed):self.feed=feed
    def open(self,req,timeout):return Response(json.dumps(self.feed.read()).encode())

class FakeHA(render.HA):
    def __init__(self,states):self.states=states;self.calls=[]
    def optional_state(self,entity):return self.states.get(entity)
    def request(self,path,body=None):self.calls.append((path,body));return {}

class Tests(unittest.TestCase):
    def setUp(self):
        self.states={
            'input_text.example':{'state':'Example | Details','last_changed':CHANGED},
            'input_boolean.example':{'state':'on','last_changed':CHANGED},
            'input_datetime.example':{'state':'2033-05-18 00:00:00','attributes':{'timestamp':NOW-20000}},
            'input_text.wheel':{'state':'spun:3','last_changed':CHANGED},
            'sensor.battery':{'state':'80','attributes':{'charging':True}},
            'sensor.seen':{'state':datetime.datetime.fromtimestamp(NOW,datetime.timezone.utc).isoformat()},
        }
        feed=messages.Messages(['input_text.example'],'fixture',SourceOpener(self.states))
        dish=dishwasher.Dishwasher('input_boolean.example','input_datetime.example','fixture',SourceOpener(self.states),clock=lambda:NOW)
        energy=household_tasks.EnergyReminders('','input_text.wheel','','',self.states.get,clock=lambda:NOW,localtime=time.gmtime,provider='Example')
        self.combined=dishwasher.Combined(feed,dish,energy,groups=('message','task','task'))
        self.client=profile_client.ProfileClient('http://example.test','fixture','',FeedOpener(self.combined))
        self.ha=FakeHA(self.states)
        self.cfg={'kindle_battery_entity':'sensor.battery','kindle_last_seen_entity':'sensor.seen','kindle_message_entities':['input_text.example'],'kindle_dishwasher_entity':'input_boolean.example','kindle_wheel_entity':'input_text.wheel'}
        self.actions=kindle_actions.KindleActions(self.ha,lambda:self.cfg,clock=lambda:NOW)
    def picture(self,feed):
        data=self.ha.snapshot([], '',shared=lambda:feed)
        data['device']={'charging':True};data['cooking']={'running':False}
        surface=device_surface.DeviceSurface(lambda:data,lambda:{'configured':True,'complete':True,'alerts':[]},None,None,self.actions,lambda value:b'png',self.ha,lambda:self.cfg,clock=lambda:NOW)
        return surface.build('main',0,'asap')[1]
    def test_owner_states_survive_client_and_snapshot_into_descriptors(self):
        feed=self.client.message_tasks();self.assertTrue(feed['complete'])
        self.assertEqual({x['action_kind'] for x in feed['items']},{'message','dishwasher','wheel'})
        zones=self.picture(feed);actions=[z['action'] for z in zones]
        for kind in ('message','dishwasher','wheel'):
            descriptor=next(x for x in actions if x['kind']==kind)
            row=next(x for x in feed['items'] if x['action_kind']==kind)
            self.assertEqual(descriptor['expected'],row['action_identity'])
            self.assertTrue(self.actions.dispatch(descriptor)['ok'])
        self.assertEqual(len(self.ha.calls),3)
    def test_changed_source_between_feed_and_frame_never_becomes_actionable(self):
        feed=self.client.message_tasks()
        for entity in ('input_text.example','input_boolean.example','input_text.wheel'):
            self.states[entity]=dict(self.states[entity],last_changed=CHANGED_AGAIN)
        self.assertFalse([z for z in self.picture(feed) if z['action'].get('kind') in ('message','dishwasher','wheel')])
        self.assertEqual(self.ha.calls,[])
    def test_changed_source_after_frame_is_rejected(self):
        feed=self.client.message_tasks();zones=self.picture(feed)
        descriptor=next(z['action'] for z in zones if z['action'].get('kind')=='dishwasher')
        self.states['input_boolean.example']=dict(self.states['input_boolean.example'],last_changed=CHANGED_AGAIN)
        self.assertFalse(self.actions.dispatch(descriptor)['ok']);self.assertEqual(self.ha.calls,[])
    def test_missing_identity_keeps_row_but_disables_action(self):
        feed=self.client.message_tasks()
        for item in feed['items']:item.pop('action_identity',None)
        self.assertEqual(len(feed['items']),3)
        self.assertFalse([z for z in self.picture(feed) if z['action'].get('kind') in ('message','dishwasher','wheel')])

if __name__=='__main__':unittest.main()
