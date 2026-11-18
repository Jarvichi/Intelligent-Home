"""Read the active cooking session from its owner, without sending actions."""
import http.client
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import frames

LIMIT=65536

class CookingClient:
    def __init__(self,url,token,opener=None):
        self.url=url.rstrip('/') if isinstance(url,str) else ''
        self.token=token if isinstance(token,str) else ''
        self.opener=opener or urllib.request.build_opener(frames.NoRedirect())
    def read(self):
        if not frames.valid_url(self.url) or urlsplit(self.url).query or not self.token:return {'available':False}
        try:
            req=urllib.request.Request(self.url+'/cook',headers={'Authorization':'Bearer '+self.token,'Accept':'application/json'})
            with self.opener.open(req,timeout=5) as response:
                raw=response.read(LIMIT+1)
                if response.status!=200 or len(raw)>LIMIT:raise ValueError()
            data=json.loads(raw)
            if not isinstance(data,dict) or type(data.get('running')) is not bool:return {'available':False}
            if not data['running']:return {'available':True,'active':False}
            title=data.get('title');eat=data.get('eat')
            if not isinstance(title,str) or not title.strip() or (eat is not None and not isinstance(eat,str)):return {'available':False}
            return {'available':True,'active':True,'label':title,'detail':'Eating at '+eat if eat else ''}
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return {'available':False}
