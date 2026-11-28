"""0.10.0: raised by, links at creation, the shared field list (New task panel = task page = API), single Save, Work this now. Made-up tasks, temp folders only."""
import json, os, re, shutil, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as APP
import model as M
import store as ST
from ihlib import db as ihdb
from test_api import OWNER, WORKER, Server, call
from test_store import Case

UI = Path(__file__).resolve().parents[1] / 'app' / 'ui'


class RaisedBy(Case):
    def test_defaults_follow_the_caller(self):
        for actor, who in (('worker', 'claude'), ('check', 'claude'), ('owner', 'owner'), ('system', 'system')):
            self.assertEqual(self.s.create('Task by ' + actor, actor, allow_duplicate=True)['task']['raised_by'], who)

    def test_explicit_name_and_limits(self):
        t = self.s.create('Fit a shelf', 'worker', raised_by='Persona A')['task']
        self.assertEqual((t['raised_by'], t['created_by']), ('Persona A', 'worker'))
        with self.assertRaises(M.RuleError):
            self.s.create('x', 'worker', raised_by='y' * 41)
        with self.assertRaises(M.RuleError):
            self.s.create('x', 'worker', raised_by=5)
        self.assertEqual(self.s.create('Blank name', 'worker', raised_by='  ')['task']['raised_by'], 'claude')

    def test_summary_and_child_carry_it(self):
        a = self.s.create('Parent', 'worker', raised_by='Someone')['task']['id']
        self.s.transition(a, 'planning', 'worker', plan='p'); self.s.transition(a, 'approved', 'owner')
        c = self.s.create_child(a, 'Child', 'worker')
        self.assertEqual(c['raised_by'], 'Someone')
        self.assertIn('Someone', [t['raised_by'] for t in self.s.list()])

    def test_old_rows_get_their_created_by(self):
        path = os.path.join(tempfile.mkdtemp(), 'old.db')
        old = ihdb.open(path, ST.MIGRATIONS[:17])
        with old.tx() as con:
            con.execute("INSERT INTO tasks(title, stage, created_by, created_at, updated_at) VALUES('Old one', 'todo', 'owner', 't', 't')")
            con.execute("INSERT INTO tasks(title, stage, created_by, created_at, updated_at) VALUES('Old two', 'todo', 'worker', 't', 't')")
        old.close()
        s = ST.TaskStore(os.path.dirname(path), path=path)
        self.assertEqual([t['raised_by'] for t in s.list()], ['owner', 'worker'])
        self.assertEqual(s.db.schema_version(), len(ST.MIGRATIONS))
        self.assertEqual(s.create('New', 'worker')['task']['raised_by'], 'claude')
        s.close()
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def test_no_name_date_lines_are_written_into_the_description(self):
        t = self.s.create('Fit a shelf', 'owner', description='Two coats.', raised_by='Someone')['task']
        self.assertEqual(t['description'], 'Two coats.')


class LinksAtCreation(Case):
    def mk(self, title):
        return self.s.create(title, 'worker', allow_duplicate=True)['task']['id']

    def test_links_are_set_with_history_on_both_tasks(self):
        a, b, c = self.mk('Buy paint'), self.mk('Buy brush'), self.mk('Garden plan')
        r = self.s.create('Paint the shed', 'worker', links={'blocked_by': [a], 'depends_on': [b], 'relates_to': [c]})
        t = r['task']
        self.assertEqual(sorted((x['kind'], x['task']) for x in t['relations']), [('blocked_by', a), ('depends_on', b), ('relates_to', c)])
        self.assertTrue(t['blocked'])
        self.assertIn('related', [h['kind'] for h in self.s.get(a)['history']])

    def test_missing_task_refuses_everything(self):
        a = self.mk('Buy paint')
        before = len(self.s.list())
        with self.assertRaises(M.RuleError) as e:
            self.s.create('Paint the shed', 'worker', links={'blocked_by': [a, 999]})
        self.assertEqual(e.exception.code, 'missing')
        self.assertEqual(len(self.s.list()), before)

    def test_bad_values(self):
        for bad in ('3', [True], [0], [1.5], ['2'], list(range(1, 14))):
            with self.assertRaises(M.RuleError):
                self.s.create('x', 'worker', links={'blocked_by': bad})
        a = self.mk('Buy paint')
        self.assertEqual(len(self.s.create('Twice', 'worker', links={'relates_to': [a, a]})['task']['relations']), 1)


