"""Staple text edits on a temporary data folder with placeholder items only."""
import json
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import main
import recipeadmin
import staples

SEED = {'groups': {'first': {'name': 'First group', 'items': ['Widgets tray x8 (4 packs)', 'Gadget']},
                   'second': {'name': 'Second group', 'items': ['Widgets tray x8 (4 packs)', 'Sprocket x2']}}}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = pathlib.Path(self.temp.name)
        self.file = self.data / 'staples.json'
        self.file.write_text(json.dumps(SEED))

    def items(self, group):
        return json.loads(self.file.read_text())['groups'][group]['items']

    def backups(self):
        folder = self.data / staples.BACKUP_DIR
        return sorted(folder.iterdir()) if folder.exists() else []


class Rename(Base):
    def test_read(self):
        self.assertEqual(staples.read(str(self.data)), SEED)
        self.file.write_text('not json')
        self.assertEqual(staples.read(str(self.data)), {'groups': {}})

    def test_rename_in_every_group_with_backup(self):
        ok, r = staples.rename(str(self.data), 'Widgets tray x8 (4 packs)', '  Widgets   x4 ')
        self.assertTrue(ok)
        self.assertEqual(r, {'changed': ['first', 'second'], 'old': 'Widgets tray x8 (4 packs)', 'new': 'Widgets x4'})
        self.assertEqual(self.items('first'), ['Widgets x4', 'Gadget'])
        self.assertEqual(self.items('second'), ['Widgets x4', 'Sprocket x2'])
        self.assertEqual([json.loads(b.read_text()) for b in self.backups()], [SEED])
        self.assertEqual(recipeadmin.shop(str(self.data), [], ['first'], write_ledger=False)['staples'], ['Widgets x4', 'Gadget'])

    def test_one_group_only_and_case(self):
        ok, r = staples.rename(str(self.data), 'gadget', 'Gadget x2', group='first')
        self.assertTrue(ok)
        self.assertEqual(r['changed'], ['first'])
        self.assertEqual(self.items('first')[1], 'Gadget x2')
        ok, _ = staples.rename(str(self.data), 'Widgets tray x8 (4 packs)', 'Widgets x4', group='second')
        self.assertTrue(ok)
        self.assertEqual(self.items('first')[0], 'Widgets tray x8 (4 packs)')

    def test_rejected_edits_write_nothing(self):
        before = self.file.read_text()
        for old, new, group in (('Missing', 'X', None), ('Gadget', '', None), ('Gadget', 'Gadget', None), ('Gadget', 'a\nb', None),
                                ('Gadget', 'x' * (staples.TEXT_MAX + 1), None), ('Gadget', 'X', 'nowhere'), ('Gadget', 'X', 'second'),
                                ('Gadget', 'widgets tray x8 (4 packs)', None), ('', 'X', None)):
            ok, why = staples.rename(str(self.data), old, new, group)
            self.assertFalse(ok, (old, new, group))
            self.assertTrue(why)
        self.assertEqual(self.file.read_text(), before)
        self.assertEqual(self.backups(), [])

    def test_no_file(self):
        self.file.unlink()
        self.assertEqual(staples.rename(str(self.data), 'Gadget', 'X'), (False, ['no staples file']))

    def test_ledger_entry_follows_the_new_text(self):
        (self.data / 'meal_items.json').write_text(json.dumps({'gadget': 123.0, 'other': 5.0}))
        self.assertTrue(staples.rename(str(self.data), 'Gadget', 'Gadget x2')[0])
        self.assertEqual(json.loads((self.data / 'meal_items.json').read_text()), {'gadget': 123.0, 'other': 5.0, 'gadget x2': 123.0})

    def test_old_backups_are_trimmed(self):
        for n in range(staples.BACKUPS_KEEP + 3):
            self.assertTrue(staples.rename(str(self.data), 'Gadget' if n == 0 else 'Gadget %d' % (n - 1), 'Gadget %d' % n)[0])
        self.assertEqual(len(self.backups()), staples.BACKUPS_KEEP)


class HTTP(Base):
    def setUp(self):
        super().setUp()
        self.st = types.SimpleNamespace(data=str(self.data), opts={}, clock=time.time, authed=lambda h: h == 'Bearer fixture',
                                        stopped=lambda: False, rate_ok=lambda: True, invocation_line=lambda *a: None)
        handler = main.make_handler(self.st); handler.log_message = lambda *a: None
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, method, path, body=None, authed=True):
        request = urllib.request.Request('http://127.0.0.1:%d%s' % (self.server.server_port, path), method=method,
                                         data=None if body is None else json.dumps(body).encode(), headers={'Authorization': 'Bearer fixture'} if authed else {})
        try: response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as e: response = e
        with response: return response.status, json.load(response)

    def test_read_and_rename_need_the_token(self):
        self.assertEqual(self.request('GET', '/staples', authed=False)[0], 401)
        self.assertEqual(self.request('POST', '/staples/rename', {'old': 'Gadget', 'new': 'X'}, authed=False)[0], 401)
        self.assertEqual(self.items('first')[1], 'Gadget')

    def test_read_rename_and_reject(self):
        self.assertEqual(self.request('GET', '/staples'), (200, SEED))
        code, body = self.request('POST', '/staples/rename', {'old': 'Gadget', 'new': 'Gadget x2'})
        self.assertEqual((code, body['ok'], body['changed']), (200, True, ['first']))
        self.assertEqual(self.items('first')[1], 'Gadget x2')
        code, body = self.request('POST', '/staples/rename', {'old': 'Missing', 'new': 'X'})
        self.assertEqual((code, body), (422, {'ok': False, 'errors': ['no such staple']}))


if __name__ == '__main__':
    unittest.main()
