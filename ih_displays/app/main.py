"""Admin ingress plus a separate read-only bearer API for display adapters."""
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import profiles
import messages
import dishwasher

ROOT = Path(__file__).parent / 'ui'
ASSETS = {'/': ('index.html','text/html'), '/ih-ui.css': ('ih-ui.css','text/css'), '/ih-ui.js': ('ih-ui.js','text/javascript'), '/ui.css': ('ui.css','text/css'), '/ui.js': ('ui.js','text/javascript'), '/ui.json': ('ui.json','application/json')}
PEER = '172.30.32.2'

def make_handler(db_path, token, ingress=False, peer=PEER, message_feed=None, task_feed=None):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, data, kind='application/json'):
            body = data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status); self.send_header('Content-Type',kind+'; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(body)
        def run(self, method):
            path=urlparse(self.path).path
            if ingress:
                if self.client_address[0] != peer: return self.reply(403,{'error':'Forbidden.'})
                if method=='GET' and path in ASSETS:
                    filename,kind=ASSETS[path]
                    return self.reply(200,(ROOT/filename).read_bytes(),kind)
                if not path.startswith('/api/'): return self.reply(404,{'error':'Not found.'})
                path=path[4:]
            else:
                if path=='/healthz' and method=='GET': return self.reply(200,{'ok':True})
                if not token or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token): return self.reply(401,{'error':'Unauthorised.'})
                if method!='GET': return self.reply(405,{'error':'Read-only API.'})
            body=None
            if method in ('POST','PUT','DELETE'):
                try:
                    if self.headers.get('Content-Type','').split(';',1)[0].strip().lower()!='application/json': raise ValueError()
                    n=int(self.headers.get('Content-Length','0'))
                    if not 0<n<=16384: raise ValueError()
                    body=json.loads(self.rfile.read(n))
                    if not isinstance(body,dict): raise ValueError()
                except (ValueError,UnicodeError): return self.reply(400,{'error':'Invalid request.'})
            if path=='/v1/message-tasks' and method=='GET':return self.reply(200,message_feed.read() if message_feed else {'configured':False,'complete':True,'items':[]})
            if path=='/v1/tasks' and method=='GET':return self.reply(200,task_feed.read() if task_feed else {'configured':False,'complete':True,'items':[]})
            db=profiles.connect(db_path)
            try:
                if path=='/v1/profiles' and method=='GET': return self.reply(200,{'profiles':profiles.listing(db)})
                if path=='/v1/profiles' and method=='POST' and ingress: return self.reply(201,profiles.create(db,body))
                if path.startswith('/v1/profiles/'):
                    ident=path[len('/v1/profiles/'):]
                    if method=='GET': return self.reply(200,profiles.get(db,ident))
                    if method=='PUT' and ingress:
                        if set(body)!={'profile','revision'}: raise profiles.Invalid('Invalid request.')
                        return self.reply(200,profiles.update(db,ident,body['profile'],body['revision']))
                    if method=='DELETE' and ingress:
                        if set(body)!={'revision'}: raise profiles.Invalid('Invalid request.')
                        profiles.delete(db,ident,body['revision']); return self.reply(200,{'ok':True})
                return self.reply(404,{'error':'Not found.'})
            except profiles.Missing: return self.reply(404,{'error':'Not found.'})
            except profiles.Conflict as e: return self.reply(409,{'error':str(e)})
            except profiles.Invalid as e: return self.reply(400,{'error':str(e)})
            finally: db.close()
        def do_GET(self): self.run('GET')
        def do_POST(self): self.run('POST')
        def do_PUT(self): self.run('PUT')
        def do_DELETE(self): self.run('DELETE')
        def log_message(self,*args): pass
    return Handler

def main():
    data=Path(os.environ.get('IHA_DATA','/data')); data.mkdir(parents=True,exist_ok=True)
    try: opts=json.loads((data/'options.json').read_text())
    except (OSError,ValueError): opts={}
    db_path=str(data/'displays.db'); db=profiles.connect(db_path); db.close()
    feed=messages.Messages(opts.get('message_entities',[]),os.environ.get('SUPERVISOR_TOKEN',''))
    dish=dishwasher.Dishwasher(opts.get('dishwasher_pending_entity',''),opts.get('dishwasher_finished_entity',''),os.environ.get('SUPERVISOR_TOKEN',''))
    tasks=dishwasher.Combined(feed,dish)
    ingress=ThreadingHTTPServer(('0.0.0.0',8827),make_handler(db_path,opts.get('api_token') or '',True,message_feed=feed,task_feed=tasks))
    threading.Thread(target=ingress.serve_forever,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',8805),make_handler(db_path,opts.get('api_token') or '',message_feed=feed,task_feed=tasks)).serve_forever()

if __name__=='__main__': main()
