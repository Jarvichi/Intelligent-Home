"""Administrator-only wall image preview and an independent health endpoint."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler,HTTPServer,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import frames
import profile_client
import render

ROOT=Path(__file__).parent/'ui'
PEER='172.30.32.2'
ASSETS={'/':('index.html','text/html'),'/ih-ui.css':('ih-ui.css','text/css'),'/ih-ui.js':('ih-ui.js','text/javascript'),'/ui.css':('ui.css','text/css'),'/ui.js':('ui.js','text/javascript')}

def make_handler(store,ingress=False,peer=PEER,profile=None,render_data=None):
    class Handler(BaseHTTPRequestHandler):
        def reply(self,code,body,kind='application/json'):
            raw=body if isinstance(body,bytes) else json.dumps(body).encode()
            self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def run(self,method):
            path=urlparse(self.path).path
            if not ingress:
                if path=='/healthz' and method=='GET':return self.reply(200,{'ok':True})
                return self.reply(404,{'error':'Not found.'})
            if self.client_address[0]!=peer:return self.reply(403,{'error':'Forbidden.'})
            if method=='GET' and path in ASSETS:
                name,kind=ASSETS[path];return self.reply(200,(ROOT/name).read_bytes(),kind)
            if method=='GET' and path=='/api/status':return self.reply(200,store.status())
            if method=='GET' and path=='/api/profile':return self.reply(200,profile.read() if profile else {'state':'not_configured'})
            if method=='GET' and path=='/api/render.svg':
                if not profile or not render_data:return self.reply(503,{'error':'Renderer unavailable.'})
                p=profile.read()
                if p.get('state')!='ready':return self.reply(503,{'error':'Display profile unavailable.'})
                try:return self.reply(200,render.svg(p,render_data()),'image/svg+xml')
                except ValueError:return self.reply(503,{'error':'Display profile unavailable.'})
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
    def snapshot():return ha.snapshot(opts.get('render_entities',[]),opts.get('render_todo_entity',''))
    ingress=ThreadingHTTPServer(('0.0.0.0',8828),make_handler(store,True,profile=profile,render_data=snapshot))
    threading.Thread(target=ingress.serve_forever,daemon=True).start()
    HTTPServer(('0.0.0.0',8806),make_handler(store)).serve_forever()

if __name__=='__main__':main()
