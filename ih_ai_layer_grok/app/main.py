"""Intelligent Home - AI Layer - Grok (ih_ai_layer_grok): HTTP front for agent.Agent."""
import json, logging, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent as agentmod
import jobs_link

PORT = 8808
MAX_BODY = 16384
REGISTER_EVERY = 30


def make_handler(ag):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct='application/json', sign=False):
            if isinstance(body, dict):
                if sign and ag.opts['register_key']:
                    body, hd = jobs_link.pack(ag.opts['register_key'], body)
                else:
                    body, hd = json.dumps(body).encode(), {}
            else:
                body, hd = body.encode(), {}
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store')
            if jobs_link.HEADER in hd:
                self.send_header(jobs_link.HEADER, hd[jobs_link.HEADER])
            self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            p = self.path.split('?')[0]
            if p == '/healthz':
                self._send(200, 'ok', 'text/plain')
            elif p == '/status':
                self._send(200, ag.status())
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path.split('?')[0] != '/ask':
                self.send_error(404); return
            n = int(self.headers.get('Content-Length') or 0)
            if n > MAX_BODY:
                self.send_error(413); return
            obj, why = jobs_link.unpack(ag.opts['register_key'], self.rfile.read(n), self.headers.get(jobs_link.HEADER))
            if obj is None:
                self._send(401, {'ok': False, 'why': why}); return
            self._send(200, ag.ask(obj), sign=True)

        def log_message(self, *a):
            pass
    return H


def main():
    lg = logging.getLogger('ih_ai_layer_grok'); lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    opts = agentmod.load_options(os.environ.get('IHA_OPTIONS', '/data/options.json'))
    ag = agentmod.Agent(opts, data=os.environ.get('IHA_DATA', '/data'), log=lg.info)
    lg.info('ih_ai_layer_grok %s starting: grok %s; shadow %s; cap %d/day; parent link %s', agentmod.VERSION,
            'configured' if opts['xai_key'] else 'NOT configured', opts['shadow'], opts['daily_cap'], 'on' if opts['register_key'] else 'OFF (no register_key)')

    def registrar():
        while True:
            ag.register(); time.sleep(REGISTER_EVERY)
    threading.Thread(target=registrar, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(ag)).serve_forever()


if __name__ == '__main__':
    main()
