"""Admin-only ingress UI and local health probe for this add-on."""
import json, os, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).with_name('ui'); INFO=json.loads((ROOT/'ui.json').read_text()); PEER='172.30.32.2'
ASSETS={'/ih-patterns.css':'ih-patterns.css','/':'index.html','/index.html':'index.html','/ui.css':'ui.css','/ui.js':'ui.js','/jobs-logic.js':'jobs-logic.js','/ih-ui.css':'ih-ui.css','/ih-ui.js':'ih-ui.js'}

SCHEDULE_KINDS=('daily','every','service','event')
def clean_schedule(value):
 """Keep only the plain schedule fields the page shows."""
 if not isinstance(value,dict) or value.get('kind') not in SCHEDULE_KINDS: return None
 out={'kind':value['kind']}
 at=value.get('at')
 if isinstance(at,str) and len(at)<=5: out['at']=at
 for key in ('minutes','seconds'):
  n=value.get(key)
  if isinstance(n,int) and not isinstance(n,bool) and 0<n<=10**6: out[key]=n
 return out
def clean_children(value):
 """Child add-ons as /status already reports them: name, mode, running, version. Nothing else is passed on."""
 out=[]
 for key,c in (value or {}).items() if isinstance(value,dict) else []:
  if not isinstance(c,dict): continue
  seen=c.get('seen_s_ago')
  out.append({'key':str(key)[:40],'title':str(c.get('title') or '')[:80],'version':str(c.get('version') or '')[:20],
   'mode':'live' if c.get('mode')=='live' else 'dry','up':c.get('up') is True,'healthy':c.get('healthy') is True,
   'seen_s_ago':seen if isinstance(seen,(int,float)) and not isinstance(seen,bool) else None})
 return out
def jobs_payload(data,now):
 """Read-only job list for the page. Same rows as before, plus each job's schedule and the child add-ons."""
 rows=[]
 for kind in ('jobs','plugins'):
  for name,item in (data.get(kind) or {}).items():
   if not isinstance(item,dict): continue
   row={key:item.get(key) for key in ('mode','runs','errors','last_ok','last_run','last_run_s','next_run','last_mode','last_error','why')}
   row.update(name=name,kind='Plugin' if kind=='plugins' else 'Built-in job',schedule=clean_schedule(item.get('schedule')))
   rows.append(row)
 return {'available':True,'checked_at':now,'jobs':rows,'children':clean_children(data.get('children'))}
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
  if path=='/api/jobs':
   try:
    req=urllib.request.Request('http://127.0.0.1:%d/status'%INFO['health_port'],headers={'Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=3) as response:
     raw=response.read(1024*1024+1)
    if len(raw)>1024*1024: raise ValueError('status too large')
    body=jobs_payload(json.loads(raw),time.time())
    return self.reply(200,json.dumps(body).encode(),'application/json; charset=utf-8')
   except Exception:
    return self.reply(503,b'{"available":false,"error":"Job results are temporarily unavailable."}','application/json; charset=utf-8')
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
