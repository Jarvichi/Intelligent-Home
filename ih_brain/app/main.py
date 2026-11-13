"""Intelligent Home - Brain (ih_brain): serves the House Brain notes as an MCP server over HTTP (stdlib only)."""
import hmac, json, logging, os, sys, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ihlib import trace
import brainlib, mcp, extras, codex_todo
import ui_server
import instructions_api

VERSION = '0.5.2'
MAX_BODY = 512 * 1024


def parse_origins(v):
    """cors_origins: comma separated exact origins (scheme://host:port). Empty = no CORS. A bare * is never accepted."""
    out = []
    for x in str(v or '').split(','):
        x = x.strip().rstrip('/')
        if x and x != '*' and '://' in x:
            out.append(x)
    return out


def load_options(path):
    try:
        with open(path) as f:
            o = json.load(f)
    except Exception:
        o = {}
    return {'brain_dir': str(o.get('brain_dir') or '/share/brain'), 'api_token': str(o.get('api_token') or ''),
            'codex_read_token': str(o.get('codex_read_token') or ''), 'codex_todo_token': str(o.get('codex_todo_token') or ''),
            'read_only': o.get('read_only', True) is not False, 'port': int(o.get('port') or 8820),
            'rate_per_min': int(o.get('rate_per_min') or 120),
            'apple_notes_dir': str(o.get('apple_notes_dir') or '/share/apple_notes_staging'),
            'apple_notes_live': o.get('apple_notes_live', False) is True,
            'pocket_live': o.get('pocket_live', False) is True,
            'pocket_interval_min': max(0, int(o.get('pocket_interval_min') or 0)),
            'pocket_cloudflare_token': str(o.get('pocket_cloudflare_token') or ''),
            'pocket_key': str(o.get('pocket_key') or ''),
            'pocket_url': str(o.get('pocket_url') or ''),
            'scrub_names': str(o.get('scrub_names') or ''),
            'cors_origins': parse_origins(o.get('cors_origins')),
            'instructions_mode': instructions_api.clean_mode(o.get('instructions_mode')),
            'instructions_files': instructions_api.clean_files(o.get('instructions_files')),
            'instructions_user_ids': instructions_api.clean_user_ids(o.get('instructions_user_ids')),
            'ingest_url': str(o.get('ingest_url') or '').strip(), 'ingest_token': str(o.get('ingest_token') or '').strip()}


class State:
    def __init__(self, opts, data_dir='/data', log=print, clock=time.time):
        self.opts, self.log, self.clock = opts, log, clock
        self.tracer = trace.from_options('ih_brain', opts)   # off unless ingest_url + ingest_token are in the options
        self.brain = brainlib.Brain(opts['brain_dir'], data_dir, opts['read_only'])
        self.data_dir = data_dir
        self._hits, self._lock = [], threading.Lock()
        self.pocket = None
        self.todo = codex_todo.CodexTodo()
        extras.set_names(opts.get('scrub_names', ''))
        extras.load_choices(data_dir)   # names chosen on the Names page (UI)
        for k, v in (('apple_notes_dir', '/share/apple_notes_staging'), ('apple_notes_live', False), ('pocket_live', False),
                     ('pocket_interval_min', 0), ('pocket_cloudflare_token', ''), ('pocket_key', ''), ('pocket_url', '')):
            opts.setdefault(k, v)
        if opts['pocket_url'] and opts['pocket_key'] and 'REPLACE_ME' not in (opts['pocket_key'] + opts['pocket_cloudflare_token']):
            self.pocket = extras.Pocket(opts['brain_dir'], data_dir, opts['pocket_cloudflare_token'], opts['pocket_key'],
                                        opts['pocket_url'], opts['pocket_live'], log=log)

    def pocket_loop(self):
        while True:
            time.sleep(self.opts['pocket_interval_min'] * 60)
            if not self.stopped():
                self.pocket.run_once()

    def apple_note(self, raw):
        note = extras.decode_payload(raw)
        path = extras.save_note(self.opts['apple_notes_dir'], note, self.opts['apple_notes_live'])
        return {'ok': True, 'dry': not self.opts['apple_notes_live'], 'file': os.path.relpath(path, self.opts['apple_notes_dir'])}

    def authed(self, header):
        tok = self.opts['api_token']
        if not tok or not header or not header.startswith('Bearer '):
            return False
        return hmac.compare_digest(header[7:].strip().encode(), tok.encode())

    def codex_authed(self, header, key='codex_read_token'):
        tok = self.opts.get(key) or ''
        if not tok or not header or not header.startswith('Bearer '):
            return False
        return hmac.compare_digest(header[7:].strip().encode(), tok.encode())

    def stopped(self):
        return os.path.exists(os.path.join(self.data_dir, 'STOP'))

    def rate_ok(self):
        now = self.clock()
        with self._lock:
            self._hits = [t for t in self._hits if now - t < 60]
            if len(self._hits) >= self.opts['rate_per_min']:
                return False
            self._hits.append(now)
            return True

    def status(self):
        notes = self.brain._notes()
        return {'ok': True, 'version': VERSION, 'notes': len(notes), 'read_only': self.opts['read_only'],
                'last_index': self.brain.last_index, 'token_set': bool(self.opts['api_token']),
                'codex_read_token_set': bool(self.opts.get('codex_read_token')),
                'codex_todo_token_set': bool(self.opts.get('codex_todo_token')),
                'apple_notes_live': self.opts['apple_notes_live'], 'pocket_live': self.opts['pocket_live'],
                'pocket_configured': self.pocket is not None, 'pocket_last': self.pocket.last if self.pocket else None}


