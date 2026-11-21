#!/usr/bin/env python3
"""ih_tasks: one list of tasks with stages, a single approval gate, questions as a flag, and a full history. See DOCS.md.

API on IHT_PORT (bearer token; an empty token refuses everything except /healthz):
  api_token    -> the caller is a worker (or, with "actor": "check", an automated check). It can never act as the owner.
  owner_token  -> the caller is the owner (approve, reject, answer, reopen, import).
  GET  /healthz (open)   GET /status   GET /board   GET /tasks?stage=a,b&needs_input=1&waiting=owner&q=text   GET /tasks/<n>   GET /tasks/<n>/history
  GET  /labels (labels in use, with counts)   GET  /legacy?kind=todo_uid&value=...
  POST /tasks {title, description?, due?, priority? (high|normal|low), labels? (list of words), source?, legacy?, allow_duplicate?}
  POST /tasks/<n>/move {to, reason?, plan?, result?, verify_by?}     POST /tasks/<n>/plan {plan, verify_by?}
  POST /tasks/<n>/reject {comment}                                   POST /tasks/<n>/close {reason, detail?, duplicate_of?}
  POST /tasks/<n>/comment {text}                                     POST /tasks/<n>/edit {title?, description?, due?, priority?, labels?}
  POST /tasks/<n>/ask {questions, after}   POST /tasks/<n>/answer {answers, note?}   POST /tasks/<n>/withdraw {reason}
  POST /tasks/<n>/link {kind, url?, text?}   POST /tasks/<n>/legacy {legacy}   POST /import {item} (owner token)
  POST /tasks/<n>/children {title, description?}   a child task under an Approved/Implementing parent (inherits the approval; the parent waits for all children)
  POST /tasks/merge {keep, merge: [n, ...]}   GET /tasks/duplicates   POST /tasks/duplicates/dismiss {ids: [n, ...]}
The ingress page (IHT_UI_PORT, Home Assistant sign-in, admin only) uses the same routes under /api/ and always acts as the owner.
Unknown fields are refused. Task text is never logged. stdlib only."""
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mirror as MIRROR
import model as M
from store import TaskStore

VERSION = '0.6.0'
DATA = os.environ.get('IHT_DATA', '/data')
PORT = int(os.environ.get('IHT_PORT', '8818'))
UI_PORT = int(os.environ.get('IHT_UI_PORT', '8823'))
INGRESS_PEER = '172.30.32.2'
MAX_BODY = 131072
HTTP = {'bad': 400, 'forbidden': 403, 'missing': 404, 'conflict': 409}
UI_ROOT = Path(HERE) / 'ui'
ASSETS = {'/': 'index.html', '/index.html': 'index.html', '/ui.css': 'ui.css', '/ui.js': 'ui.js', '/ih-ui.css': 'ih-ui.css', '/ih-ui.js': 'ih-ui.js',
          '/ih-patterns.css': 'ih-patterns.css', '/ui.json': 'ui.json'}
TASK_POSTS = {   # action -> (allowed fields, required fields)
    'move': ({'to', 'reason', 'plan', 'result', 'verify_by'}, {'to'}),
    'plan': ({'plan', 'verify_by'}, {'plan'}),
    'reject': ({'comment'}, {'comment'}),
    'close': ({'reason', 'detail', 'duplicate_of'}, {'reason'}),
    'comment': ({'text'}, {'text'}),
    'edit': ({'title', 'description', 'due', 'priority', 'labels'}, set()),
    'ask': ({'questions', 'after'}, {'questions', 'after'}),
    'answer': ({'answers', 'note'}, {'answers'}),
    'withdraw': ({'reason'}, {'reason'}),
    'link': ({'kind', 'url', 'text'}, {'kind'}),
    'legacy': ({'legacy'}, {'legacy'}),
    'children': ({'title', 'description'}, {'title'}),
}
CREATE_FIELDS = ({'title', 'description', 'due', 'source', 'legacy', 'allow_duplicate', 'priority', 'labels'}, {'title'})
TASK_PATH = re.compile(r'/tasks/(\d{1,9})(?:/([a-z]+))?')


