"""HTTP API and the ingress page server: tokens decide who the caller is. A local test server on a free port; temp folders only."""
import json, os, shutil, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import HTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import main as APP
from store import TaskStore

WORKER, OWNER = 'worker-token-for-tests', 'owner-token-for-tests'


class Server:
    """One single-threaded server with its own store handle on its own thread, as in main.serve()."""
    def __init__(self, data, make):
        self.ready = threading.Event()
        self.data, self.make = data, make
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        self.ready.wait(5)

    def run(self):
        self.store = TaskStore(self.data)
        self.httpd = HTTPServer(('127.0.0.1', 0), self.make(self.store))
        self.port = self.httpd.server_address[1]
        self.ready.set()
        self.httpd.serve_forever()
        self.store.close()

    def stop(self):
        self.httpd.shutdown()
        self.thread.join(5)
        self.httpd.server_close()


def call(port, method, path, body=None, token=None, ctype='application/json'):
    headers = {'Content-Type': ctype} if body is not None else {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers.get('Content-Type', '').startswith('application/json') else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


class Api(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tokens = {'api_token': WORKER, 'owner_token': OWNER}
        self.srv = Server(self.dir, lambda s: APP.make_api_handler(s, lambda: {'version': APP.VERSION, 'counts': s.counts()}, lambda: self.tokens))
        self.p = self.srv.port

    def tearDown(self):
        self.srv.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_health_is_open_everything_else_needs_a_token(self):
        self.assertEqual(call(self.p, 'GET', '/healthz'), (200, b'ok'))
        self.assertEqual(call(self.p, 'GET', '/board')[0], 401)
        self.assertEqual(call(self.p, 'GET', '/board', token='wrong')[0], 401)
        self.assertEqual(call(self.p, 'POST', '/tasks', {'title': 'x'})[0], 401)
        self.assertEqual(call(self.p, 'GET', '/board', token=WORKER)[0], 200)

    def test_empty_tokens_refuse_everything(self):
        self.tokens = {'api_token': '', 'owner_token': ''}
        self.assertEqual(call(self.p, 'GET', '/board', token='')[0], 401)
        self.assertEqual(call(self.p, 'GET', '/status', token=WORKER)[0], 401)

    def test_whole_flow_over_http(self):
        code, r = call(self.p, 'POST', '/tasks', {'title': 'Fit a shelf', 'due': '2026-12-01', 'legacy': [{'kind': 'ok_number', 'value': '7'}]}, WORKER)
        self.assertEqual((code, r['task']['id'], r['task']['stage'], r['task']['created_by']), (200, 1, 'todo', 'worker'))
        self.assertEqual(call(self.p, 'POST', '/tasks/1/move', {'to': 'planning', 'plan': 'Brackets, then board.'}, WORKER)[0], 200)
        # the worker token cannot approve, whatever it claims to be
        self.assertEqual(call(self.p, 'POST', '/tasks/1/move', {'to': 'approved'}, WORKER)[0], 403)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/move', {'to': 'approved', 'actor': 'owner'}, WORKER)[0], 403)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/move', {'to': 'approved', 'actor': 'system'}, WORKER)[0], 403)
        self.assertEqual(call(self.p, 'GET', '/tasks/1', token=WORKER)[1]['stage'], 'planning')
        # question, answer (owner), approve (owner)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/ask', {'questions': [{'text': 'Oak?', 'type': 'yesno'}], 'after': 'I update the plan.'}, WORKER)[0], 200)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/ask', {'questions': [{'text': 'Oak?', 'type': 'yesno'}], 'after': 'again'}, WORKER)[0], 409)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/answer', {'answers': {'q1': True}}, WORKER)[0], 403)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/move', {'to': 'approved'}, OWNER)[0], 409)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/answer', {'answers': {'q1': True}}, OWNER)[0], 200)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/plan', {'plan': 'Oak board on brackets.'}, WORKER)[0], 200)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/move', {'to': 'approved'}, OWNER)[1]['stage'], 'approved')
        self.assertEqual(call(self.p, 'POST', '/tasks/1/move', {'to': 'implementing'}, WORKER)[0], 200)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/link', {'kind': 'pr', 'url': 'https://example.invalid/pull/2'}, WORKER)[0], 200)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/move', {'to': 'verifying'}, WORKER)[0], 200)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/move', {'to': 'done', 'result': 'ok'}, WORKER)[0], 403)            # a worker is not a check
        code, t = call(self.p, 'POST', '/tasks/1/move', {'to': 'done', 'result': 'Load test passed', 'actor': 'check'}, WORKER)
        self.assertEqual((code, t['stage']), (200, 'done'))
        code, h = call(self.p, 'GET', '/tasks/1/history', token=WORKER)
        self.assertEqual([e['kind'] for e in h['history']], ['created', 'moved', 'asked', 'answered', 'plan', 'moved', 'moved', 'link', 'moved', 'moved'])
        self.assertEqual(call(self.p, 'GET', '/legacy?kind=ok_number&value=7', token=WORKER)[1]['id'], 1)
        self.assertEqual(call(self.p, 'GET', '/legacy?kind=ok_number&value=8', token=WORKER)[0], 404)
        self.assertEqual(call(self.p, 'GET', '/status', token=WORKER)[1]['counts']['done'], 1)

    def test_list_close_comment_and_bad_requests(self):
        call(self.p, 'POST', '/tasks', {'title': 'Clean the gutters'}, WORKER)
        call(self.p, 'POST', '/tasks', {'title': 'Renew the car insurance'}, OWNER)
        self.assertEqual(call(self.p, 'GET', '/tasks/2', token=WORKER)[1]['created_by'], 'owner')
        self.assertEqual([t['id'] for t in call(self.p, 'GET', '/tasks?q=gutters&stage=todo', token=WORKER)[1]['tasks']], [1])
        self.assertEqual(call(self.p, 'GET', '/tasks?stage=later', token=WORKER)[0], 400)
        self.assertEqual(call(self.p, 'GET', '/tasks?needs_input=maybe', token=WORKER)[0], 400)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/close', {'reason': 'because'}, OWNER)[0], 400)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/close', {}, OWNER)[0], 400)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/close', {'reason': 'duplicate', 'duplicate_of': 2}, WORKER)[1]['stage'], 'closed')
        self.assertEqual(call(self.p, 'POST', '/tasks/2/comment', {'text': 'Due in March'}, OWNER)[1]['comments'][0]['actor'], 'owner')
        self.assertEqual(call(self.p, 'POST', '/tasks/2/comment', {'text': 'x', 'colour': 'red'}, OWNER)[0], 400)
        self.assertEqual(call(self.p, 'POST', '/tasks/9/comment', {'text': 'x'}, OWNER)[0], 404)
        self.assertEqual(call(self.p, 'POST', '/tasks/2/explode', {}, OWNER)[0], 404)
        self.assertEqual(call(self.p, 'GET', '/nothing', token=OWNER)[0], 404)
        self.assertEqual(call(self.p, 'POST', '/tasks', {'title': 'x'}, OWNER, ctype='text/plain')[0], 400)
        self.assertEqual(call(self.p, 'POST', '/tasks/2/move', {'to': 'planning', 'actor': 'worker'}, OWNER)[0], 400)      # the owner token is always the owner

    def test_duplicate_on_create(self):
        call(self.p, 'POST', '/tasks', {'title': 'Fix the hall light automation'}, WORKER)
        code, r = call(self.p, 'POST', '/tasks', {'title': 'Fix hall light automation'}, WORKER)
        self.assertEqual((r['duplicate_of'], r['task']['stage'], r['task']['closed_reason']), (1, 'closed', 'duplicate'))
        self.assertIsNone(call(self.p, 'POST', '/tasks', {'title': 'Fix hall light automation', 'allow_duplicate': True}, WORKER)[1]['duplicate_of'])

    def test_import_is_owner_only(self):
        item = {'title': 'Old job', 'stage': 'approved', 'plan': 'Approved ask #3', 'legacy': [{'kind': 'todo_uid', 'value': 'u1'}]}
        self.assertEqual(call(self.p, 'POST', '/import', {'item': item}, WORKER)[0], 403)
        code, r = call(self.p, 'POST', '/import', {'item': item}, OWNER)
        self.assertEqual((code, r['task']['stage'], r['existing']), (200, 'approved', False))
        self.assertTrue(call(self.p, 'POST', '/import', {'item': item}, OWNER)[1]['existing'])


