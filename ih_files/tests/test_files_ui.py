import http.client
import json
import os
import re
import socket
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'app'))
import files_info  # noqa: E402
import ui_server  # noqa: E402

UI = os.path.join(ROOT, 'app', 'ui')
SECRET = 'S3cretPassw0rd'

CONF = """[global]
  workgroup = WORKGROUP
  smb ports = 445
  hosts allow = 127.0.0.1 10.0.0.0/8
[andrew]
  path = /share/files/andrew
  valid users = andrew
  read only = no
[family]
  comment = Family folder
  path = /share/files/family
  valid users = andrew claire rob
  read only = no
[docs]
  path = /share/docs
  valid users = andrew claire
  read only = yes
  write list = andrew
"""
NAMES = "andrew:1000:\nclaire:1001:\nrob:1002:\n"
PROCS = """
Samba version 4.19
PID     Username     Group        Machine                                   Protocol Version  Encryption           Signing
----------------------------------------------------------------------------------------------------------------------------------------
1234    claire       claire       10.0.0.7 (ipv4:10.0.0.7:5555)             SMB3_11           -                    partial(AES-128-CMAC)
"""
SHARES = """
Service      pid     Machine       Connected at                     Encryption   Signing
---------------------------------------------------------------------------------------------
family       1234    10.0.0.7      Tue Nov 25 10:15:03 2026 GMT     -            -
"""


def fake_status():
    return files_info.build_status(CONF, NAMES, PROCS, SHARES, True, {'total': 100, 'used': 40, 'free': 60})


class Parsing(unittest.TestCase):
    def test_shares_and_access(self):
        c = files_info.parse_smb_conf(CONF)
        self.assertEqual(c['port'], 445)
        self.assertEqual([s['name'] for s in c['shares']], ['andrew', 'family', 'docs'])
        fam = c['shares'][1]
        self.assertEqual(fam['path'], '/share/files/family')
        self.assertTrue(all(a['mode'] == 'read-write' for a in fam['access']))
        docs = {a['user']: a['mode'] for a in c['shares'][2]['access']}
        self.assertEqual(docs, {'andrew': 'read-write', 'claire': 'read-only'})

    def test_sessions(self):
        s = files_info.parse_smbstatus(PROCS, SHARES)
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]['share'], s[0]['user']), ('family', 'claire'))
        self.assertIn('Nov 25 10:15:03 2026', s[0]['since'])

    def test_status_shape(self):
        j = fake_status()
        self.assertTrue(j['healthy'])
        self.assertEqual(j['version'], files_info.VERSION)
        self.assertEqual([l['name'] for l in j['logins']], ['andrew', 'claire', 'rob'])
        self.assertTrue(j['sessions_known'])

    def test_no_secret_text_possible(self):
        # Hostile input: password-like lines in the pdbedit/smb.conf text must not be copied through.
        conf = CONF + "[x]\n  path = /p\n  smb passwd file = /nope\n  password = %s\n" % SECRET
        names = "andrew:1000:\nandrew:LM:%s:NT:%s\n" % (SECRET, SECRET)
        j = files_info.build_status(conf, names, PROCS, SHARES, True, None)
        self.assertNotIn(SECRET, json.dumps(j))
        for key in ('password', 'hash', 'token'):
            self.assertNotIn(key, json.dumps(j).lower().replace('login_enabled', ''))


class Files(unittest.TestCase):
    def test_assets(self):
        for f in ('index.html', 'ui.css', 'ui.js', 'ui.json', 'ih-ui.css', 'ih-ui.js', 'ih-patterns.css'):
            self.assertTrue(os.path.isfile(os.path.join(UI, f)), f)

    def test_nav_and_no_external_urls(self):
        html = open(os.path.join(UI, 'index.html')).read()
        self.assertIn('<ih-nav', html)
        self.assertIn('data-home-href="/app/32c8114c_ih_orchestrator"', html)
        for f in ('index.html', 'ui.css', 'ui.js'):
            text = open(os.path.join(UI, f)).read()
            self.assertIsNone(re.search(r'(https?:)?//[a-z0-9.-]+\.[a-z]{2,}', text.replace('w3.org', '')), f)
        js = open(os.path.join(UI, 'ui.js')).read()
        self.assertNotIn('innerHTML', js)
        self.assertNotIn("fetch('/", js)

    def test_ui_json(self):
        j = json.load(open(os.path.join(UI, 'ui.json')))
        for k in ('title', 'purpose', 'navigation', 'access'):
            self.assertIn(k, j)

    def test_version_matches_config(self):
        cfg = open(os.path.join(ROOT, 'config.yaml')).read()
        self.assertIn('version: "%s"' % files_info.VERSION, cfg)
        self.assertIn('ingress: true', cfg)
        self.assertIn('panel_admin: true', cfg)


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ui_server.make_server(0, ('127.0.0.1',), fake_status, '127.0.0.1')
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path, method='GET'):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        c.request(method, path)
        r = c.getresponse()
        return r.status, r.read()

    def test_idle_connection_does_not_block(self):
        idle = socket.create_connection(('127.0.0.1', self.port))
        try:
            code, body = self.get('/api/status')
            self.assertEqual(code, 200)
        finally:
            idle.close()

    def test_status_endpoint(self):
        code, body = self.get('/api/status')
        j = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(j['sessions'][0]['user'], 'claire')
        self.assertIsInstance(j['checked_at'], int)
        self.assertEqual(j['disk']['free'], 60)

    def test_prefixed_path_and_assets(self):
        self.assertEqual(self.get('/api/hassio_ingress/abc/api/status')[0], 200)
        code, body = self.get('/')
        self.assertEqual(code, 200)
        self.assertIn(b'<ih-nav', body)
        self.assertEqual(self.get('/ui.js')[0], 200)
        self.assertEqual(self.get('/etc/passwd')[0], 404)

    def test_read_only(self):
        self.assertEqual(self.get('/api/status', 'POST')[0], 405)

    def test_only_gateway_allowed(self):
        srv = ui_server.make_server(0, ('172.30.32.2',), fake_status, '127.0.0.1')
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection('127.0.0.1', srv.server_address[1], timeout=5)
            c.request('GET', '/api/status')
            self.assertEqual(c.getresponse().status, 403)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == '__main__':
    unittest.main()
