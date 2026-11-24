"""Optional, read-only access to a shared display profile."""
import http.client
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import frames

ID=re.compile(r'^[a-f0-9]{32}$')
MAX_SIZE=65536

class ProfileClient:
    def __init__(self,url,token,profile_id,opener=None):
        self.url=url.rstrip('/') if isinstance(url,str) else ''
        self.token=token if isinstance(token,str) else ''
        self.profile_id=profile_id if isinstance(profile_id,str) else ''
        self.opener=opener or urllib.request.build_opener(frames.NoRedirect())
    def read(self):
        if not self.url and not self.token and not self.profile_id:
            return {'state':'not_configured'}
        if not frames.valid_url(self.url) or urlsplit(self.url).query or not self.token or not ID.fullmatch(self.profile_id):
            return {'state':'not_configured'}
        try:
            req=urllib.request.Request(self.url+'/v1/profiles/'+self.profile_id,headers={'Authorization':'Bearer '+self.token,'Accept':'application/json'})
            with self.opener.open(req,timeout=3) as response:
                raw=response.read(MAX_SIZE+1)
                if response.status!=200 or len(raw)>MAX_SIZE:raise ValueError()
            value=json.loads(raw)
            if not isinstance(value,dict) or value.get('id')!=self.profile_id or value.get('kind')!='wall':
                return {'state':'invalid'}
            name=value.get('name');width=value.get('width');height=value.get('height');refresh=value.get('refresh_seconds')
            if not isinstance(name,str) or not name or len(name)>80 or type(value.get('touch')) is not bool or any(type(n) is not int or n<1 for n in (width,height,refresh)):
                return {'state':'invalid'}
            return {'state':'ready','name':name,'width':width,'height':height,'refresh_seconds':refresh,'touch':value.get('touch') is True}
        except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException):
            return {'state':'unavailable'}

    def message_tasks(self):
        if not frames.valid_url(self.url) or urlsplit(self.url).query or not self.token:
            return None
        try:
            req=urllib.request.Request(self.url+'/v1/tasks',headers={'Authorization':'Bearer '+self.token,'Accept':'application/json'})
            with self.opener.open(req,timeout=8) as response:
                raw=response.read(MAX_SIZE+1)
                if response.status!=200 or len(raw)>MAX_SIZE:raise ValueError()
            value=json.loads(raw)
            if not isinstance(value,dict) or type(value.get('configured')) is not bool or type(value.get('complete')) is not bool or not isinstance(value.get('items'),list):return None
            for item in value['items']:
                if not isinstance(item,dict) or not isinstance(item.get('title'),str) or not item['title'].strip() or not isinstance(item.get('detail'),str) or item.get('priority') not in ('high','normal','low'):return None
            if not value['configured']:return None
            return value
        except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException):return None

    def alerts(self):
        if not frames.valid_url(self.url) or urlsplit(self.url).query or not self.token:return None
        try:
            req=urllib.request.Request(self.url+'/v1/alerts',headers={'Authorization':'Bearer '+self.token,'Accept':'application/json'})
            with self.opener.open(req,timeout=8) as response:
                raw=response.read(MAX_SIZE+1)
                if response.status!=200 or len(raw)>MAX_SIZE:raise ValueError()
            value=json.loads(raw)
            if not isinstance(value,dict) or type(value.get('configured')) is not bool or type(value.get('complete')) is not bool or not isinstance(value.get('alerts'),list) or len(value['alerts'])>100:return None
            for item in value['alerts']:
                if not isinstance(item,dict) or item.get('kind') not in ('smoke','co','robot','free','saving') or not isinstance(item.get('title'),str) or not item['title'].strip() or not isinstance(item.get('short'),str) or type(item.get('dismissible')) is not bool:return None
                if item['dismissible'] != (item['kind'] in ('free','saving')):return None
            off=value.get('offline');value['offline']=[n.strip() for n in off[:4] if isinstance(n,str) and re.fullmatch(r"[A-Za-z0-9 ._'()-]{1,40}",n.strip())] if isinstance(off,list) else []
            return value
        except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException):return None
