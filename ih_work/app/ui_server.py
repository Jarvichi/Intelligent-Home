"""Admin-only ingress UI and local health probe for this add-on."""
import json, os, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
DATA=os.environ.get('IHW_DATA','/data')
ROOT=Path(__file__).with_name('ui'); INFO=json.loads((ROOT/'ui.json').read_text()); PEER='172.30.32.2'
ASSETS={'/workers.js':'workers.js','/workers.css':'workers.css','/runs.js':'runs.js','/runs.css':'runs.css','/':'index.html','/index.html':'index.html','/ui.css':'ui.css','/ui.js':'ui.js','/ih-ui.css':'ih-ui.css','/ih-ui.js':'ih-ui.js','/ih-patterns.css':'ih-patterns.css'}
RUN_POSTS={'/api/runs/settings':'/runs/settings','/api/runs/start':'/runs/start','/api/runs/stop':'/runs/stop'}
def api(method,path,body=None):
 """Call the add-on's own token API on this machine. The token is read from the options file and never sent to the page."""
 try:
  with open(os.path.join(DATA,'options.json')) as f: token=json.load(f).get('api_token') or ''
 except (OSError,ValueError): token=''
 base=os.environ.get('IHW_API','http://127.0.0.1:%s'%os.environ.get('IHW_PORT','8802'))
 req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=10) as r: return r.status,r.read()
 except urllib.error.HTTPError as e: return e.code,e.read()
 except Exception: return 502,b'{"error":"add-on API not reachable"}'
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  if self.client_address[0]!=PEER: return self.send_error(403)
  path=self.path.split('?',1)[0]
  if path not in RUN_POSTS: return self.send_error(404)
  if not self.headers.get('Content-Type','').startswith('application/json'): return self.send_error(415)
  try: n=int(self.headers.get('Content-Length') or 0)
  except ValueError: return self.send_error(400)
  if n>8192: return self.send_error(413)
  try: body=json.loads(self.rfile.read(n) or b'{}')
  except ValueError: return self.send_error(400)
  code,out=api('POST',RUN_POSTS[path],body); self.reply(code,out,'application/json; charset=utf-8')
 def do_GET(self):
  if self.client_address[0]!=PEER: return self.send_error(403)
  path=self.path.split('?',1)[0]
  if path=='/api/status':
   healthy=False; detail='Health check failed'
   try:
    req=urllib.request.Request('http://127.0.0.1:%d%s'%(INFO['health_port'],INFO['health_path']),headers={'Accept':'text/plain'})
    with urllib.request.urlopen(req,timeout=2) as response: healthy=200<=response.status<400; detail='Health endpoint responded'
   except Exception as e: detail=type(e).__name__
   body=json.dumps({'healthy':healthy,'detail':detail,'checked_at':time.time()}).encode(); return self.reply(200,body,'application/json; charset=utf-8')
  if path=='/api/runs':
   code,out=api('GET','/runs'); return self.reply(code,out,'application/json; charset=utf-8')
  if path=='/api/workers':
   code,out=api('GET','/workers'); return self.reply(code,out,'application/json; charset=utf-8')
  if path=='/api/today':
   try:
    import today
    data=today.today()
   except Exception: data=None
   if data is None: return self.send_error(404)
   return self.reply(200,json.dumps(data).encode(),'application/json; charset=utf-8')
  if path=='/ui.json':
   body=json.dumps({k:INFO.get(k) for k in ('title','purpose','description','navigation','access')}).encode(); return self.reply(200,body,'application/json; charset=utf-8')
  name=ASSETS.get(path)
  if not name:return self.send_error(404)
  try: body=(ROOT/name).read_bytes()
  except OSError:return self.send_error(404)
  typ='text/html; charset=utf-8' if name.endswith('.html') else 'text/css; charset=utf-8' if name.endswith('.css') else 'text/javascript; charset=utf-8'
  self.reply(200,body,typ)
 def reply(self,code,body,typ):
  self.send_response(code);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
if __name__=='__main__': ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('IH_UI_PORT','8811'))),Handler).serve_forever()
