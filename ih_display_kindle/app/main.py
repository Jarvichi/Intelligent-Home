"""Read-only administrator preview of the existing Kindle picture."""
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs,urlsplit,urlunsplit
import frames
import render
import kindle_render
import kindle_html
import source_settings
import profile_client
import device_state
import cooking_client
import popup_render
import device_frames
import device_http
import device_surface
import kindle_actions

ROOT=Path(__file__).parent/'ui'
PEER='172.30.32.2'
ASSETS={'/':('index.html','text/html'),'/ih-ui.css':('ih-ui.css','text/css'),'/ih-ui.js':('ih-ui.js','text/javascript'),'/ui.css':('ui.css','text/css'),'/ui.js':('ui.js','text/javascript')}

def png(data):
    result=subprocess.run(['rsvg-convert','--format=png'],input=data,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,check=True)
    if len(result.stdout)>8*1024*1024 or not result.stdout.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Invalid image')
    return result.stdout

def make_handler(store,ingress=False,peer=PEER,sources=None,snapshot=None,alerts=None,png_encoder=png,variants=None,menu=None,html=None):
    def main_picture(data,alert_data):
        if html:
            value=html.png(data,alert_data)
            if value:return value
        return kindle_render.svg(data,alert_data)
    def popup(name,payload):
        if html:
            value=html.screen(name,payload,None)
            if value:return value
        return popup_render.svg(name,payload)
    class Handler(BaseHTTPRequestHandler):
        def reply(self,code,body,kind='application/json'):
            raw=body if isinstance(body,bytes) else json.dumps(body).encode()
            self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def run(self,method):
            parsed=urlparse(self.path);path=parsed.path;query=parse_qs(parsed.query);screen=(query.get('screen') or ['main'])[0]
            if not ingress:
                if method=='GET' and path=='/healthz':return self.reply(200,{'ok':True})
                return self.reply(404,{'error':'Not found.'})
            if self.client_address[0]!=peer:return self.reply(403,{'error':'Forbidden.'})
            if method=='GET' and path in ASSETS:
                name,kind=ASSETS[path];return self.reply(200,(ROOT/name).read_bytes(),kind)
            if method=='POST' and path=='/api/sources/import':
                if not sources:return self.reply(503,{'error':'Source import unavailable.'})
                try:
                    if self.headers.get('Content-Type','').split(';',1)[0].strip().lower()!='application/json':raise ValueError()
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=65536:raise ValueError()
                    value=json.loads(self.rfile.read(size));sources.save(value)
                    return self.reply(200,{'ok':True,'updated':len(value)})
                except (OSError,ValueError,TypeError,UnicodeError):return self.reply(400,{'error':'Invalid source mappings.'})
            if method=='GET' and path=='/api/render.png':
                if not snapshot:return self.reply(503,{'error':'Preview unavailable.'})
                if screen not in ('main','dinner','home','birthdays'):return self.reply(400,{'error':'Invalid preview screen.'})
                try:
                    if screen=='dinner':
                        page=int((query.get('page') or ['0'])[0]);picture=popup('dinner',menu(page) if menu else None)
                    else:
                        data=snapshot()
                        if screen=='home':picture=popup('home',next((x for x in data.get('panels',[]) if x.get('label')=='Home'),None))
                        elif screen=='birthdays':picture=popup('birthdays',data.get('birthdays'))
                        else:picture=main_picture(data,alerts() if alerts else {'configured':False,'complete':False,'alerts':[]})
                    return self.reply(200,picture if picture[:8]==b'\x89PNG\r\n\x1a\n' else png_encoder(picture),'image/png')
                except (OSError,ValueError,TypeError,subprocess.SubprocessError):return self.reply(503,{'error':'Preview unavailable.'})
            selected=store if screen=='main' else (variants or {}).get(screen)
            if method=='GET' and path=='/api/status':return self.reply(200,selected.status() if selected else {'state':'not_configured','has_frame':False})
            if method=='POST' and path=='/api/refresh':return self.reply(200,selected.refresh() if selected else {'state':'not_configured','has_frame':False})
            if method=='GET' and path=='/api/frame':
                if not selected:return self.reply(503,{'error':'Picture unavailable.'})
                data,kind=selected.frame()
                if data is None:return self.reply(503,{'error':'Picture unavailable.'})
                return self.reply(200,data,kind)
            return self.reply(404,{'error':'Not found.'})
        def do_GET(self):self.run('GET')
        def do_POST(self):self.run('POST')
        def log_message(self,*args):pass
    return Handler

