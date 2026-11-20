"""Authenticated, opt-in device transport. Never publish this listener by default."""
import hmac
import json
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import device_frames


def make_handler(frames, build, token, card=None):
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError('a device token of at least 32 characters is required')
    class Handler(BaseHTTPRequestHandler):
        def send(self, code, data, kind='text/plain; charset=utf-8'):
            if isinstance(data, str): data = data.encode()
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            value = self.headers.get('Authorization', '')
            try: return hmac.compare_digest(value.encode('utf-8'), ('Bearer ' + token).encode('utf-8'))
            except (TypeError, UnicodeError): return False

        def do_GET(self):
            if not self.authorized(): return self.send(401, 'Unauthorized')
            request = urlparse(self.path)
            if request.path == '/device/v1/frame':
                query = parse_qs(request.query)
                screen = (query.get('screen') or ['main'])[0]
                page = (query.get('page') or ['0'])[0]
                eat = (query.get('eat') or ['asap'])[0]
                if screen not in ('main', 'menu', 'dinner', 'home', 'birthdays') or not re.fullmatch(r'\d{1,3}', page) or eat not in ('asap','1230','1330','1830'):
                    return self.send(400, 'Invalid screen')
                try:
                    image, zones = build(screen, int(page), eat)
                    frame = frames.bundle(frames.issue(image, zones))
                    lines = ['frame ' + frame['frame'], 'sha256 ' + frame['sha256']]
                    for zone in frame['zones']:
                        lines.append('zone ' + zone['id'] + ' ' + ' '.join(str(x) for x in zone['box'])+' '+zone['power']+' '+zone['preview'])
                    return self.send(200, '\n'.join(lines) + '\n')
                except Exception:
                    return self.send(503, 'Frame unavailable')
            if request.path == '/device/v1/card/tyres':
                if not card: return self.send(404, 'Not found')
                query = parse_qs(request.query)
                values = {}
                for k in ('fl', 'fr', 'rl', 'rr', 'tfl', 'tfr', 'trl', 'trr'):
                    v = (query.get(k) or [''])[0]
                    if not re.fullmatch(r'\d{1,3}(\.\d)?', v): return self.send(400, 'Invalid tyre values')
                    values[k] = float(v)
                image = card('tyres', values)
                return self.send(200, image, 'image/png') if image else self.send(503, 'Card unavailable')
            prefix = '/device/v1/picture/'
            if request.path.startswith(prefix):
                frame = frames.bundle(request.path[len(prefix):])
                return self.send(200, frame['image'], 'image/png') if frame else self.send(404, 'Not found')
            return self.send(404, 'Not found')

        def do_POST(self):
            if not self.authorized(): return self.send(401, 'Unauthorized')
            if self.path not in ('/device/v1/activate', '/device/v1/tap'): return self.send(404, 'Not found')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 512: raise ValueError()
                data = parse_qs(self.rfile.read(length).decode('ascii'), strict_parsing=True, max_num_fields=2)
                if any(len(v) != 1 for v in data.values()): raise ValueError()
                if self.path.endswith('/activate'):
                    if set(data) != {'frame', 'sha256'}: raise ValueError()
                    result = {'ok': frames.activate(data['frame'][0], data['sha256'][0])}
                else:
                    if set(data) != {'frame', 'zone'}: raise ValueError()
                    result = frames.tap(data['frame'][0], data['zone'][0])
                navigation=result.get('navigate') if result.get('ok') is True else None
                if isinstance(navigation,dict):
                    screen=navigation.get('screen');page=navigation.get('page',0);eat=navigation.get('eat','asap')
                    if screen in ('main','menu','dinner','home','birthdays','wifi') and type(page) is int and 0<=page<=999 and eat in ('asap','1230','1330','1830'):
                        return self.send(200, f'navigate {screen} {page} {eat}\n')
                return self.send(200 if result.get('ok') is True else 409, json.dumps({'ok': result.get('ok') is True}), 'application/json')
            except (UnicodeError, ValueError):
                return self.send(400, 'Invalid request')
        def log_message(self, *args): pass
    return Handler