def make_handler(st):
    class H(BaseHTTPRequestHandler):
        def _cors(self):
            org = self.headers.get('Origin')
            if org and org.rstrip('/') in st.opts.get('cors_origins', []):
                self.send_header('Access-Control-Allow-Origin', org.rstrip('/'))
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
                self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
                self.send_header('Access-Control-Max-Age', '600')
                self.send_header('Vary', 'Origin')

        def do_OPTIONS(self):
            self.send_response(204 if self.headers.get('Origin', '').rstrip('/') in st.opts.get('cors_origins', []) else 403)
            self._cors(); self.send_header('Content-Length', '0'); self.end_headers()

        def _send(self, code, body, ct='application/json'):
            b = (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode()
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store'); self._cors(); self.end_headers(); self.wfile.write(b)

        def _gate(self):
            """True if the request may go on (auth, kill switch, rate limit); else the answer is already sent."""
            if not st.authed(self.headers.get('Authorization')):
                self._send(401, {'ok': False, 'why': 'bearer token required'}); return False
            if st.stopped():
                self._send(503, {'ok': False, 'why': 'STOP file present'}); return False
            if not st.rate_ok():
                self._send(429, {'ok': False, 'why': 'rate limit'}); return False
            return True

        def _codex_gate(self, key='codex_read_token'):
            if not st.codex_authed(self.headers.get('Authorization'), key):
                self._send(401, {'ok': False, 'why': 'Codex credential required'}); return False
            if st.stopped():
                self._send(503, {'ok': False, 'why': 'STOP file present'}); return False
            if not st.rate_ok():
                self._send(429, {'ok': False, 'why': 'rate limit'}); return False
            return True

        def do_GET(self):
            p = self.path.split('?')[0].rstrip('/') or '/'
            if p == '/healthz':
                return self._send(200, 'ok', 'text/plain')
            if not self._gate():
                return
            if p == '/status':
                return self._send(200, st.status())
            if p in ('/mcp', '/codex-mcp', '/codex-todo-mcp'):
                self.send_response(405); self.send_header('Allow', 'POST'); self.send_header('Content-Length', '0'); self.end_headers(); return
            self._send(404, {'ok': False})

        def do_POST(self):
            p = self.path.split('?')[0].rstrip('/') or '/'
            if p not in ('/mcp', '/codex-mcp', '/codex-todo-mcp', '/', '/apple-notes', '/pocket-sync'):
                return self._send(404, {'ok': False})
            if not (self._codex_gate('codex_todo_token' if p == '/codex-todo-mcp' else 'codex_read_token') if p in ('/codex-mcp', '/codex-todo-mcp') else self._gate()):
                return
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413, {'ok': False, 'why': 'body too large'})
            raw = self.rfile.read(n)
            if p == '/apple-notes':
                try:
                    return self._send(200, st.apple_note(raw))
                except Exception:
                    return self._send(400, {'ok': False, 'why': 'bad note payload'})
            if p == '/pocket-sync':
                if st.pocket is None:
                    return self._send(409, {'ok': False, 'why': 'pocket brain not configured'})
                return self._send(200, st.pocket.run_once())
            try:
                msg = json.loads(raw or b'')
            except Exception:
                return self._send(400, {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}})
            with st.tracer.span('mcp_rpc') as sp:
                sp.set(in_bytes=len(raw))
                if p == '/codex-todo-mcp':
                    resp = mcp.handle_rpc(st.todo, msg, VERSION, tools=codex_todo.TOOLS, server_name='ih-brain-codex-todo',
                                          instructions=codex_todo.CODEX_TODO_INSTRUCTIONS)
                elif p == '/codex-mcp':
                    class ShareableBrain:
                        def t_brain_search(self, args): return st.brain.t_codex_brain_search(args)
                        def t_brain_read(self, args): return st.brain.t_codex_brain_read(args)
                    resp = mcp.handle_rpc(ShareableBrain(), msg, VERSION, tools=mcp.CODEX_TOOLS,
                                         server_name='ih-brain-shareable-readonly')
                else:
                    resp = mcp.handle_rpc(st.brain, msg, VERSION)
                try:
                    sp.set(out_bytes=len(json.dumps(resp)) if resp is not None else 0)
                except Exception:
                    pass
            if resp is None:
                self.send_response(202); self.send_header('Content-Length', '0'); self.end_headers(); return
            self._send(200, resp)

        def log_message(self, *a):
            pass
    return H


def main():
    lg = logging.getLogger('ih_brain'); lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    opts = load_options(os.environ.get('IHA_OPTIONS', '/data/options.json'))
    st = State(opts, os.environ.get('IHA_DATA', '/data'), lg.info)
    editor = instructions_api.InstructionsEditor(opts['instructions_mode'], opts['instructions_files'], opts['instructions_user_ids'],
                                                 os.path.join(st.data_dir, 'instructions'))
    ui_server.serve(st.brain, editor, st.data_dir)
    lg.info('instructions editor: %s (%d file(s) listed)', editor.mode, len(editor.table) if editor.mode != 'off' else 0)
    lg.info('ih_brain %s starting: %d notes in %s; %s; token %s', VERSION, len(st.brain._notes()), opts['brain_dir'],
            'READ-ONLY' if opts['read_only'] else 'writes allowed', 'set' if opts['api_token'] else 'NOT set (all requests refused)')
    if st.pocket and opts['pocket_interval_min']:
        lg.info('pocket sync every %d min (%s)', opts['pocket_interval_min'], 'LIVE' if opts['pocket_live'] else 'DRY')
        threading.Thread(target=st.pocket_loop, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', opts['port']), make_handler(st)).serve_forever()


if __name__ == '__main__':
    main()