class HttpWave1(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tokens = {'api_token': WORKER, 'owner_token': OWNER}
        self.srv = Server(self.dir, lambda s: APP.make_api_handler(s, lambda: {}, lambda: self.tokens))
        self.p = self.srv.port

    def tearDown(self):
        self.srv.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_existing_callers_unchanged_and_raised_by_default(self):
        code, r = call(self.p, 'POST', '/tasks', {'title': 'Old style caller', 'source': 'claude', 'priority': 'high'}, WORKER)
        self.assertEqual((code, r['task']['raised_by'], r['task']['created_by']), (200, 'claude', 'worker'))
        code, r = call(self.p, 'POST', '/tasks', {'title': 'Named', 'raised_by': 'Persona A'}, WORKER)
        self.assertEqual(r['task']['raised_by'], 'Persona A')
        self.assertEqual(call(self.p, 'POST', '/tasks', {'title': 'Owner made'}, OWNER)[1]['task']['raised_by'], 'owner')
        self.assertEqual(call(self.p, 'POST', '/tasks', {'title': 'Bad', 'raised_by': 7}, WORKER)[0], 400)
        self.assertEqual(call(self.p, 'POST', '/tasks/1/edit', {'raised_by': 'Else'}, WORKER)[0], 400)     # attribution is not edited later

    def test_links_in_the_create_body(self):
        a = call(self.p, 'POST', '/tasks', {'title': 'Buy paint'}, WORKER)[1]['task']['id']
        code, r = call(self.p, 'POST', '/tasks', {'title': 'Paint the shed', 'blocked_by': [a], 'relates_to': [a]}, WORKER)
        self.assertEqual((code, r['task']['blocked'], r['task']['blocked_by']), (200, True, a))
        self.assertEqual(call(self.p, 'POST', '/tasks', {'title': 'Another job', 'blocked_by': [999]}, WORKER)[0], 404)
        self.assertEqual(call(self.p, 'POST', '/tasks', {'title': 'Third job', 'blocked_by': 'x'}, WORKER)[0], 400)
        self.assertEqual(len(call(self.p, 'GET', '/tasks', token=WORKER)[1]['tasks']), 2)

    def test_run_now_is_owner_only_and_logged(self):
        n = call(self.p, 'POST', '/tasks', {'title': 'Fix the gate'}, WORKER)[1]['task']['id']
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/run_now' % n, {}, WORKER)[0], 403)
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/run_now' % n, {'on': True, 'actor': 'check'}, WORKER)[0], 403)
        code, t = call(self.p, 'POST', '/tasks/%d/run_now' % n, {}, OWNER)
        self.assertEqual((code, t['run_now']), (200, True))
        self.assertEqual([h['text'] for h in t['history'] if h['kind'] == 'run_now'], ['Work this now: requested by the owner.'])
        self.assertTrue(call(self.p, 'GET', '/tasks', token=WORKER)[1]['tasks'][0]['run_now'])
        call(self.p, 'POST', '/tasks/%d/run_now' % n, {}, OWNER)                                          # asking twice adds no second line
        self.assertEqual(sum(1 for h in call(self.p, 'GET', '/tasks/%d' % n, token=OWNER)[1]['history'] if h['kind'] == 'run_now'), 1)
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/run_now' % n, {'on': 'yes'}, OWNER)[0], 400)
        code, t = call(self.p, 'POST', '/tasks/%d/run_now' % n, {'on': False}, WORKER)                    # a worker may clear it (the run started)
        self.assertEqual((code, t['run_now']), (200, False))

    def test_run_now_rules(self):
        n = call(self.p, 'POST', '/tasks', {'title': 'Fix the gate'}, WORKER)[1]['task']['id']
        call(self.p, 'POST', '/tasks/%d/move' % n, {'to': 'planning', 'plan': 'p'}, WORKER)
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/run_now' % n, {}, OWNER)[0], 409)               # Planning waits for the owner, nothing for a worker
        call(self.p, 'POST', '/tasks/%d/reject' % n, {'comment': 'again'}, OWNER)                        # revise flag: the worker has something to do
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/run_now' % n, {}, OWNER)[1]['run_now'], True)
        t = call(self.p, 'POST', '/tasks/%d/plan' % n, {'plan': 'p2'}, WORKER)[1]
        call(self.p, 'POST', '/tasks/%d/move' % n, {'to': 'approved'}, OWNER)
        self.assertFalse(call(self.p, 'GET', '/tasks/%d' % n, token=OWNER)[1]['run_now'])                 # a move clears it
        call(self.p, 'POST', '/tasks/%d/run_now' % n, {}, OWNER)
        self.assertTrue(call(self.p, 'GET', '/tasks/%d' % n, token=OWNER)[1]['run_now'])
        call(self.p, 'POST', '/tasks/%d/ask' % n, {'questions': [{'text': 'Oak?', 'type': 'yesno'}], 'after': 'a'}, WORKER)
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/run_now' % n, {'on': False}, OWNER)[1]['run_now'], False)
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/run_now' % n, {}, OWNER)[0], 409)               # an open question: the owner is the one to act
        self.assertEqual(call(self.p, 'POST', '/tasks/9/run_now', {}, OWNER)[0], 404)
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/close' % n, {'reason': 'dropped', 'detail': 'x'}, WORKER)[0], 200)
        self.assertEqual(call(self.p, 'POST', '/tasks/%d/run_now' % n, {}, OWNER)[0], 409)               # closed


class PageWave1(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.srv = Server(self.dir, lambda s: APP.make_ui_handler(s, lambda: {}, peer='127.0.0.1'))
        self.p = self.srv.port

    def tearDown(self):
        self.srv.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def post(self, path, body, headers):
        import urllib.request
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.p, path), data=json.dumps(body).encode(), method='POST', headers=dict({'Content-Type': 'application/json'}, **headers))
        return json.loads(urllib.request.urlopen(req, timeout=5).read())

    def test_the_signed_in_user_raises_the_task(self):
        r = self.post('/api/tasks', {'title': 'From the page'}, {'X-Remote-User-Display-Name': 'Test User'})
        self.assertEqual(r['task']['raised_by'], 'Test User')
        r = self.post('/api/tasks', {'title': 'Name only', 'raised_by': 'Ignored'}, {'X-Remote-User-Name': 'testuser'})
        self.assertEqual(r['task']['raised_by'], 'testuser')                           # the page cannot claim to be someone else
        self.assertEqual(self.post('/api/tasks', {'title': 'No header'}, {})['task']['raised_by'], 'owner')

    def test_meta_carries_the_field_list_and_the_user(self):
        import urllib.request
        req = urllib.request.Request('http://127.0.0.1:%d/api/meta' % self.p, headers={'X-Remote-User-Display-Name': 'Test User'})
        meta = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertEqual(meta['user'], 'Test User')
        self.assertEqual([f['id'] for f in meta['fields']], [f['id'] for f in M.TASK_FIELDS])


