import json, os, re, socket, sys, tempfile, threading, unittest, urllib.error, urllib.request
from datetime import date
APP = os.path.join(os.path.dirname(__file__), '..', 'app')
sys.path.insert(0, APP)
import ledger as L, main as M, ui_server as U

UI = os.path.join(APP, 'ui')
REC = {'shop': 'Shop', 'bought_on': '2026-11-13', 'total': 4.0, 'lines': [{'raw_name': 'CAT BISCUITS', 'unit_price': 4.0, 'line_total': 4.0, 'category': 'Pet food'}]}


def make_app(mode='live'):
    return M.App(L.open_ledger(os.path.join(tempfile.mkdtemp(), 'ih.db')), mode, 'tok', today=lambda: date(2026, 11, 20))


def serve(app, peer='127.0.0.1'):
    srv = U.make_ui_server(app, port=0, host='127.0.0.1', peer=peer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, 'http://127.0.0.1:%d' % srv.server_address[1]


def get(base, path):
    return json.load(urllib.request.urlopen(base + path))


class UiFiles(unittest.TestCase):
    def test_files_and_nav(self):
        for f in ('index.html', 'ui.css', 'ui.js', 'ui.json', 'ih-ui.css', 'ih-ui.js', 'ih-patterns.css'):
            self.assertTrue(os.path.isfile(os.path.join(UI, f)), f)
        html = open(os.path.join(UI, 'index.html')).read()
        self.assertIn('<ih-nav data-home-href="/app/32c8114c_ih_orchestrator"', html)
        info = json.load(open(os.path.join(UI, 'ui.json')))
        for k in ('title', 'purpose', 'navigation', 'access'):
            self.assertIn(k, info)

    def test_no_external_urls_no_writes(self):
        for f in os.listdir(UI):
            text = open(os.path.join(UI, f)).read()
            self.assertIsNone(re.search(r'https?://|src="//|href="//', text), f)
        js = open(os.path.join(UI, 'ui.js')).read()
        self.assertNotRegex(js, r"method\s*:|POST|innerHTML")
        for m in re.findall(r'(?:src|href)="([^"]+)"', open(os.path.join(UI, 'index.html')).read()):
            self.assertFalse(m.startswith('/') and 'ih_orchestrator' not in m, m)   # relative asset URLs only


class UiServer(unittest.TestCase):
    def test_idle_connection_does_not_block(self):
        srv, base = serve(make_app())
        idle = socket.create_connection(('127.0.0.1', srv.server_address[1]))   # opens and says nothing
        try:
            self.assertEqual(get(base, '/api/summary')['receipts'], 0)
        finally:
            idle.close(); srv.shutdown()

    def test_only_gateway_and_read_only(self):
        srv, base = serve(make_app(), peer='172.30.32.2')
        try:
            with self.assertRaises(urllib.error.HTTPError) as c:
                urllib.request.urlopen(base + '/')
            self.assertEqual(c.exception.code, 403)
        finally:
            srv.shutdown()
        srv, base = serve(make_app())
        try:
            with self.assertRaises(urllib.error.HTTPError) as c:
                urllib.request.urlopen(urllib.request.Request(base + '/api/summary', data=b'{}'))
            self.assertEqual(c.exception.code, 405)
            self.assertIn(b'ih-nav', urllib.request.urlopen(base + '/').read())
            with self.assertRaises(urllib.error.HTTPError) as c:
                urllib.request.urlopen(base + '/nope')
            self.assertEqual(c.exception.code, 404)
        finally:
            srv.shutdown()


class UiData(unittest.TestCase):
    def test_empty_database(self):
        srv, base = serve(make_app())
        try:
            self.assertEqual(get(base, '/api/summary'), {'mode': 'live', 'mode_label': 'live', 'items': 0, 'receipts': 0, 'price_changes': 0})
            self.assertEqual(get(base, '/api/receipts'), {'receipts': []})
            self.assertEqual(get(base, '/api/prices'), {'changes': []})
            self.assertEqual(get(base, '/api/due'), {'due': []})
            self.assertEqual(get(base, '/api/library?q=x')['items'], [])
        finally:
            srv.shutdown()

    def test_dry_mode_says_test_mode(self):
        srv, base = serve(make_app('dry'))
        try:
            s = get(base, '/api/summary')
            self.assertEqual((s['mode'], s['mode_label']), ('dry', 'test mode'))
        finally:
            srv.shutdown()

    def test_with_receipts(self):
        a = make_app()
        for day in ('2026-09-01', '2026-09-22', '2026-10-13', '2026-11-03'):
            a.route('POST', '/receipts', {}, dict(REC, bought_on=day), 'Bearer tok')
        a.route('POST', '/receipts', {}, dict(REC, bought_on='2026-11-14', total=5.0, lines=[dict(REC['lines'][0], unit_price=5.0, line_total=5.0)]), 'Bearer tok')
        L.add_alias(a.db, 'oat milk', 'milk_oat')
        srv, base = serve(a)
        try:
            s = get(base, '/api/summary')
            self.assertEqual((s['receipts'], s['items'], s['price_changes']), (5, 2, 1))   # biscuits + alias-only milk_oat
            r = get(base, '/api/receipts')['receipts']
            self.assertEqual((r[0]['bought_on'], r[0]['shop'], r[0]['total_p']), ('2026-11-14', 'Shop', 500))
            p = get(base, '/api/prices')['changes'][0]
            self.assertEqual((p['name'], p['first_p'], p['last_p'], p['pct']), ('CAT BISCUITS', 400, 500, 25.0))
            lib = get(base, '/api/library?q=biscuit')['items']
            self.assertEqual((lib[0]['name'], lib[0]['times_bought'], lib[0]['last_bought']), ('CAT BISCUITS', 5, '2026-11-14'))
            self.assertEqual(get(base, '/api/library?q=oat')['items'][0]['id'], 'milk_oat')
            self.assertEqual(len(get(base, '/api/library')['items']), 2)
            self.assertEqual(get(base, '/api/library?q=%25')['items'], [])   # % is literal, not a wildcard
        finally:
            srv.shutdown()

    def test_due_again(self):
        a = make_app()
        for day in ('2026-09-01', '2026-09-22', '2026-10-13', '2026-11-03'):
            a.route('POST', '/receipts', {}, dict(REC, bought_on=day), 'Bearer tok')
        srv, base = serve(a)
        try:
            due = get(base, '/api/due')['due']
            self.assertEqual((due[0]['name'], due[0]['last'], due[0]['due'], due[0]['in_days']), ('CAT BISCUITS', '2026-11-03', '2026-11-24', 4))
        finally:
            srv.shutdown()


if __name__ == '__main__':
    unittest.main()
