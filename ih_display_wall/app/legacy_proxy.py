"""Optional compatibility listener for the existing screensaver image client."""
import http.client
import re
import subprocess
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

LIMIT=16*1024*1024
HOP={'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade','host','content-length'}

def upstream_origin(value):
    try:
        url=urlsplit(value)
        if url.scheme!='http' or not url.hostname or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:return None
        return (url.hostname,url.port or 80)
    except (ValueError,TypeError):return None

def make_handler(upstream,render=None,wall_enabled=False):
    origin=upstream_origin(upstream)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def fail(self,status):
            self.send_response(status);self.send_header('Content-Length','0');self.send_header('Cache-Control','no-store');self.end_headers()
        def handle_request(self):
            # Accept origin-form only: never let callers choose the proxy destination.
            if not self.path.startswith('/') or self.path.startswith('//') or '\\' in self.path or '\r' in self.path or '\n' in self.path:return self.fail(400)
            if self.command in ('GET','HEAD') and urlsplit(self.path).path=='/7' and wall_enabled and render:
                try:
                    data=render()
                    if not isinstance(data,bytes) or not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data)>LIMIT:raise ValueError()
                except (OSError,ValueError,TypeError,subprocess.SubprocessError):data=None
                if data is not None:
                    self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-IH-Image-Source','wall');self.end_headers()
                    if self.command=='GET':self.wfile.write(data)
                    return
            if not origin:return self.fail(503)
            if self.headers.get('Transfer-Encoding'):return self.fail(501)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<0 or size>LIMIT:return self.fail(413)
                body=self.rfile.read(size) if size else None
                headers={k:v for k,v in self.headers.items() if k.lower() not in HOP}
                if size:headers['Content-Length']=str(size)
                conn=http.client.HTTPConnection(*origin,timeout=10)
                try:
                    conn.request(self.command,self.path,body=body,headers=headers);response=conn.getresponse()
                    length=response.getheader('Content-Length')
                    if length and int(length)>LIMIT:return self.fail(502)
                    data=response.read(LIMIT+1) if self.command!='HEAD' else b''
                    if len(data)>LIMIT:return self.fail(502)
                    self.send_response(response.status)
                    for key,value in response.getheaders():
                        if key.lower() not in HOP and key.lower() not in ('server','date'):self.send_header(key,value)
                    self.send_header('Content-Length',length if self.command=='HEAD' and length else str(len(data)))
                    if urlsplit(self.path).path=='/7':self.send_header('X-IH-Image-Source','legacy')
                    self.end_headers()
                    if self.command!='HEAD':self.wfile.write(data)
                finally:conn.close()
            except (OSError,ValueError,http.client.HTTPException):self.fail(502)
        do_GET=do_HEAD=do_POST=do_PUT=do_PATCH=do_DELETE=do_OPTIONS=handle_request
    return Handler