def main():
    try:opts=json.loads((Path(os.environ.get('IHA_DATA','/data'))/'options.json').read_text())
    except (OSError,ValueError):opts={}
    source=opts.get('source_url','');store=frames.Frames(source);variants={}
    if frames.valid_url(source):
        parts=urlsplit(source)
        if parts.path in ('','/','/1') and not parts.query:
            for name,path in (('dinner','/3'),('home','/4'),('birthdays','/5')):
                variants[name]=frames.Frames(urlunsplit((parts.scheme,parts.netloc,path,'','')))
    data=Path(os.environ.get('IHA_DATA','/data'));sources=source_settings.SourceSettings(data/'kindle-source-overrides.json')
    ha=render.HA(os.environ.get('SUPERVISOR_TOKEN',''))
    cooking=cooking_client.CookingClient(opts.get('cooking_url',''),opts.get('cooking_token',''))
    shared=profile_client.ProfileClient(opts.get('displays_url',''),opts.get('displays_token',''),'')
    def snapshot():
        cfg=sources.read()
        result=ha.snapshot(cfg.get('render_entities',[]),'','',shared.message_tasks if opts.get('render_shared_messages') is True else None,cfg.get('weather_forecast_entity',''),cfg.get('weather_forecast_fallback_entity',''),cfg.get('render_media_entities',[]),cfg.get('render_overlay_entities',[]),cfg.get('render_cooking_entity',''),{'alert':cfg.get('render_intrusion_alert',''),'log':cfg.get('render_intrusion_log',''),'presence':cfg.get('render_intrusion_presence',[])},{'boiler':cfg.get('render_heat_boiler',''),'thermostat':cfg.get('render_heat_thermostat',''),'floors':cfg.get('render_heat_floors',[])},cfg.get('render_doors',[]),cfg.get('render_energy',{}),cfg.get('render_alarm',''),cfg.get('render_presence',[]),cfg.get('render_lights',[]),cfg.get('render_game_entities',[]),cfg.get('weather_current_entity',''),None,cfg.get('render_rooms',[]),cfg.get('render_sun_entity',''),'light',{'birthdays':cfg.get('render_birthdays_entity',''),'halloween':cfg.get('render_halloween_entity',''),'fixed':cfg.get('render_birthdays_fixed',[])})
        result['device']=device_state.read(ha,cfg.get('kindle_battery_entity',''),cfg.get('kindle_last_seen_entity',''),cfg.get('kindle_page_entity',''))
        result['cooking']=cooking.read()
        halloween=ha.optional_state(cfg.get('render_halloween_entity','')) if cfg.get('render_halloween_entity') else None
        result['halloween_mode']=halloween.get('state') if isinstance(halloween,dict) and halloween.get('state') in ('on','off') else None
        result['birthdays']=device_state.birthdays(ha,cfg.get('kindle_birthdays_entity',''))
        presence=next((p for p in result.get('panels',[]) if p.get('label')=='Home'),None)
        times=ha.optional_state(cfg.get('kindle_presence_times_entity','')) if cfg.get('kindle_presence_times_entity') else None
        attrs=times.get('attributes',{}) if isinstance(times,dict) and isinstance(times.get('attributes'),dict) else {}
        if presence:
            for person in presence.get('members',[]):
                key=person.get('presence_key','');person['transition']=attrs.get(key+('_arrived' if person.get('home') is True else '_left')) if key else None
        return result
    html=None
    if opts.get('renderer')=='html':   # every screen as an HTML page; anything it cannot draw falls back to the SVG
        mine=Path('/config/templates')   # the add-on's own config folder: your template files here are used before the built-in ones
        kindle_html.configure(mine if mine.is_dir() else None)
        try:
            reference=mine/'default';reference.mkdir(parents=True,exist_ok=True)
            for source in kindle_html.BUILTIN.iterdir():(reference/source.name).write_bytes(source.read_bytes())   # a fresh copy to start from
        except OSError:pass
        html=kindle_html.Renderer()
    alert_reader=shared.alerts if opts.get('render_shared_alerts') is True else None
    ingress=ThreadingHTTPServer(('0.0.0.0',8840),make_handler(store,True,sources=sources,snapshot=snapshot,alerts=alert_reader,variants=variants,menu=cooking.menu,html=html))
    threading.Thread(target=ingress.serve_forever,daemon=True).start()
    if opts.get('device_enabled') is True:
        # The device client obtains a new owner snapshot after this action.
        actions=kindle_actions.KindleActions(ha,sources.read,alerts=alert_reader,refresh=lambda:None)
        surface=device_surface.DeviceSurface(snapshot,alert_reader or (lambda:None),cooking.menu,cooking,actions,png,ha,sources.read,html=html)
        device_store=device_frames.DeviceFrames(surface.dispatch)
        handler=device_http.make_handler(device_store,surface.build,opts.get('device_token',''))
        class DeviceHandler(handler):
            def do_GET(self):
                if self.path=='/healthz':return self.send(200,'{"ok":true}','application/json')
                return super().do_GET()
        ThreadingHTTPServer(('0.0.0.0',8841),DeviceHandler).serve_forever()
    else:ThreadingHTTPServer(('0.0.0.0',8841),make_handler(store)).serve_forever()

if __name__=='__main__':main()
