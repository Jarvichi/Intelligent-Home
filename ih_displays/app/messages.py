"""Read configured message helpers without modifying their source states."""
import http.client
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ENTITY=re.compile(r'^input_text\.[a-z0-9_]+$')
LIMIT=65536
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
class Messages:
    def __init__(self,entities,token,opener=None):
        self.entities=entities if isinstance(entities,list) else []
        self.token=token if isinstance(token,str) else ''
        self.opener=opener or urllib.request.build_opener(NoRedirect())
    def _state(self,entity):
        if not isinstance(entity,str) or not ENTITY.fullmatch(entity) or not self.token:return None
        try:
            req=urllib.request.Request('http://supervisor/core/api/states/'+entity,headers={'Authorization':'Bearer '+self.token})
            with self.opener.open(req,timeout=3) as response:
                body=response.read(LIMIT+1)
                if response.status!=200 or len(body)>LIMIT:return None
            data=json.loads(body);state=data.get('state')
            return state if isinstance(state,str) and state not in ('unknown','unavailable') else None
        except (OSError,ValueError,AttributeError,urllib.error.URLError,http.client.HTTPException):return None
    def read(self):
        if not self.entities:return {'configured':False,'complete':True,'items':[]}
        if len(self.entities)>12:return {'configured':True,'complete':False,'items':[]}
        items=[];complete=True
        with ThreadPoolExecutor(max_workers=12) as pool:
            states=list(pool.map(self._state,self.entities))
        for state in states:
            if state is None:complete=False;continue
            if not state.strip():continue
            title,_,detail=state.partition('|');title=title.strip();detail=detail.strip()
            if not title:complete=False;continue
            items.append({'title':title,'detail':detail,'priority':'high'})
        return {'configured':True,'complete':complete,'items':items}