class SharedFieldList(unittest.TestCase):
    """The 'maintain the modal' rule: panel, task page and API are all driven by model.TASK_FIELDS."""
    def test_api_accepts_exactly_the_listed_create_fields(self):
        allowed, required = APP.CREATE_FIELDS
        self.assertEqual(allowed, {f['id'] for f in M.TASK_FIELDS if f['create']} | set(M.API_ONLY_CREATE))
        self.assertEqual(required, {'title'})
        self.assertFalse(set(M.API_ONLY_CREATE) & {f['id'] for f in M.TASK_FIELDS})

    def test_api_edit_accepts_exactly_the_listed_edit_fields(self):
        self.assertEqual(APP.TASK_POSTS['edit'][0], {f['id'] for f in M.TASK_FIELDS if f['edit']})

    def test_every_field_is_wired_to_the_store(self):
        import inspect
        create = set(inspect.signature(ST.TaskStore.create).parameters)
        edit = set(inspect.signature(ST.TaskStore.edit).parameters)
        for f in M.TASK_FIELDS:
            if f['create']:
                self.assertTrue(f['id'] in create or f['id'] in M.LINK_FIELDS, f['id'])
            if f['edit']:
                self.assertIn(f['id'], edit, f['id'])
            self.assertIn(f['type'], ('text', 'textarea', 'date', 'select', 'labels', 'tasks', 'readonly'))
        self.assertEqual(len({f['id'] for f in M.TASK_FIELDS}), len(M.TASK_FIELDS))

    def test_page_builds_both_forms_from_the_list_and_hardcodes_no_field(self):
        page = (UI / 'index.html').read_text()
        js = (UI / 'ui.js').read_text()
        self.assertIn('id="new-fields"', page)
        for f in M.TASK_FIELDS:
            self.assertNotIn('id="new-%s"' % f['id'], page)          # no per-field markup in the panel
        self.assertIn("fieldsFor('create')", js)
        self.assertIn("fieldsFor('edit')", js)
        self.assertIn('state.meta.fields', js)
        for old in ('Save priority', 'Save labels', 'Save comment', 'd-labels-list'):
            self.assertNotIn(old, js)

    def test_docs_name_every_field(self):
        docs = (Path(__file__).resolve().parents[1] / 'DOCS.md').read_text()
        for f in M.TASK_FIELDS:
            if f['create']:
                self.assertIn('`%s' % f['id'], docs, f['id'])

    def test_details_and_comment_have_their_own_save_actions(self):
        js = (UI / 'ui.js').read_text()
        self.assertEqual(len(re.findall(r"text: 'Save details'", js)), 1)
        self.assertEqual(len(re.findall(r"text: 'Add comment'", js)), 1)
        self.assertIn("api('POST', '/tasks/' + t.id + '/edit', body)", js)
        self.assertIn('save.disabled', js)
        self.assertIn("api('POST', '/tasks/' + t.id + '/run_now'", js)


if __name__ == '__main__':
    unittest.main()