def load_options(path=None):
    try:
        with open(path or os.path.join(DATA, 'options.json')) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}
    if not isinstance(o, dict):
        o = {}
    text = lambda k, d='': o[k].strip() if isinstance(o.get(k), str) else d
    try:
        minutes = max(1, min(120, int(o.get('mirror_minutes', 5))))
    except (TypeError, ValueError):
        minutes = 5
    api, owner = text('api_token'), text('owner_token')
    if owner and owner == api:
        owner = ''     # one token for both would let a worker approve its own plan
    return {'api_token': api, 'owner_token': owner, 'mirror': text('mirror', 'off') if text('mirror', 'off') in ('off', 'dry', 'live') else 'off',
            'mirror_entity': text('mirror_entity'), 'mirror_minutes': minutes}


def check_fields(d, allowed, required):
    if not isinstance(d, dict):
        raise M.RuleError('bad', 'body must be a JSON object')
    extra = sorted(set(d) - allowed)
    if extra:
        raise M.RuleError('bad', 'unknown field: %s' % ', '.join(str(x)[:30] for x in extra[:3]))
    missing = sorted(required - set(d))
    if missing:
        raise M.RuleError('bad', 'missing: %s' % ', '.join(missing))


def actor_of(role, body):
    """role: 'owner' (owner token or the ingress page) or 'agent' (api_token). The body may say actor worker|check for an agent; nothing else."""
    asked = body.pop('actor', None) if isinstance(body, dict) else None
    if role == 'owner':
        if asked not in (None, 'owner'):
            raise M.RuleError('bad', 'the owner token always acts as the owner')
        return 'owner'
    if asked in (None, 'worker'):
        return 'worker'
    if asked == 'check':
        return 'check'
    raise M.RuleError('forbidden', 'this token can act as worker or check only')


def dispatch(store, status, method, path, query, body, role):
    """One request -> the reply object. Raises RuleError. `role` comes from the token (or the ingress page), never from the request body."""
    if method == 'GET':
        if path == '/status':
            return status()
        if path == '/board':
            return store.board()
        if path == '/tasks':
            q = parse_qs(query)
            one = lambda k: (q.get(k) or [''])[0]
            stages = [x for x in one('stage').split(',') if x]
            ni = one('needs_input')
            if ni not in ('', '0', '1'):
                raise M.RuleError('bad', 'needs_input must be 0 or 1')
            return {'tasks': store.list(stages or None, None if ni == '' else ni == '1', one('waiting') or None, one('q'))}
        if path == '/tasks/duplicates':
            return store.duplicates()
        if path == '/labels':
            return store.labels()
        if path == '/legacy':
            q = parse_qs(query)
            kind, value = (q.get('kind') or [''])[0], (q.get('value') or [''])[0]
            if kind not in M.LEGACY_KINDS or not value:
                raise M.RuleError('bad', 'kind must be one of: %s; value is needed' % ', '.join(M.LEGACY_KINDS))
            t = store.by_legacy(kind, value)
            if not t:
                raise M.RuleError('missing', 'no task has that old identifier')
            return t
        m = TASK_PATH.fullmatch(path)
        if m and m.group(2) is None:
            return store.get(int(m.group(1)))
        if m and m.group(2) == 'history':
            return {'history': store.history(int(m.group(1)))}
        raise M.RuleError('missing', 'not found')
    if method != 'POST':
        raise M.RuleError('missing', 'not found')
    if path == '/tasks':
        actor = actor_of(role, body)
        check_fields(body, *CREATE_FIELDS)
        if not isinstance(body.get('allow_duplicate', False), bool):
            raise M.RuleError('bad', 'allow_duplicate must be true or false')
        return store.create(body['title'], actor, body.get('description', ''), body.get('due', ''), body.get('source', ''), body.get('legacy'), body.get('allow_duplicate', False), body.get('priority', ''), body.get('labels'))
    if path == '/tasks/merge':
        actor = actor_of(role, body)
        check_fields(body, {'keep', 'merge'}, {'keep', 'merge'})
        return store.merge(body['keep'], body['merge'], actor)
    if path == '/tasks/duplicates/dismiss':
        actor = actor_of(role, body)
        check_fields(body, {'ids'}, {'ids'})
        return store.not_duplicates(body['ids'], actor)
    if path == '/import':
        if role != 'owner':
            raise M.RuleError('forbidden', 'import needs the owner token: it can place a task past the approval step')
        check_fields(body, {'item'}, {'item'})
        return store.import_task(body['item'])
    m = TASK_PATH.fullmatch(path)
    if not m or m.group(2) not in TASK_POSTS:
        raise M.RuleError('missing', 'not found')
    tid, action = int(m.group(1)), m.group(2)
    actor = actor_of(role, body)
    check_fields(body, *TASK_POSTS[action])
    b = body.get
    if action == 'move':
        return store.transition(tid, b('to'), actor, b('reason', ''), b('plan', ''), b('result', ''), b('verify_by'))
    if action == 'plan':
        return store.set_plan(tid, actor, b('plan'), b('verify_by'))
    if action == 'reject':
        return store.reject(tid, actor, b('comment'))
    if action == 'close':
        return store.close_task(tid, actor, b('reason'), b('detail', ''), b('duplicate_of'))
    if action == 'comment':
        return store.comment(tid, actor, b('text'))
    if action == 'edit':
        return store.edit(tid, actor, b('title'), b('description'), b('due'), b('priority'), b('labels'))
    if action == 'ask':
        return store.ask(tid, actor, b('questions'), b('after'))
    if action == 'answer':
        return store.answer(tid, actor, b('answers'), b('note', ''))
    if action == 'withdraw':
        return store.withdraw_question(tid, actor, b('reason'))
    if action == 'children':
        return store.create_child(tid, b('title'), actor, b('description', ''))
    if action == 'link':
        return store.add_link(tid, actor, b('kind'), b('url', ''), b('text', ''))
    return store.add_legacy(tid, actor, b('legacy'))


