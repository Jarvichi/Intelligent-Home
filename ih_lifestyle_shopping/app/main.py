"""Private Shopping API and Supervisor-only ingress. No list writes by default."""
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
import ha_client
import cooking_client
import layouts
import list_bridge
import meal_merge
import service

PEER = '172.30.32.2'
MAX_BODY = 512 * 1024


def make_handler(app, token, ingress=False, peer=PEER):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, obj):
            raw = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def run(self, method):
            path = urlparse(self.path).path
            if ingress:
                if self.client_address[0] != peer:
                    return self.send_json(403, {'error':'Forbidden.'})
                if not path.startswith('/api/'):
                    return self.send_json(404, {'error':'Not found.'})
                path = path[4:]
            elif path != '/healthz':
                expected = 'Bearer ' + token
                if not token or not hmac.compare_digest(self.headers.get('Authorization', ''), expected):
                    return self.send_json(401, {'error':'Unauthorised.'})
            body = None
            if method in ('POST','PUT'):
                try:
                    if self.headers.get('Content-Type','').split(';',1)[0].strip().lower() != 'application/json':
                        raise ValueError()
                    length = int(self.headers.get('Content-Length','0'))
                    if not 0 < length <= MAX_BODY:
                        raise ValueError()
                    body = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeDecodeError):
                    return self.send_json(400, {'error':'Invalid request.'})
            return self.send_json(*app.route(method, path, body))

        def do_GET(self): self.run('GET')
        def do_POST(self): self.run('POST')
        def do_PUT(self): self.run('PUT')
        def log_message(self, *args): pass
    return Handler


def main():
    data = os.environ.get('IHA_DATA', '/data')
    try:
        with open(os.path.join(data,'options.json')) as source:
            opts = json.load(source)
    except (OSError, ValueError):
        opts = {}
    token = opts.get('api_token') or ''
    entity = opts.get('shopping_list_entity') or 'todo.shopping_list'
    shopping = list_bridge.ShoppingList(ha_client.HA(os.environ.get('SUPERVISOR_TOKEN','')), entity)
    meals = meal_merge.MealShopping(cooking_client.Cooking(opts.get('cooking_url') or '', opts.get('cooking_api_token') or ''), shopping)
    def app():
        return service.App(layouts.connect(os.path.join(data,'shopping.db')), shopping, opts.get('list_writes_enabled'), meals)
    ingress = HTTPServer(('0.0.0.0',8826),make_handler(app(), token, ingress=True))
    threading.Thread(target=ingress.serve_forever,daemon=True).start()
    HTTPServer(('0.0.0.0',8804),make_handler(app(), token)).serve_forever()


if __name__ == '__main__': main()
