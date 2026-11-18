"""Read-only administrator preview of the existing Kindle picture."""
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import frames
import render
import kindle_render
import source_settings
import profile_client
import device_state

ROOT=Path(__file__).parent/'ui'
PEER='172.30.32.2'
ASSETS={'/':('index.html','text/html'),'/ih-ui.css':('ih-ui.css','text/css'),'/ih-ui.js':('ih-ui.js','text/javascript'),'/ui.css':('ui.css','text/css'),'/ui.js':('ui.js','text/javascript')}

def png(data):
    result=subprocess.run(['rsvg-convert','--format=png'],input=data,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,check=True)
    if len(result.stdout)>8*1024*1024 or not result.stdout.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Invalid image')
    return result.stdout

def make_handler(store,ingress=False,peer=PEER,sources=None,snapshot=None,alerts=None,png_encoder=png):
    class Handler(BaseHTTPRequestHandler):
        def reply(self,code,body,kind='application/json'):
            raw=body if isinstance(body,bytes) else json.dumps(body).encode()
            self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def run(self,method):
            path=urlparse(self.path).path
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
                try:return self.reply(200,png_encoder(kindle_render.svg(snapshot(),alerts() if alerts else {'configured':False,'complete':False,'alerts':[]})),'image/png')
                except (OSError,ValueError,TypeError,subprocess.SubprocessError):return self.reply(503,{'error':'Preview unavailable.'})
            if method=='GET' and path=='/api/status':return self.reply(200,store.status())
            if method=='POST' and path=='/api/refresh':return self.reply(200,store.refresh())
            if method=='GET' and path=='/api/frame':
                data,kind=store.frame()
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
    store=frames.Frames(opts.get('source_url',''))
    data=Path(os.environ.get('IHA_DATA','/data'));sources=source_settings.SourceSettings(data/'kindle-source-overrides.json')
    ha=render.HA(os.environ.get('SUPERVISOR_TOKEN',''))
    shared=profile_client.ProfileClient(opts.get('displays_url',''),opts.get('displays_token',''),'')
    def snapshot():
        cfg=sources.read()
        result=ha.snapshot(cfg.get('render_entities',[]),'','',shared.message_tasks if opts.get('render_shared_messages') is True else None,cfg.get('weather_forecast_entity',''),cfg.get('weather_forecast_fallback_entity',''),cfg.get('render_media_entities',[]),cfg.get('render_overlay_entities',[]),cfg.get('render_cooking_entity',''),{'alert':cfg.get('render_intrusion_alert',''),'log':cfg.get('render_intrusion_log',''),'presence':cfg.get('render_intrusion_presence',[])},{'boiler':cfg.get('render_heat_boiler',''),'thermostat':cfg.get('render_heat_thermostat',''),'floors':cfg.get('render_heat_floors',[])},cfg.get('render_doors',[]),cfg.get('render_energy',{}),cfg.get('render_alarm',''),cfg.get('render_presence',[]),cfg.get('render_lights',[]),cfg.get('render_game_entities',[]),cfg.get('weather_current_entity',''),None,cfg.get('render_rooms',[]),cfg.get('render_sun_entity',''),'light',{'birthdays':cfg.get('render_birthdays_entity',''),'halloween':cfg.get('render_halloween_entity',''),'fixed':cfg.get('render_birthdays_fixed',[])})
        result['device']=device_state.read(ha,cfg.get('kindle_battery_entity',''),cfg.get('kindle_last_seen_entity',''),cfg.get('kindle_page_entity',''))
        return result
    alert_reader=shared.alerts if opts.get('render_shared_alerts') is True else None
    ingress=ThreadingHTTPServer(('0.0.0.0',8840),make_handler(store,True,sources=sources,snapshot=snapshot,alerts=alert_reader))
    threading.Thread(target=ingress.serve_forever,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',8841),make_handler(store)).serve_forever()

if __name__=='__main__':main()
