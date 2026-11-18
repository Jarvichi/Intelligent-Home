"""Administrator-only wall image preview and an independent health endpoint."""
import json
import hmac
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler,HTTPServer,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import frames
import cooking_client
import profile_client
import render
import wall_tv
import source_settings

ROOT=Path(__file__).parent/'ui'
PEER='172.30.32.2'
ASSETS={'/':('index.html','text/html'),'/ih-ui.css':('ih-ui.css','text/css'),'/ih-ui.js':('ih-ui.js','text/javascript'),'/ui.css':('ui.css','text/css'),'/ui.js':('ui.js','text/javascript')}

def png(svg):
    result=subprocess.run(['rsvg-convert','--format=png'],input=svg,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,check=True)
    if len(result.stdout)>8*1024*1024 or not result.stdout.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Invalid image')
    return result.stdout

def make_handler(store,ingress=False,peer=PEER,profile=None,render_data=None,alert_preview=False,image_token='',png_encoder=png,allow_url_token=False,sources=None):
    image_token=image_token if isinstance(image_token,str) else ''
    class Handler(BaseHTTPRequestHandler):
        def reply(self,code,body,kind='application/json'):
            raw=body if isinstance(body,bytes) else json.dumps(body).encode()
            self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def run(self,method):
            path=urlparse(self.path).path
            if not ingress:
                if path=='/healthz' and method=='GET':return self.reply(200,{'ok':True})
                match=re.fullmatch(r'/v1/render/([A-Za-z0-9_-]{32,128})\.png',path) if allow_url_token else None
                if (path=='/v1/render.png' or match) and method=='GET':
                    bearer=bool(image_token) and hmac.compare_digest(self.headers.get('Authorization','').encode('utf-8'),('Bearer '+image_token).encode('utf-8'))
                    capability=bool(match and re.fullmatch(r'[A-Za-z0-9_-]{32,128}',image_token) and hmac.compare_digest(match.group(1),image_token))
                    if not bearer and not capability:return self.reply(401,{'error':'Unauthorised.'})
                    if not profile or not render_data:return self.reply(503,{'error':'Renderer unavailable.'})
                    try:
                        p=profile.read()
                        if p.get('state')!='ready' or any(type(p.get(k)) is not int or not 1<=p[k]<=4096 for k in ('width','height')):raise ValueError()
                        warnings=(profile.alerts() or {'configured':True,'complete':False,'alerts':[]}) if alert_preview else None
                        return self.reply(200,png_encoder(wall_tv.svg(p,render_data(),warnings)),'image/png')
                    except (OSError,ValueError,subprocess.SubprocessError):return self.reply(503,{'error':'Renderer unavailable.'})
                if path=='/v1/frame' and method=='GET':
                    if not image_token or not hmac.compare_digest(self.headers.get('Authorization','').encode('utf-8'),('Bearer '+image_token).encode('utf-8')):return self.reply(401,{'error':'Unauthorised.'})
                    status=store.refresh();data,kind=store.frame()
                    if data is None:return self.reply(503,{'error':'Image unavailable.'})
                    self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-IH-Frame-Stale','true' if status.get('stale') else 'false');self.end_headers();self.wfile.write(data);return
                return self.reply(404,{'error':'Not found.'})
            if self.client_address[0]!=peer:return self.reply(403,{'error':'Forbidden.'})
            if method=='GET' and path in ASSETS:
                name,kind=ASSETS[path];return self.reply(200,(ROOT/name).read_bytes(),kind)
            if method=='GET' and path=='/api/status':return self.reply(200,store.status())
            if method=='POST' and path=='/api/sources/import':
                if not sources:return self.reply(503,{'error':'Source import unavailable.'})
                try:
                    if self.headers.get('Content-Type','').split(';',1)[0].strip().lower()!='application/json':raise ValueError()
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=65536:raise ValueError()
                    values=json.loads(self.rfile.read(size));sources.save(values)
                    return self.reply(200,{'ok':True,'updated':len(values)})
                except (OSError,ValueError,TypeError,UnicodeError):return self.reply(400,{'error':'Invalid source mappings.'})
            if method=='GET' and path=='/api/profile':return self.reply(200,profile.read() if profile else {'state':'not_configured'})
            if method=='GET' and path=='/api/diagnostics':
                if not profile or not render_data:return self.reply(503,{'error':'Renderer unavailable.'})
                try:
                    data=render_data();warnings=profile.alerts() if alert_preview else None
                    return self.reply(200,{'weather':'ready' if data.get('weather') else ('unavailable' if data.get('weather_configured') else 'not_configured'),
                        'tasks':'unavailable' if data.get('tasks') is None and data.get('tasks_configured') else 'partial' if data.get('tasks_partial') else 'ready' if data.get('tasks_configured') else 'not_configured',
                        'alerts':'not_configured' if not alert_preview else 'unavailable' if not warnings else 'not_configured' if not warnings.get('configured') else 'ready' if warnings.get('complete') else 'partial',
                        'panel_sources':len(data.get('panels') or []),'unavailable_panels':sum(not p.get('available') for p in data.get('panels') or [])})
                except (OSError,ValueError,TypeError):return self.reply(503,{'error':'Diagnostics unavailable.'})
            if method=='GET' and path in ('/api/render.svg','/api/render.png'):
                if not profile or not render_data:return self.reply(503,{'error':'Renderer unavailable.'})
                p=profile.read()
                if p.get('state')!='ready' or any(type(p.get(k)) is not int or not 1<=p[k]<=4096 for k in ('width','height')):return self.reply(503,{'error':'Display profile unavailable.'})
                try:
                    warnings=(profile.alerts() or {'configured':True,'complete':False,'alerts':[]}) if alert_preview else None
                    picture=wall_tv.svg(p,render_data(),warnings)
                    return self.reply(200,png_encoder(picture) if path.endswith('.png') else picture,'image/png' if path.endswith('.png') else 'image/svg+xml')
                except (OSError,ValueError,subprocess.SubprocessError):return self.reply(503,{'error':'Renderer unavailable.'})
            if method=='POST' and path=='/api/refresh':return self.reply(200,store.refresh())
            if method=='GET' and path=='/api/frame':
                data,kind=store.frame()
                if data is None:return self.reply(404,{'error':'No picture available.'})
                return self.reply(200,data,kind)
            return self.reply(404,{'error':'Not found.'})
        def do_GET(self):self.run('GET')
        def do_POST(self):self.run('POST')
        def log_message(self,*args):pass
    return Handler

