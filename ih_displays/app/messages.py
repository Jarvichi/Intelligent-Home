"""Read configured message helpers without modifying their source states."""
import http.client
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ENTITY=re.compile(r'^input_text\.[a-z0-9_]+$')
LIMIT=65536
def action_identity(value):
    """Identity of the exact HA state used to derive a displayed task."""
    if not isinstance(value,dict):return None
    state=value.get('state');changed=value.get('last_changed')
    if not isinstance(state,str) or state in ('unknown','unavailable') or not isinstance(changed,str) or not changed:return None
    return {'state':state,'last_changed':changed}

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
            return data if isinstance(state,str) and state not in ('unknown','unavailable') else None
        except (OSError,ValueError,AttributeError,urllib.error.URLError,http.client.HTTPException):return None
    def read(self):
        if not self.entities:return {'configured':False,'complete':True,'items':[]}
        if len(self.entities)>12:return {'configured':True,'complete':False,'items':[]}
        items=[];complete=True
        with ThreadPoolExecutor(max_workers=12) as pool:
            states=list(pool.map(self._state,self.entities))
        for entity,value in zip(self.entities,states):
            if value is None:complete=False;continue
            state=value['state']
            if not state.strip():continue
            title,_,detail=state.partition('|');title=title.strip();detail=detail.strip()
            if not title:complete=False;continue
            item={'title':title,'detail':detail,'priority':'high'}
            identity=action_identity(value)
            if identity:item.update(action_kind='message',action_source=entity,action_identity=identity)
            items.append(item)
        return {'configured':True,'complete':complete,'items':items}
