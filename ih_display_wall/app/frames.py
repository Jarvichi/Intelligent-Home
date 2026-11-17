"""Bounded read-only preview of an administrator-configured picture."""
import http.client
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

LIMIT=8*1024*1024
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def valid_url(url):
    if not isinstance(url,str) or not url or len(url)>2048: return False
    try:
        parsed=urlsplit(url)
        return parsed.scheme in ('http','https') and bool(parsed.hostname) and parsed.username is None and parsed.password is None and not parsed.fragment and parsed.port != 0
    except ValueError: return False

class Frames:
    def __init__(self,url,opener=None,clock=time.time):
        self.url=url.strip() if isinstance(url,str) else ''
        self.opener=opener or urllib.request.build_opener(NoRedirect())
        self.clock=clock;self.lock=threading.Lock();self.image=None;self.kind=None;self.last_success=None;self.stale=False
    def refresh(self):
        with self.lock:
            if not valid_url(self.url):
                self.stale=self.image is not None
                return self.status_locked('not_configured')
            try:
                req=urllib.request.Request(self.url,headers={'Accept':'image/png, image/jpeg','User-Agent':'IH-Wall-Display/1'})
                with self.opener.open(req,timeout=10) as response:
                    if response.status!=200: raise ValueError('source')
                    data=response.read(LIMIT+1)
                if len(data)>LIMIT: raise ValueError('size')
                if data.startswith(b'\x89PNG\r\n\x1a\n'): kind='image/png'
                elif data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'): kind='image/jpeg'
                else: raise ValueError('image')
                self.image,self.kind,self.last_success,self.stale=data,kind,int(self.clock()),False
                return self.status_locked('ready')
            except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException):
                self.stale=self.image is not None
                return self.status_locked('unavailable')
    def status_locked(self,state=None):
        return {'state':state or ('stale' if self.stale else ('ready' if self.image else ('not_configured' if not valid_url(self.url) else 'not_loaded'))),'has_frame':self.image is not None,'stale':self.stale,'last_success':self.last_success}
    def status(self):
        with self.lock:return self.status_locked()
    def frame(self):
        with self.lock:return self.image,self.kind
