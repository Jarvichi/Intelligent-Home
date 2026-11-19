"""Read-only dishwasher task, preserving the four-hour cooling interval."""
import datetime
import http.client
import json
import math
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import messages

PENDING=re.compile(r'^input_boolean\.[a-z0-9_]+$')
FINISHED=re.compile(r'^input_datetime\.[a-z0-9_]+$')
DATE=re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}')
COOLING_SECONDS=4*60*60

class Dishwasher:
    def __init__(self,pending,finished,token,opener=None,clock=time.time,format_time=None):
        self.pending=pending if isinstance(pending,str) else ''
        self.finished=finished if isinstance(finished,str) else ''
        self.token=token if isinstance(token,str) else ''
        self.opener=opener or urllib.request.build_opener(messages.NoRedirect())
        self.clock=clock
        self.format_time=format_time or (lambda timestamp:time.strftime('%H:%M',time.localtime(timestamp)))
    def _get(self,entity):
        if not self.token:return None
        try:
            request=urllib.request.Request('http://supervisor/core/api/states/'+entity,headers={'Authorization':'Bearer '+self.token})
            with self.opener.open(request,timeout=3) as response:
                raw=response.read(messages.LIMIT+1)
                if response.status!=200 or len(raw)>messages.LIMIT:return None
            value=json.loads(raw)
            return value if isinstance(value,dict) else None
        except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException):return None
    def read(self):
        if not self.pending and not self.finished:return {'configured':False,'complete':True,'items':[]}
        if not PENDING.fullmatch(self.pending) or not FINISHED.fullmatch(self.finished):return {'configured':True,'complete':False,'items':[]}
        flag=self._get(self.pending)
        if not flag or flag.get('state') not in ('on','off'):return {'configured':True,'complete':False,'items':[]}
        if flag['state']=='off':return {'configured':True,'complete':True,'items':[]}
        finish=self._get(self.finished);timestamp=None;complete=True
        if finish and isinstance(finish.get('state'),str) and DATE.match(finish['state']):
            attr=finish.get('attributes')
            value=attr.get('timestamp') if isinstance(attr,dict) else None
            if type(value) in (int,float) and math.isfinite(value):timestamp=value
        if timestamp is None:
            complete=False
            try:timestamp=datetime.datetime.fromisoformat(flag['last_changed'].replace('Z','+00:00')).timestamp()
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError):return {'configured':True,'complete':False,'items':[]}
        now=self.clock()
        try:
            if now-timestamp<COOLING_SECONDS:
                title='Dishwasher finished · cooling';detail='Ready to unload at '+self.format_time(timestamp+COOLING_SECONDS)
            else:
                title='Dishwasher ready to unload'
                same_day=datetime.date.fromtimestamp(timestamp)==datetime.date.fromtimestamp(now)
                detail='Finished '+('' if same_day else 'yesterday ')+self.format_time(timestamp)
        except (OverflowError,OSError,ValueError):return {'configured':True,'complete':False,'items':[]}
        item={'title':title,'detail':detail,'priority':'normal'}
        identity=messages.action_identity(flag)
        if identity:item.update(action_kind='dishwasher',action_source=self.pending,action_identity=identity)
        return {'configured':True,'complete':complete,'items':[item]}

class Combined:
    def __init__(self,*sources,groups=None):
        self.sources=sources;self.groups=groups if isinstance(groups,(tuple,list)) and len(groups)==len(sources) else [None]*len(sources)
    def read(self):
        with ThreadPoolExecutor(max_workers=max(1,len(self.sources))) as pool:
            feeds=list(pool.map(lambda source:source.read(),self.sources))
        active=[(f,g) for f,g in zip(feeds,self.groups) if f['configured']]
        items=[dict(item,display_group=group) if group else item for feed,group in active for item in feed['items']]
        return {'configured':bool(active),'complete':all(f['complete'] for f,_ in active),'items':items}
