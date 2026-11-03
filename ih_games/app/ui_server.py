"""Admin-only ingress UI and local health probe for this add-on."""
import json, os, re, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).with_name('ui'); INFO=json.loads((ROOT/'ui.json').read_text()); PEER='172.30.32.2'
OPTIONS=Path(os.environ.get('IHG_OPTIONS','/data/options.json'))
API_PORT=int(os.environ.get('IHG_PORT','8794'))
DATA=Path(os.environ.get('IHG_DATA','/data'))
SUPERVISOR_TOKEN=os.environ.get('SUPERVISOR_TOKEN','').strip()
ASSETS={'/':'index.html','/index.html':'index.html','/ui.css':'ui.css','/ui.js':'ui.js','/ih-ui.css':'ih-ui.css','/ih-ui.js':'ih-ui.js'}
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.client_address[0]!=PEER: return self.send_error(403)
  path=self.path.split('?',1)[0]
  if path.startswith('/api/') and path!='/api/status':
   return self.game_api(path)
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
 def do_POST(self):
  if self.client_address[0]!=PEER: return self.send_error(403)
  path=self.path.split('?',1)[0]
  if not re.fullmatch(r'/api/games/[A-Za-z0-9_-]{1,120}/art',path):
   return self.send_error(404)
  return self.game_api(path,'POST')
 def game_api(self,path,method='GET'):
  try:
   opts=json.loads(OPTIONS.read_text())
  except (OSError,ValueError):
   return self.reply(503,json.dumps({'ok':False,'error':'Game data is not available yet.'}).encode(),'application/json; charset=utf-8')
  players=opts.get('players') or []
  query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
  art_match=re.fullmatch(r'/api/art/([A-Za-z0-9_-]{1,120})',path)
  art_meta_match=re.fullmatch(r'/api/art-meta/([A-Za-z0-9_-]{1,120})',path)
  if art_meta_match and method=='GET':
   gid=art_meta_match.group(1); meta_path=DATA/'artwork'/(gid+'.json')
   try:
    meta=json.loads(meta_path.read_text()); name=meta.get('file','')
    if (meta.get('game_id')!=gid or name!=os.path.basename(name) or not name.startswith(gid+'.') or
        not re.fullmatch(r'[A-Za-z0-9_-]+\.(?:jpg|png|webp)',name) or
        meta.get('content_type') not in ('image/jpeg','image/png','image/webp')): raise ValueError()
    image_path=DATA/'artwork'/name
    size=image_path.stat().st_size
    if size<1 or size>6*1024*1024: raise ValueError()
    return self.reply(200,json.dumps({'ok':True,'found':True,'artwork':meta}).encode(),'application/json; charset=utf-8')
   except (OSError,ValueError,TypeError):
    return self.reply(200,b'{"ok":true,"found":false}','application/json; charset=utf-8')
  if art_match and method=='GET':
   gid=art_match.group(1); meta_path=DATA/'artwork'/(gid+'.json')
   try:
    meta=json.loads(meta_path.read_text()); name=meta.get('file','')
    if (meta.get('game_id')!=gid or name!=os.path.basename(name) or not name.startswith(gid+'.') or
        not re.fullmatch(r'[A-Za-z0-9_-]+\.(?:jpg|png|webp)',name) or
        meta.get('content_type') not in ('image/jpeg','image/png','image/webp')): raise ValueError()
    data=(DATA/'artwork'/name).read_bytes()
    if not data or len(data)>6*1024*1024: raise ValueError()
    return self.reply(200,data,meta['content_type'])
   except (OSError,ValueError,TypeError):
    return self.reply(404,b'{"ok":false,"error":"No saved cover art."}','application/json; charset=utf-8')
  if path=='/api/players':
   rows=[{'key':str(p.get('key') or ''),'name':str(p.get('display_name') or p.get('key') or 'Player')} for p in players if p.get('key')]
   return self.reply(200,json.dumps({'ok':True,'players':rows,'art_search_available':bool(opts.get('ai_layer_art_token'))}).encode(),'application/json; charset=utf-8')
  if path=='/api/research' and method=='GET':
   if not SUPERVISOR_TOKEN:
    return self.reply(200,b'{"ok":true,"available":false,"items":[]}','application/json; charset=utf-8')
   req=urllib.request.Request('http://supervisor/core/api/states/sensor.claude_todo',headers={'Authorization':'Bearer '+SUPERVISOR_TOKEN,'Accept':'application/json'})
   try:
    with urllib.request.urlopen(req,timeout=5) as response: state=json.loads(response.read(1024*1024))
    rows=state.get('attributes',{}).get('todo_rows',[])
    items=[{'what':str(row.get('what') or ''),'due':str(row.get('due') or '')} for row in rows if isinstance(row,dict) and str(row.get('what') or '').lower().startswith('games:')]
    return self.reply(200,json.dumps({'ok':True,'available':True,'items':items}).encode(),'application/json; charset=utf-8')
   except Exception:
    return self.reply(200,b'{"ok":true,"available":false,"items":[]}','application/json; charset=utf-8')
  player=(query.get('player') or [''])[0]
  if not player or player not in {str(p.get('key') or '') for p in players}:
   return self.reply(400,json.dumps({'ok':False,'error':'Choose a configured player.'}).encode(),'application/json; charset=utf-8')
  token=str(opts.get('admin_token') or '').strip()
  if not token:
   return self.reply(503,json.dumps({'ok':False,'error':'The Games API is not configured.'}).encode(),'application/json; charset=utf-8')
  art_request=re.fullmatch(r'/api/games/([A-Za-z0-9_-]{1,120})/art',path)
  if art_request and method=='POST':
   upstream='/games/'+art_request.group(1)+'/art?'+urllib.parse.urlencode({'player':player})
   request_body=b'{}'
  elif path=='/api/library' and method=='GET':
   upstream='/library?'+urllib.parse.urlencode({'player':player})
   request_body=None
  elif method=='GET':
   match=re.fullmatch(r'/api/games/([A-Za-z0-9_-]+)/details',path)
   if not match:
    return self.send_error(404)
   upstream='/games/'+match.group(1)+'/details?'+urllib.parse.urlencode({'player':player})
   request_body=None
  else:
   return self.send_error(404)
  req=urllib.request.Request('http://127.0.0.1:%d%s'%(API_PORT,upstream),data=request_body,method=method,headers={'Authorization':'Bearer '+token,'Accept':'application/json','Content-Type':'application/json'})
  try:
   with urllib.request.urlopen(req,timeout=45 if method=='POST' else 8) as response:
    data=response.read(2*1024*1024+1)
    if len(data)>2*1024*1024:
     return self.reply(502,json.dumps({'ok':False,'error':'Game details are too large to display safely.'}).encode(),'application/json; charset=utf-8')
    return self.reply(response.status,data,'application/json; charset=utf-8')
  except urllib.error.HTTPError as e:
   data=e.read(65536)
   return self.reply(e.code,data,'application/json; charset=utf-8')
  except Exception:
   return self.reply(503,json.dumps({'ok':False,'error':'The Games service is temporarily unavailable.'}).encode(),'application/json; charset=utf-8')
 def reply(self,code,body,typ):
  self.send_response(code);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
if __name__=='__main__': ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('IH_UI_PORT','8811'))),Handler).serve_forever()
