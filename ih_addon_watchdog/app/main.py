#!/usr/bin/env python3
"""IH add-on watchdog: monitor loop + ingress UI (port 8850, Supervisor only) + /healthz (port 8851)."""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ihlib import db as ihdb
import core, clients

DATA = os.environ.get('IHWD_DATA', '/data')
UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui', 'index.html')
PEER = '172.30.32.2'
DEFAULTS = {'mode': 'dry', 'interval_seconds': 60, 'failed_probes': 3, 'cooldown_minutes': 10, 'max_restarts_per_hour': 3, 'http_timeout_seconds': 5,
            'never_restart': [], 'media_players': [], 'media_probe_enabled': True, 'media_probe_clip': '', 'tasks_url': '', 'tasks_token': '',
            'discord_bot_token': '', 'discord_channel_id': ''}
STATE = {'last_cycle': 0, 'error': ''}


def make(data=DATA):
    d = ihdb.open_db(os.path.join(data, 'ih.db'), core.MIGRATIONS, snapshot_dir=os.path.join(data, 'backup'))
    kv = ihdb.KV(d, 'watchdog')

    def get_opts():
        o = dict(DEFAULTS)
        try:
            o.update(json.load(open(os.path.join(data, 'options.json'))))
        except (OSError, ValueError):
            pass
        ov = kv.load('settings', {})
        if ov.get('mode') in ('dry', 'live'):
            o['mode'] = ov['mode']          # the page switch wins over the option
        return o
    sup = clients.Supervisor()
    wd = core.Watchdog(d, kv, sup, lambda u: clients.http_probe(u, get_opts()['http_timeout_seconds']), clients.Tasks(get_opts),
                       clients.discord_notifier(get_opts), clients.Media(get_opts), get_opts)
    return d, kv, wd, get_opts


def snapshot(d, kv, wd, get_opts):
    rows = sorted(wd.health.values(), key=lambda r: (r.get('state') == 'started', r['name'].lower()))
    inc = [dict(zip(('id', 'at', 'slug', 'name', 'probe', 'action', 'dry', 'task', 'excerpt'), r))
           for r in d.query('SELECT id,at,slug,name,probe,action,dry,task,excerpt FROM incidents ORDER BY id DESC LIMIT 40')]
    return {'mode': get_opts()['mode'], 'rows': rows, 'incidents': inc, 'last_cycle': STATE['last_cycle'], 'error': STATE['error']}


def handler(d, kv, wd, get_opts):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def send(self, code, body, ctype='application/json; charset=utf-8'):
            b = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            if self.client_address[0] != PEER:
                return self.send_error(403)
            p = self.path.split('?', 1)[0]
            if p == '/api/state':
                return self.send(200, snapshot(d, kv, wd, get_opts))
            if p in ('/', '/index.html'):
                return self.send(200, open(UI, 'rb').read(), 'text/html; charset=utf-8')
            self.send_error(404)

        def do_POST(self):
            if self.client_address[0] != PEER:
                return self.send_error(403)
            try:
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(min(n, 4096)) or b'{}')
            except ValueError:
                return self.send_error(400)
            p = self.path.split('?', 1)[0]
            if p == '/api/mode' and body.get('mode') in ('dry', 'live'):
                kv.save('settings', dict(kv.load('settings', {}), mode=body['mode']))
                return self.send(200, {'ok': True})
            if p == '/api/toggle' and isinstance(body.get('slug'), str):
                s = wd.st(body['slug']); s['enabled'] = bool(body.get('enabled')); wd.save(body['slug'], s)
                if body['slug'] in wd.health:
                    wd.health[body['slug']]['enabled'] = s['enabled']
                return self.send(200, {'ok': True})
            self.send_error(404)
    return H


def health_handler():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            ok = time.time() - STATE['last_cycle'] < 600 or time.time() - START < 300
            self.send_response(200 if (self.path.startswith('/healthz') and ok) else 503); self.end_headers(); self.wfile.write(b'ok' if ok else b'stale')
    return H


START = time.time()


def loop(d, wd, get_opts):
    last_prune = 0
    while True:
        try:
            wd.cycle(); wd.media_cycle(); STATE['error'] = ''
        except Exception as e:
            STATE['error'] = type(e).__name__
        STATE['last_cycle'] = time.time()
        if time.time() - last_prune > 86400:
            d.execute('DELETE FROM incidents WHERE at<?', (time.time() - 30 * 86400,)); d.execute('DELETE FROM restarts WHERE at<?', (time.time() - 3 * 86400,)); last_prune = time.time()
        time.sleep(get_opts()['interval_seconds'])


if __name__ == '__main__':
    d, kv, wd, get_opts = make()
    threading.Thread(target=ThreadingHTTPServer(('0.0.0.0', 8851), health_handler()).serve_forever, daemon=True).start()
    threading.Thread(target=ThreadingHTTPServer(('0.0.0.0', 8850), handler(d, kv, wd, get_opts)).serve_forever, daemon=True).start()
    loop(d, wd, get_opts)
