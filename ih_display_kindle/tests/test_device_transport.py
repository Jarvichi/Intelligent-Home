"""Device HTTP and shell protocol tests use only synthetic fixtures."""
import http.client
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

APP=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP/'app'))
import device_frames
import device_http
import device_surface

PNG=b'\x89PNG\r\n\x1a\nfixture'
TOKEN='a'*40

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        self.store=device_frames.DeviceFrames(lambda x:self.calls.append(x) or {'ok':True})
        def build(screen,page,eat):
            self.selection=(screen,page,eat)
            return PNG,[{'box':[10,20,200,220],'action':{'kind':'fixture'}}]
        self.server=ThreadingHTTPServer(('127.0.0.1',0),device_http.make_handler(self.store,build,TOKEN))
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def request(self,path,auth=None,method='GET',body=None):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3);self.addCleanup(c.close)
        c.request(method,path,body=body,headers={'Authorization':auth} if auth else {})
        r=c.getresponse();return r.status,r.read()
    def test_auth_is_required_and_protocol_rejects_untrusted_target(self):
        self.assertEqual(self.request('/device/v1/frame')[0],401)
        self.assertEqual(self.request('/device/v1/frame','Bearer wrong')[0],401)
        self.assertEqual(self.request('/device/v1/frame?screen=invalid','Bearer '+TOKEN)[0],400)
        self.assertEqual(self.request('/device/v1/tap','Bearer '+TOKEN,'POST','frame=x&zone=y&kind=lights')[0],400)
        self.assertEqual(self.calls,[])
    def test_real_shell_client_fetch_draw_and_tap(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=pathlib.Path(tmp)/'curl.conf';config.write_text('header = "Authorization: Bearer '+TOKEN+'"\n')
            draw='/bin/true'
            env=dict(os.environ,IH_DEVICE_URL=self.base,IH_DEVICE_CURL_CONFIG=str(config),IH_DEVICE_DIR=tmp+'/state',IH_DEVICE_DRAW=str(draw),CAPTURE=tmp+'/drawn.png')
            script=str(APP/'device/ih-device.sh')
            subprocess.run(['sh',script,'refresh','dinner','1','1230'],env=env,check=True,capture_output=True)
            self.assertEqual(self.selection,('dinner',1,'1230'))
            zone=pathlib.Path(tmp+'/state/zones').read_text().split()[0]
            subprocess.run(['sh',script,'tap-xy','20','30'],env=env,check=True,capture_output=True)
            self.assertEqual(self.calls,[{'kind':'fixture'}])
            result=subprocess.run(['sh',script,'tap',zone],env=env,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(len(self.calls),1)

class FakeActions:
    def describe(self,kind,**kw):return {'kind':kind,**kw}
    def message_for(self,title,detail,source=None):return {'kind':'message','source':source}
    def dispatch(self,d):return {'ok':True}

class WifiTests(unittest.TestCase):
    def test_private_qr_stays_local_and_return_works_offline(self):
        import io
        requests=[]
        frames=device_frames.DeviceFrames(lambda action:{'ok':True,'navigate':{'screen':'wifi','page':0,'eat':'asap'}})
        def build(screen,page,eat):
            return PNG,[{'box':[140,860,932,976],'action':{'kind':'navigate'}}]
        base=device_http.make_handler(frames,build,TOKEN)
        class Recording(base):
            def do_GET(self):
                requests.append((self.path,b''));super().do_GET()
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                requests.append((self.path,body));self.rfile=io.BytesIO(body);super().do_POST()
        server=ThreadingHTTPServer(('127.0.0.1',0),Recording)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        with tempfile.TemporaryDirectory() as tmp:
            config=pathlib.Path(tmp)/'curl.conf';config.write_text('header = "Authorization: Bearer '+TOKEN+'"\n')
            private=pathlib.Path(tmp)/'private.png';secret=PNG+b'private-qr-fixture';private.write_bytes(secret)
            env=dict(os.environ,IH_DEVICE_URL='http://127.0.0.1:'+str(server.server_port),IH_DEVICE_CURL_CONFIG=str(config),IH_DEVICE_DIR=tmp+'/state',IH_DEVICE_DRAW='/bin/true',IH_DEVICE_WIFI_IMAGE=str(private))
            script=str(APP/'device/ih-device.sh');state=pathlib.Path(tmp)/'state'
            subprocess.run(['sh',script,'refresh','menu'],env=env,check=True,capture_output=True)
            menu_frame=(state/'current').read_text()
            subprocess.run(['sh',script,'tap-xy','200','900'],env=env,check=True,capture_output=True)
            self.assertEqual((state/'current').read_text().strip(),'local-wifi')
            self.assertTrue(all(secret not in body and str(private) not in path for path,body in requests))
            server.shutdown()
            count=len(requests)
            subprocess.run(['sh',script,'tap-xy','10','10'],env=env,check=True,capture_output=True)
            self.assertEqual((state/'current').read_text(),menu_frame)
            self.assertEqual(len(requests),count)

class FakeCooking:
    def __init__(self):self.calls=[]
    def post(self,path,body):self.calls.append((path,body));return {'ok':True}

class CachedScreenTests(unittest.TestCase):
    def test_slow_prefetch_does_not_block_a_foreground_refresh(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        frames=device_frames.DeviceFrames(lambda action:{'ok':True})
        def build(screen,page,eat):
            if screen=='home':entered.set();release.wait(4)
            return PNG,[{'box':[10,20,200,220],'action':{'kind':'fixture'}}]
        server=ThreadingHTTPServer(('127.0.0.1',0),device_http.make_handler(frames,build,TOKEN))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            config=pathlib.Path(tmp)/'curl.conf';config.write_text('header = "Authorization: Bearer '+TOKEN+'"\n')
            env=dict(os.environ,IH_DEVICE_URL='http://127.0.0.1:'+str(server.server_port),IH_DEVICE_CURL_CONFIG=str(config),IH_DEVICE_DIR=tmp+'/state',IH_DEVICE_DRAW='/bin/true',IH_DEVICE_CHARGING='true')
            script=str(APP/'device/ih-device.sh')
            worker=subprocess.Popen(['sh',script,'prefetch','home'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                self.assertTrue(entered.wait(2))
                result=subprocess.run(['sh',script,'refresh','main'],env=env,capture_output=True,timeout=3)
                self.assertEqual(result.returncode,0,result.stderr)
            finally:
                release.set();worker.communicate(timeout=5)
            self.assertEqual(worker.returncode,0)

    def test_local_back_during_slow_navigation_discards_late_picture(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def dispatch(action):
            entered.set();release.wait(4)
            return {'ok':True,'navigate':{'screen':'home','page':0,'eat':'asap'}}
        frames=device_frames.DeviceFrames(dispatch)
        def build(screen,page,eat):return PNG+screen.encode(),[{'box':[10,20,200,220],'power':'mains','preview':'home','action':{'kind':'navigate'}}]
        server=ThreadingHTTPServer(('127.0.0.1',0),device_http.make_handler(frames,build,TOKEN))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            config=pathlib.Path(tmp)/'curl.conf';config.write_text('header = "Authorization: Bearer '+TOKEN+'"\n')
            state=pathlib.Path(tmp)/'state';script=str(APP/'device/ih-device.sh')
            env=dict(os.environ,IH_DEVICE_URL='http://127.0.0.1:'+str(server.server_port),IH_DEVICE_CURL_CONFIG=str(config),IH_DEVICE_DIR=str(state),IH_DEVICE_DRAW='/bin/true',IH_DEVICE_CHARGING='true')
            def run(*args):return subprocess.run(['sh',script,*args],env=env,capture_output=True)
            self.assertEqual(run('refresh','main').returncode,0);original=(state/'current').read_text()
            zone=(state/'zones').read_text().split()[0]
            self.assertEqual(run('prefetch','home').returncode,0);self.assertEqual(run('show-cached','home').returncode,0)
            worker=subprocess.Popen(['sh',script,'tap-cached',zone],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertTrue(entered.wait(2))
            self.assertEqual(run('tap-xy','150','1020').returncode,0)
            self.assertEqual((state/'current').read_text(),original)
            release.set();worker.communicate(timeout=3)
            self.assertNotEqual(worker.returncode,0)
            self.assertEqual((state/'current').read_text(),original)

    def test_instant_cache_tap_binds_original_frame_and_navigation(self):
        calls=[]
        def dispatch(action):
            calls.append(action)
            return {'ok':True,'navigate':{'screen':'home','page':0,'eat':'asap'}}
        frames=device_frames.DeviceFrames(dispatch)
        def build(screen,page,eat):
            return PNG+screen.encode(),[{'box':[10,20,200,220],'power':'mains','preview':'home','action':{'kind':'navigate','screen':'home'}}]
        server=ThreadingHTTPServer(('127.0.0.1',0),device_http.make_handler(frames,build,TOKEN))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            config=pathlib.Path(tmp)/'curl.conf';config.write_text('header = "Authorization: Bearer '+TOKEN+'"\n')
            state=pathlib.Path(tmp)/'state';script=str(APP/'device/ih-device.sh')
            env=dict(os.environ,IH_DEVICE_URL='http://127.0.0.1:'+str(server.server_port),IH_DEVICE_CURL_CONFIG=str(config),IH_DEVICE_DIR=str(state),IH_DEVICE_DRAW='/bin/true',IH_DEVICE_CHARGING='true')
            def run(*args):return subprocess.run(['sh',script,*args],env=env,capture_output=True)
            self.assertEqual(run('refresh','main').returncode,0)
            zone=(state/'zones').read_text().split()[0]
            self.assertEqual(run('prefetch','home').returncode,0)
            self.assertEqual(run('show-cached','home').returncode,0)
            self.assertEqual((state/'current').read_text().strip(),'local-cache')
            (state/'cache-screen').write_text('dinner\n')
            self.assertNotEqual(run('tap-cached',zone).returncode,0);self.assertEqual(calls,[])
            (state/'cache-screen').write_text('home\n')
            self.assertEqual(run('tap-cached',zone).returncode,0)
            self.assertEqual((state/'screen').read_text().strip(),'home')
            self.assertEqual(len(calls),1)

    def test_prefetch_requires_charging_and_offline_screens_only_offer_back(self):
        issued=[]
        frames=device_frames.DeviceFrames(lambda action:{'ok':True})
        def build(screen,page,eat):
            issued.append(screen)
            return PNG+screen.encode(),[{'box':[10,20,200,220],'action':{'kind':'fixture'}}]
        server=ThreadingHTTPServer(('127.0.0.1',0),device_http.make_handler(frames,build,TOKEN))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        with tempfile.TemporaryDirectory() as tmp:
            config=pathlib.Path(tmp)/'curl.conf';config.write_text('header = "Authorization: Bearer '+TOKEN+'"\n')
            env=dict(os.environ,IH_DEVICE_URL='http://127.0.0.1:'+str(server.server_port),IH_DEVICE_CURL_CONFIG=str(config),IH_DEVICE_DIR=tmp+'/state',IH_DEVICE_DRAW='/bin/true')
            script=str(APP/'device/ih-device.sh');state=pathlib.Path(tmp)/'state'
            subprocess.run(['sh',script,'refresh','main'],env=env,check=True,capture_output=True)
            original=(state/'current').read_text();original_zones=(state/'zones').read_text()
            result=subprocess.run(['sh',script,'prefetch'],env=env,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertEqual(issued,['main'])
            env['IH_DEVICE_CHARGING']='true'
            subprocess.run(['sh',script,'prefetch'],env=env,check=True,capture_output=True)
            self.assertEqual(issued,['main','dinner','home','birthdays'])
            self.assertEqual((state/'current').read_text(),original)
            server.shutdown()
            battery_env=dict(env,IH_DEVICE_CHARGING='false')
            self.assertNotEqual(subprocess.run(['sh',script,'show-cached','home'],env=battery_env,capture_output=True).returncode,0)
            for screen,back in (('dinner',('60','1250')),('home',('150','1020')),('birthdays',('60','1250'))):
                subprocess.run(['sh',script,'show-cached',screen],env=env,check=True,capture_output=True)
                self.assertEqual((state/'current').read_text().strip(),'local-cache')
                self.assertTrue((state/'zones').read_text().startswith('cache_back '))
                self.assertNotEqual(subprocess.run(['sh',script,'tap-xy','20','30'],env=env,capture_output=True).returncode,0)
                subprocess.run(['sh',script,'tap-xy',*back],env=env,check=True,capture_output=True)
                self.assertEqual((state/'current').read_text(),original)
                self.assertEqual((state/'zones').read_text(),original_zones)
            (state/'cache-home.png').write_bytes(b'tampered')
            self.assertNotEqual(subprocess.run(['sh',script,'show-cached','home'],env=env,capture_output=True).returncode,0)
            self.assertEqual((state/'current').read_text(),original)
            self.assertEqual(issued,['main','dinner','home','birthdays'])
class FakeHA:
    def optional_state(self,key):return None

class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.data={'device':{'charging':True},'panels':[],'tasks':[],'cooking':{'running':False}}
        self.cooking=FakeCooking()
        self.menu={'page':0,'pages':1,'revision':'a'*64,'items':[{'slug':'meal','revision':'b'*64,'eat':'18:45','lead':30,'title':'Meal'}]}
        self.surface=device_surface.DeviceSurface(lambda:self.data,lambda:{'configured':True,'complete':True,'alerts':[]},lambda page:self.menu,self.cooking,FakeActions(),lambda value:PNG,FakeHA(),lambda:{},clock=lambda:1000)
        self.surface.charging=lambda:True
    def test_dinner_rows_and_chip_navigation_are_generation_bound(self):
        image,zones=self.surface.build('dinner',0,'1830');self.assertEqual(image,PNG)
        start=next(x['action'] for x in zones if x['action']['kind']=='start_cook')
        self.assertEqual(start['eat'],'18:45')
        nav=next(x['action'] for x in zones if x['action'].get('eat')=='1230')
        self.assertEqual(self.surface.dispatch(nav)['navigate'],{'screen':'dinner','page':0,'eat':'1230'})
        self.assertEqual(len(start['request_id']),32)
    def test_cooking_uses_display_revision_and_item_id(self):
        self.data['cooking']={'running':True,'title':'Meal','revision':'a'*64,'next':[{'id':'b'*64,'text':'Step','at':'12:30'}],'prep':[],'steps':[]}
        _,zones=self.surface.build('main',0,'asap')
        done=next(z['action'] for z in zones if z['action'].get('action')=='done')
        self.assertTrue(self.surface.dispatch(done)['ok'])
        self.assertEqual(self.cooking.calls[0],('/cook/display-action',{'revision':'a'*64,'action':'done','item_id':'b'*64}))
        row=[z for z in zones if z['box'][1]==278]
        self.assertEqual([z['action']['action'] for z in row],['delay','early','ack','did','done'])
        self.assertTrue(all(z['power']=='battery' for z in row))
        self.assertEqual(row[-1]['box'],[48,278,1024,336])
    def test_menu_labels_match_navigation_boxes(self):
        import device_menu
        image=device_menu.svg();self.assertIn(b'COOK DINNER',image);self.assertIn(b'BIRTHDAYS',image)
        _,zones=self.surface.build('menu',0,'asap')
        self.assertTrue(any(z['box']==[140,300,932,416] and z['action'].get('screen')=='dinner' for z in zones))
        self.assertTrue(any(z['box']==[140,860,932,976] and z['action'].get('screen')=='wifi' for z in zones))
        self.assertTrue(all(z['power']=='mains' for z in zones))
        self.assertTrue(any(z['box']==[140,1000,932,1116] and z['action'].get('screen')=='birthdays' for z in zones))
    def test_only_visible_energy_alert_can_have_dismiss_zone(self):
        self.surface.alerts=lambda:{'configured':True,'complete':True,'alerts':[{'kind':'smoke','title':'Smoke','dismissible':False},{'kind':'free','title':'Free power','short':'Power','dismissible':True}]}
        _,zones=self.surface.build('main',0,'asap')
        self.assertEqual([z['action']['kind'] for z in zones if z['box']==[894,612,1047,664]],['alert'])
        self.surface.alerts=lambda:{'configured':True,'complete':True,'alerts':[{'kind':'smoke','title':'Smoke','dismissible':False}]}
        _,zones=self.surface.build('main',0,'asap')
        self.assertFalse(any(z['box']==[894,612,1047,664] for z in zones))
    def test_power_restriction_removes_popup_actions(self):
        self.data['device']['charging']=False
        _,zones=self.surface.build('dinner',0,'asap')
        self.assertEqual(zones,[])
        self.surface.charging=lambda:False
        self.assertFalse(self.surface.dispatch({'kind':'navigate','screen':'dinner','page':0,'eat':'asap'})['ok'])

    def test_missing_cooking_owner_disables_normal_touch_controls(self):
        self.data['cooking']=None
        captured=[];self.surface.png=lambda svg:captured.append(svg) or PNG
        _,zones=self.surface.build('main',0,'asap')
        self.assertEqual(zones,[])
        self.assertIn(b'Cooking unavailable',captured[0])

if __name__=='__main__':unittest.main()
