"""ih_room_insights: read-only room insights (warm-up learning, frozen-valve check, mould risk)."""
import hmac, json, logging, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ihlib import db as ihdb
from ihlib.ha import HA
import engine as E
import ui_server

DATA = os.environ.get('IHRI_DATA', '/data')
PORT = int(os.environ.get('IHRI_PORT', '8812'))
UI_PORT = int(os.environ.get('IHRI_UI_PORT', '8813'))


def load_options():
    try:
        with open(os.path.join(DATA, 'options.json')) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}
    return o


def make_handler(eng, token):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype='application/json'):
            b = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path == '/healthz':
                return self._send(200, b'ok', 'text/plain')
            t = token()
            sent = (self.headers.get('Authorization') or '')[7:]
            if not t or not hmac.compare_digest(sent, t):
                return self._send(401, {'error': 'unauthorized'})
            if self.path in ('/status', '/insights'):
                return self._send(200, {'mode': eng.opts.get('mode'), 'version': E.VERSION, **eng.last})
            self._send(404, {'error': 'not found'})

        def log_message(self, *a):
            pass
    return H


def run_tick(eng, live, last_snap, snapshot, now, log):
    try:
        out = eng.run_once()
        if live:
            eng.publish(out)
        log('run ok: %d rooms, %d suspect valves, mould %s', len(out['rooms']), len(out['frozen']),
            {m['room']: m['level'] for m in out['mould'].values()})
    except Exception as e:
        log('run failed (%s)', type(e).__name__)
    if now - last_snap > 86400:
        try:
            snapshot(os.path.join(DATA, 'ih.db'), os.path.join(DATA, 'backup'))
            return now
        except Exception as e:
            log('snapshot failed (%s)', type(e).__name__)
    return last_snap


def main():
    lg = logging.getLogger('ihri')
    lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__)
    sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
    lg.addHandler(sh)
    opts = load_options()
    d = ihdb.open(os.path.join(DATA, 'ih.db'), E.MIGRATIONS, snapshot_dir=os.path.join(DATA, 'backup'))
    eng = E.Engine(d, HA(), opts, log=lg.info)
    live = opts.get('mode') == 'live'
    lg.info('ih_room_insights %s starting: %s (read-only; no service calls)', E.VERSION, 'LIVE: publishes sensors' if live else 'DRY: computes only')

    def ticker():
        last_snap = 0
        while True:
            last_snap = run_tick(eng, live, last_snap, ihdb.snapshot, time.time(), lg.info)
            time.sleep(max(5, int(opts.get('interval_min', 15))) * 60)
    threading.Thread(target=ticker, daemon=True).start()
    threading.Thread(target=ui_server.make_server(eng, E.VERSION, UI_PORT).serve_forever, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(eng, lambda: opts.get('api_token', ''))).serve_forever()


if __name__ == '__main__':
    main()
