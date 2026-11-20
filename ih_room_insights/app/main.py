"""ih_room_insights: read-only room insights (warm-up learning, frozen-valve check, mould risk)."""
import hmac, json, logging, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ihlib import db as ihdb
from ihlib.ha import HA
import engine as E

DATA = os.environ.get('IHRI_DATA', '/data')
PORT = int(os.environ.get('IHRI_PORT', '8812'))


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
            try:
                out = eng.run_once()
                if live:
                    eng.publish(out)
                lg.info('run ok: %d rooms, %d suspect valves, mould %s', len(out['rooms']), len(out['frozen']),
                        {m['room']: m['level'] for m in out['mould'].values()})
                if time.time() - last_snap > 86400:
                    ihdb.snapshot(os.path.join(DATA, 'ih.db'), os.path.join(DATA, 'backup'))
                    last_snap = time.time()
            except Exception as e:
                lg.info('run failed (%s)', type(e).__name__)
            time.sleep(max(5, int(opts.get('interval_min', 15))) * 60)
    threading.Thread(target=ticker, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(eng, lambda: opts.get('api_token', ''))).serve_forever()


if __name__ == '__main__':
    main()
