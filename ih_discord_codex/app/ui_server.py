"""Admin-only ingress UI and local health probe for this add-on."""
import json, os, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).with_name('ui'); INFO=json.loads((ROOT/'ui.json').read_text()); PEER='172.30.32.2'
ASSETS={'/':'index.html','/index.html':'index.html','/ui.css':'ui.css','/ui.js':'ui.js','/ih-ui.css':'ih-ui.css','/ih-ui.js':'ih-ui.js'}
class Handler(BaseHTTPRequestHandler):
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
  name=ASSETS.get(path)
  if not name:return self.send_error(404)
  try: body=(ROOT/name).read_bytes()
  except OSError:return self.send_error(404)
  typ='text/html; charset=utf-8' if name.endswith('.html') else 'text/css; charset=utf-8' if name.endswith('.css') else 'text/javascript; charset=utf-8'
  self.reply(200,body,typ)
 def reply(self,code,body,typ):
  self.send_response(code);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
if __name__=='__main__': ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('IH_UI_PORT','8812'))),Handler).serve_forever()