def main():
    data=Path(os.environ.get('IHA_DATA','/data'))
    try:opts=json.loads((data/'options.json').read_text())
    except (OSError,ValueError):opts={}
    store=frames.Frames(opts.get('source_url',''))
    profile=profile_client.ProfileClient(opts.get('displays_url',''),opts.get('displays_token',''),opts.get('profile_id',''))
    ha=render.HA(os.environ.get('SUPERVISOR_TOKEN',''))
    cooking=cooking_client.CookingClient(opts.get('cooking_url',''),opts.get('cooking_token',''))
    sources=source_settings.SourceSettings(data/'wall-source-overrides.json')
    def snapshot():
        cfg=dict(opts);updates=sources.read();energy=dict(cfg.get('render_energy') or {});energy.update(updates.get('render_energy') or {});cfg.update(updates);cfg['render_energy']=energy
        return ha.snapshot(cfg.get('render_entities',[]),cfg.get('render_todo_entity',''),cfg.get('render_task_feed_entity',''),profile.message_tasks if cfg.get('render_shared_messages') is True else None,cfg.get('weather_forecast_entity',''),cfg.get('weather_forecast_fallback_entity',''),cfg.get('render_media_entities',[]),cfg.get('render_overlay_entities',[]),cfg.get('render_cooking_entity',''),{'alert':cfg.get('render_intrusion_alert',''),'log':cfg.get('render_intrusion_log',''),'presence':cfg.get('render_intrusion_presence',[])},{'boiler':cfg.get('render_heat_boiler',''),'thermostat':cfg.get('render_heat_thermostat',''),'floors':cfg.get('render_heat_floors',[])},cfg.get('render_doors',[]),cfg.get('render_energy',{}),cfg.get('render_alarm',''),cfg.get('render_presence',[]),cfg.get('render_lights',[]),cfg.get('render_game_entities',[]),cfg.get('weather_current_entity',''),cooking.read if cfg.get('cooking_url') or cfg.get('cooking_token') else None,cfg.get('render_rooms',[]),cfg.get('render_sun_entity',''),cfg.get('render_night_mode','auto'),{'birthdays':cfg.get('render_birthdays_entity',''),'halloween':cfg.get('render_halloween_entity',''),'fixed':cfg.get('render_birthdays_fixed',[])},cfg.get('render_background_path',''))
    ingress=ThreadingHTTPServer(('0.0.0.0',8828),make_handler(store,True,profile=profile,render_data=snapshot,alert_preview=opts.get('render_shared_alerts') is True,sources=sources))
    threading.Thread(target=ingress.serve_forever,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',8806),make_handler(store,profile=profile,render_data=snapshot,alert_preview=opts.get('render_shared_alerts') is True,image_token=opts.get('image_api_token',''),allow_url_token=opts.get('image_url_access') is True)).serve_forever()

if __name__=='__main__':main()
