"""Read the owner-controlled cooking display; never send actions from preview."""
import http.client
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import frames

LIMIT=65536
class CookingClient:
    def __init__(self,url,token,opener=None):
        self.url=url.rstrip('/') if isinstance(url,str) else '';self.token=token if isinstance(token,str) else ''
        self.opener=opener or urllib.request.build_opener(frames.NoRedirect())
    def _get(self,path):
        if not frames.valid_url(self.url) or urlsplit(self.url).query or not self.token:return None
        try:
            req=urllib.request.Request(self.url+path,headers={'Authorization':'Bearer '+self.token,'Accept':'application/json'})
            with self.opener.open(req,timeout=5) as response:
                data=response.read(LIMIT+1)
                if response.status!=200 or len(data)>LIMIT:raise ValueError()
            value=json.loads(data)
            return value if isinstance(value,dict) else None
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return None
    def read(self):
        value=self._get('/cook/display')
        if not isinstance(value,dict) or type(value.get('running')) is not bool:return None
        if value['running'] and (not isinstance(value.get('revision'),str) or any(not isinstance(value.get(k),list) for k in ('next','prep','steps'))):return None
        return value
    def menu(self,page=0):
        if type(page) is not int or not 0<=page<=999:return None
        value=self._get('/cook/menu?page='+str(page))
        if not isinstance(value,dict) or not isinstance(value.get('items'),list) or not isinstance(value.get('revision'),str) or type(value.get('page')) is not int or type(value.get('pages')) is not int:return None
        return value
    def post(self,path,body):
        if path not in ('/cook/display-action','/cook/menu-start') or not frames.valid_url(self.url) or urlsplit(self.url).query or not self.token:return {'ok':False}
        try:
            req=urllib.request.Request(self.url+path,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},method='POST')
            with self.opener.open(req,timeout=8) as response:
                data=response.read(LIMIT+1)
                if response.status!=200 or len(data)>LIMIT:raise ValueError()
            result=json.loads(data)
            return result if isinstance(result,dict) else {'ok':False}
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return {'ok':False}
