"""Non-destructive migration of meal-plan ownership."""
import json
import http.client
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import cooking
import freshdates
import main
import recipeadmin


class MealPlanIngressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = pathlib.Path(self.temp.name)
        self.legacy = {'mon': 'Example meal', 'tue': 'Eating out'}
        def plan():
            path = self.data / 'mealplan.json'
            if path.exists():
                return json.loads(path.read_text()), 'file'
            return dict(self.legacy), 'ha'
        self.st = types.SimpleNamespace(data=str(self.data), opts={'manage_plan': False}, plan=plan)

    def test_read_only_by_default_and_preserve_legacy(self):
        code, view = main.ingress_api(self.st, 'GET', '/plan', {})
        self.assertEqual(code, 200)
        self.assertEqual(view['plan']['mon'], 'Example meal')
        self.assertFalse(view['editable'])
        code, _ = main.ingress_api(self.st, 'PUT', '/plan', {'plan': view['plan'], 'revision': view['revision']})
        self.assertEqual(code, 403)
        self.assertFalse((self.data / 'mealplan.json').exists())

    def test_opt_in_saves_full_plan_and_rejects_stale_edits(self):
        self.st.opts['manage_plan'] = True
        old = main.plan_view(self.st)
        self.legacy['wed'] = 'Takeaway'
        self.assertEqual(main.ingress_api(self.st, 'PUT', '/plan', {'plan': old['plan'], 'revision': old['revision']})[0], 409)
        current = main.plan_view(self.st)
        desired = dict(current['plan'], mon='New meal')
        code, result = main.ingress_api(self.st, 'PUT', '/plan', {'plan': desired, 'revision': current['revision']})
        self.assertEqual(code, 200)
        self.assertEqual(result['source'], 'file')
        self.assertEqual(result['plan']['wed'], 'Takeaway')
        self.assertEqual(result['plan']['mon'], 'New meal')
        self.assertEqual(main.ingress_api(self.st, 'PUT', '/plan', {'plan': current['plan'], 'revision': current['revision']})[0], 409)

    def test_empty_file_plan_does_not_restore_legacy(self):
        recipeadmin.write_plan(str(self.data), {})
        fake = types.SimpleNamespace(data=str(self.data), opts={'plan_entity_prefix':'input_select.meal_', 'ha_token':'fixture', 'manage_plan':True},
                                     ha=lambda _: {'state':'Old meal'})
        plan, source = cooking.Store.plan(fake)
        self.assertEqual(source, 'file')
        self.assertTrue(all(not value for value in plan.values()))
        fake.opts['manage_plan'] = False
        plan, source = cooking.Store.plan(fake)
        self.assertEqual(source, 'ha')
        self.assertEqual(plan['mon'], 'Old meal')

    def test_ingress_allows_gateway_and_never_logs_plan(self):
        log = []
        self.st.clock = time.time
        self.st.invocation_line = lambda *args: log.append(args)
        self.st.opts['log_payloads'] = True
        handler = main.make_handler(self.st, ingress=True, ingress_ips=('127.0.0.1',))
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        self.addCleanup(conn.close)
        conn.request('GET', '/api/plan')
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())['plan']['mon'], 'Example meal')
        self.assertEqual(log, [])

    def test_plan_consumers_resolve_recipe_ids_and_titles(self):
        fake = types.SimpleNamespace(recipes=lambda:[{'slug':'example-meal'}],
            recipe=lambda slug:{'title':'Example meal','shopping':['Example ingredient'],'serves':2})
        by_id = freshdates.Publisher(fake,None).shopping_by_meal()
        self.assertEqual(by_id['example-meal'], ['Example ingredient'])
        self.assertEqual(by_id['example meal'], ['Example ingredient'])
        meta = cooking.Store.recipe_meta(fake)
        self.assertEqual(meta['example-meal']['serves'],2)
        self.assertEqual(meta['example meal']['serves'],2)


if __name__ == '__main__':
    unittest.main()