class Base(BaseHTTPRequestHandler):
    timeout = 15    # one request at a time per port (one database connection per thread): a stuck client must not hold the port

    def _send(self, code, body, ct='application/json; charset=utf-8'):
        body = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        if not self.headers.get('Content-Type', '').startswith('application/json'):
            raise M.RuleError('bad', 'send JSON (Content-Type: application/json)')
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise M.RuleError('bad', 'bad length')
        if n > MAX_BODY:
            raise M.RuleError('bad', 'request too large')
        try:
            d = json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            raise M.RuleError('bad', 'body must be JSON')
        if not isinstance(d, dict):
            raise M.RuleError('bad', 'body must be a JSON object')
        return d

    def _run(self, fn):
        try:
            self._send(200, fn())
        except M.RuleError as e:
            self._send(HTTP[e.code], {'error': str(e)})
        except Exception as e:
            logging.getLogger('ihtasks').info('request failed (%s)', type(e).__name__)
            self._send(500, {'error': 'internal error'})

    def log_message(self, *a):
        pass


def make_api_handler(store, status, tokens):
    def role_of(h):
        given = h.headers.get('Authorization', '')
        if not given.startswith('Bearer '):
            return None
        given = given[7:].encode()
        t = tokens()
        if t['owner_token'] and hmac.compare_digest(given, t['owner_token'].encode()):
            return 'owner'
        if t['api_token'] and hmac.compare_digest(given, t['api_token'].encode()):
            return 'agent'
        return None

    class H(Base):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == '/healthz':
                return self._send(200, b'ok', 'text/plain')
            role = role_of(self)
            if not role:
                return self._send(401, {'error': 'unauthorized'})
            self._run(lambda: dispatch(store, status, 'GET', u.path, u.query, None, role))

        def do_POST(self):
            u = urlparse(self.path)
            role = role_of(self)
            if not role:
                return self._send(401, {'error': 'unauthorized'})
            self._run(lambda: dispatch(store, status, 'POST', u.path, u.query, self._body(), role))
    return H