class Options(unittest.TestCase):
    @staticmethod
    def _write(path, obj):
        with open(path, 'w') as f:
            json.dump(obj, f)

    def test_same_token_for_both_drops_the_owner_token(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, 'options.json')
            write = lambda o: self._write(p, o)
            write({'api_token': 'same', 'owner_token': 'same', 'mirror': 'sideways', 'mirror_minutes': 'x'})
            o = APP.load_options(p)
            self.assertEqual((o['api_token'], o['owner_token'], o['mirror'], o['mirror_minutes']), ('same', '', 'off', 5))
            write({'api_token': 'a', 'owner_token': 'b', 'mirror': 'dry', 'mirror_entity': 'todo.x', 'mirror_minutes': 500})
            o = APP.load_options(p)
            self.assertEqual((o['owner_token'], o['mirror'], o['mirror_entity'], o['mirror_minutes']), ('b', 'dry', 'todo.x', 120))
            self.assertEqual(APP.load_options(os.path.join(d, 'missing.json'))['api_token'], '')
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Page(unittest.TestCase):
    """The ingress server answers only the Supervisor ingress address and always acts as the owner."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_other_addresses_are_refused(self):
        srv = Server(self.dir, lambda s: APP.make_ui_handler(s, lambda: {}))
        try:
            self.assertEqual(call(srv.port, 'GET', '/')[0], 403)
            self.assertEqual(call(srv.port, 'GET', '/api/board')[0], 403)
            self.assertEqual(call(srv.port, 'POST', '/api/tasks', {'title': 'x'})[0], 403)
        finally:
            srv.stop()

    def test_page_assets_and_owner_actions(self):
        srv = Server(self.dir, lambda s: APP.make_ui_handler(s, lambda: {}, peer='127.0.0.1'))
        try:
            p = srv.port
            code, html = call(p, 'GET', '/')
            self.assertEqual(code, 200)
            self.assertIn(b'<ih-nav', html)
            for asset in ('/ui.js', '/ui.css', '/ih-ui.js', '/ih-ui.css', '/ih-patterns.css'):
                self.assertEqual(call(p, 'GET', asset)[0], 200, asset)
            self.assertEqual(call(p, 'GET', '/main.py')[0], 404)
            self.assertEqual(call(p, 'GET', '/../main.py')[0], 404)
            ui = call(p, 'GET', '/ui.json')[1]
            self.assertEqual(ui['title'], 'Tasks')
            self.assertNotIn('health_port', ui)
            self.assertEqual([s['id'] for s in call(p, 'GET', '/api/meta')[1]['stages']][-1], 'closed')
            self.assertEqual(call(p, 'POST', '/api/tasks', {'title': 'Paint the fence'})[1]['task']['created_by'], 'owner')
            self.assertEqual(call(p, 'POST', '/api/tasks/1/move', {'to': 'planning', 'plan': 'p'})[0], 403)      # the page is the owner, not a worker
            self.assertEqual(call(p, 'POST', '/api/tasks/1/close', {'reason': 'dropped'})[1]['stage'], 'closed')
            self.assertEqual(call(p, 'POST', '/api/tasks/1/move', {'to': 'todo', 'reason': 'again'})[1]['stage'], 'todo')
            self.assertEqual(call(p, 'POST', '/api/import', {'item': {'title': 'x', 'stage': 'todo'}})[0], 404)
            self.assertEqual(call(p, 'GET', '/api/board')[1]['total'], 1)
        finally:
            srv.stop()


if __name__ == '__main__':
    unittest.main()
