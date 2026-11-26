import http.client, json, os, re, socket, sys, tempfile, threading, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import ui_server

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app', 'ui')


class FakeEng:
    def __init__(self, last=None):
        self.opts = {'mode': 'dry', 'api_token': 'SECRET-TOKEN-XYZ', 'history_days': 14, 'rooms': ['climate.secret_room']}
        self.last = last or {}


LAST = {'at': '2026-11-25T01:00:00+00:00', 'version': '0.2.0', 'mould_pct': 70.0, 'mould_hours': 2.0,
        'rooms': {'climate.office': {'room': 'office', 'current': 18.0, 'target': 21.0, 'min_per_deg': 25.0, 'runs': 4,
                                     'minutes_to_target': 75, 'frozen': True, 'frozen_why': 'stuck', 'entity_leak': 'x'}},
        'frozen': [{'room': 'office', 'entity': 'climate.office', 'why': 'stuck', 'since': '2026-11-24T10:00:00+00:00'}],
        'mould': {'sensor.hall_humidity': {'room': 'hall_humidity', 'level': 'watch', 'minutes_above': 30, 'now': 74.0}}}


class Files(unittest.TestCase):
    def test_files_exist(self):
        for f in ('index.html', 'ui.css', 'ui.js', 'ih-ui.css', 'ih-ui.js', 'ih-patterns.css', 'ui.json'):
            self.assertTrue(os.path.isfile(os.path.join(UI, f)), f)

    def test_nav_and_no_external_urls(self):
        html = open(os.path.join(UI, 'index.html')).read()
        self.assertIn('<ih-nav data-home-href="/app/32c8114c_ih_orchestrator"', html)
        for f in ('index.html', 'ui.js', 'ui.css'):
            txt = open(os.path.join(UI, f)).read()
            self.assertFalse(re.search(r'(src|href)=["\']https?://|url\(https?://|fetch\(["\']https?://', txt), f)
        self.assertFalse(re.search(r'(src|href)=["\']/', html.replace('href="/app/32c8114c_ih_orchestrator"', '')))

    def test_ui_json(self):
        j = json.load(open(os.path.join(UI, 'ui.json')))
        for k in ('title', 'purpose', 'navigation', 'access'):
            self.assertIn(k, j)


class Data(unittest.TestCase):
    def test_empty_state(self):
        d = ui_server.public_data(FakeEng(), '0.2.0')
        self.assertFalse(d['ran'])
        self.assertEqual((d['rooms'], d['frozen'], d['mould']), ([], [], []))

    def test_no_secrets_or_options(self):
        s = json.dumps(ui_server.public_data(FakeEng(LAST), '0.2.0'))
        self.assertNotIn('SECRET-TOKEN-XYZ', s)
        self.assertNotIn('secret_room', s)
        self.assertNotIn('api_token', s)
        self.assertNotIn('entity_leak', s)
        self.assertNotIn('climate.', s)
        d = json.loads(s)
        self.assertEqual(d['rooms'][0]['minutes_to_target'], 75)
        self.assertEqual(d['frozen'][0]['since'], '2026-11-24T10:00:00+00:00')
        self.assertEqual(d['mould'][0]['level'], 'watch')


class Server(unittest.TestCase):
    def setUp(self):
        self.srv = ui_server.make_server(FakeEng(LAST), '0.2.0', 0, '127.0.0.1')
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def get(self, path):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        c.request('GET', path)
        r = c.getresponse()
        return r.status, r.read()

    def test_non_gateway_gets_403(self):
        self.assertEqual(self.get('/api/insights')[0], 403)
        self.assertEqual(self.get('/')[0], 403)

    def test_gateway_served_and_idle_connection_does_not_block(self):
        old = ui_server.PEER
        ui_server.PEER = '127.0.0.1'
        try:
            idle = socket.create_connection(('127.0.0.1', self.port))   # opened, never sends anything
            try:
                st, body = self.get('/api/insights')
                self.assertEqual(st, 200)
                self.assertEqual(json.loads(body)['rooms'][0]['room'], 'office')
                self.assertEqual(self.get('/')[0], 200)
                self.assertEqual(self.get('/nope')[0], 404)
            finally:
                idle.close()
        finally:
            ui_server.PEER = old


if __name__ == '__main__':
    unittest.main()


class Since(unittest.TestCase):
    def test_reading_since(self):
        import insights as I
        from datetime import datetime, timedelta, timezone
        t0 = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
        mk = lambda m, cur: {'t': t0 + timedelta(minutes=m), 'state': 'heat', 'cur': cur}
        self.assertEqual(I.reading_since([mk(0, 17), mk(10, 18), mk(20, 18), mk(30, 18)]), (t0 + timedelta(minutes=10)).isoformat())
        self.assertIsNone(I.reading_since([]))