def make_ui_handler(store, status, peer=INGRESS_PEER):
    info = json.loads((UI_ROOT / 'ui.json').read_text())

    class H(Base):
        def _allowed(self):
            if self.client_address[0] != peer:
                self.send_error(403)
                return False
            return True

        def do_GET(self):
            if not self._allowed():
                return
            u = urlparse(self.path)
            if u.path.startswith('/api/'):
                if u.path == '/api/meta':
                    return self._send(200, {'stages': [{'id': s, 'label': M.STAGE_LABELS[s]} for s in M.STAGES],
                                            'close_reasons': [{'id': r, 'label': M.CLOSE_LABELS[r]} for r in M.CLOSE_REASONS], 'version': VERSION})
                return self._run(lambda: dispatch(store, status, 'GET', u.path[4:], u.query, None, 'owner'))
            if u.path == '/ui.json':
                return self._send(200, {k: info.get(k) for k in ('title', 'purpose', 'description', 'navigation', 'access')})
            name = ASSETS.get(u.path)
            if not name:
                return self.send_error(404)
            try:
                body = (UI_ROOT / name).read_bytes()
            except OSError:
                return self.send_error(404)
            self._send(200, body, 'text/html; charset=utf-8' if name.endswith('.html') else 'text/css; charset=utf-8' if name.endswith('.css') else 'text/javascript; charset=utf-8')

        def do_POST(self):
            if not self._allowed():
                return
            u = urlparse(self.path)
            if not u.path.startswith('/api/') or u.path == '/api/import':
                return self.send_error(404)
            self._run(lambda: dispatch(store, status, 'POST', u.path[4:], u.query, self._body(), 'owner'))
    return H


def serve(make_server, make_store, name, log):
    """Run one single-threaded server on its own long-lived thread with its own store handle (= its own database connection)."""
    def loop():
        try:
            make_server(make_store()).serve_forever()
        except Exception as e:
            log('%s server stopped (%s)' % (name, type(e).__name__))
            os._exit(1)     # let the Supervisor watchdog restart the add-on
    t = threading.Thread(target=loop, name=name, daemon=True)
    t.start()
    return t


def main():
    lg = logging.getLogger('ihtasks')
    lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__)
    sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
    lg.addHandler(sh)
    opts = load_options()
    first = TaskStore(DATA)          # opens the file and applies migrations once, on the main thread
    last_mirror = {'at': '', 'result': None}

    def status_for(store):
        return lambda: {'version': VERSION, 'counts': store.counts(), 'schema': store.db.schema_version(),
                        'mirror': {'mode': opts['mirror'], 'entity_set': bool(opts['mirror_entity']), 'last': last_mirror},
                        'owner_token_set': bool(opts['owner_token'])}

    lg.info('ih_tasks %s starting: API %s, owner token %s, mirror %s, tasks %s', VERSION, 'on' if opts['api_token'] else 'OFF (no api_token)',
            'set' if opts['owner_token'] else 'not set (approve on the page only)', opts['mirror'], sum(first.counts().values()))
    serve(lambda s: HTTPServer(('0.0.0.0', PORT), make_api_handler(s, status_for(s), lambda: opts)), lambda: TaskStore(DATA), 'api', lg.info)
    serve(lambda s: HTTPServer(('0.0.0.0', UI_PORT), make_ui_handler(s, status_for(s))), lambda: TaskStore(DATA), 'ui', lg.info)
    from ihlib import db as ihdb
    from ihlib.ha import HA
    ha = HA()
    day = None
    while True:
        try:
            if opts['mirror'] != 'off' and opts['mirror_entity']:
                last_mirror['result'] = MIRROR.run(ha, first, opts['mirror_entity'], opts['mirror'], lg.info)
                last_mirror['at'] = first.now()
            today = time.strftime('%Y-%m-%d')
            if today != day:     # once a day: a snapshot of the database next to it
                day = today
                ihdb.snapshot(first.path, os.path.join(DATA, 'backup'))
        except Exception as e:
            lg.info('background pass failed (%s)', type(e).__name__)
        time.sleep(opts['mirror_minutes'] * 60)


if __name__ == '__main__':
    main()
