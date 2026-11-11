"""Instructions editor: store, access rules and the ingress routes. Temporary synthetic files only."""
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import brainlib
import instructions_api as api
import instructions_store as store_mod
import ui_server
from instructions_store import Conflict, InstructionStore, InvalidContent

ORIGINAL = '# Instructions\nOriginal\n'
USER = 'a' * 32
OTHER = 'b' * 32
READ = {api.USER_HEADER: USER}
WRITE = {api.USER_HEADER: USER, 'Content-Type': 'application/json', api.CSRF_HEADER: api.CSRF_VALUE, 'Sec-Fetch-Site': 'same-origin'}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'cfg').mkdir()
        self.path = self.root / 'cfg' / 'CLAUDE.md'; self.path.write_text(ORIGINAL); self.path.chmod(0o640)
        self.locked = self.root / 'cfg' / 'LOCKED.md'; self.locked.write_text('managed\n')
        self.work = self.root / 'data' / 'instructions'
        self.table = {'house': {'label': 'House', 'path': str(self.path), 'locked': False},
                      'claude_addon': {'label': 'Managed', 'path': str(self.locked), 'locked': True},
                      'claude_user': {'label': 'Not listed', 'path': str(self.root / 'cfg' / 'OTHER.md'), 'locked': False}}

    def editor(self, mode='edit', files=('house', 'claude_addon'), user_ids=()):
        return api.InstructionsEditor(mode, files, user_ids, str(self.work), table=self.table)

    def post(self, ed, route, payload, headers=WRITE):
        return ed.handle('POST', route, headers, body=json.dumps(payload).encode())

    def preview_and_save(self, ed, content, revision):
        code, p = self.post(ed, 'preview', {'id': 'house', 'content': content, 'revision': revision})
        self.assertEqual(code, 200, p)
        return self.post(ed, 'save', {'id': 'house', 'content': content, 'revision': revision, 'preview': p['preview']})


class StoreTests(Base):
    def setUp(self):
        super().setUp()
        self.store = InstructionStore('house', self.path, self.work)

    def test_read_gives_content_and_hash_revision(self):
        d = self.store.read()
        self.assertEqual(d['content'], ORIGINAL)
        self.assertEqual(d['revision'], hashlib.sha256(ORIGINAL.encode()).hexdigest())

    def test_save_writes_private_backup_first_and_keeps_mode(self):
        d = self.store.read(); r = self.store.save('# Instructions\nChanged\n', d['revision'])
        self.assertTrue(r['changed']); self.assertEqual(self.path.read_text(), '# Instructions\nChanged\n')
        backups = list((self.work / 'backups').iterdir())
        self.assertEqual([b.name for b in backups], [r['backup']])
        self.assertEqual(backups[0].read_text(), ORIGINAL)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.work / 'backups').stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertRegex(r['backup'], store_mod.BACKUP_NAME)
        self.assertEqual([p.name for p in self.path.parent.iterdir() if 'editor' in p.name], [])   # no temp or lock file left in the config folder

    def test_backup_failure_means_no_save(self):
        d = self.store.read()
        (self.work).mkdir(parents=True); (self.work / 'backups').write_text('not a folder')
        with self.assertRaises(OSError):
            self.store.save('changed', d['revision'])
        self.assertEqual(self.path.read_text(), ORIGINAL)

    def test_stale_revision_is_refused_and_keeps_the_other_change(self):
        d = self.store.read(); self.path.write_text('Changed elsewhere')
        with self.assertRaises(Conflict):
            self.store.save('Mine', d['revision'])
        self.assertEqual(self.path.read_text(), 'Changed elsewhere')
        self.assertFalse((self.work / 'backups').exists())

    def test_noop_save_makes_no_backup_and_no_audit(self):
        d = self.store.read()
        self.assertFalse(self.store.save(d['content'], d['revision'])['changed'])
        self.assertFalse((self.work / 'backups').exists()); self.assertEqual(store_mod.audit_entries(self.work), [])

    def test_bad_content_and_bad_revision_refused(self):
        d = self.store.read()
        for bad in ['', '  ', 'a\x00b', 'x' * (store_mod.MAX_BYTES + 1), None, 5, '\ud800']:
            with self.subTest(bad=type(bad).__name__), self.assertRaises(InvalidContent):
                self.store.save(bad, d['revision'])
        for rev in [None, '', 'abc', 5]:
            with self.subTest(rev=rev), self.assertRaises(InvalidContent):
                self.store.save('ok', rev)
        self.assertEqual(self.path.read_text(), ORIGINAL)

    def test_oversize_file_on_disk_is_not_read(self):
        self.path.write_bytes(b'x' * (store_mod.MAX_BYTES + 1))
        with self.assertRaises(InvalidContent):
            self.store.read()

    def test_symlinked_file_is_refused_for_read_and_save(self):
        target = self.root / 'target'; target.write_text('secret'); rev = self.store.read()['revision']
        self.path.unlink(); self.path.symlink_to(target)
        with self.assertRaises(InvalidContent):
            self.store.read()
        with self.assertRaises(InvalidContent):
            self.store.save('changed', rev)
        self.assertEqual(target.read_text(), 'secret'); self.assertFalse(self.store.available())

    def test_symlinked_parent_folder_is_refused(self):
        real = self.root / 'elsewhere'; real.mkdir(); (real / 'CLAUDE.md').write_text('other file')
        link = self.root / 'linked'; link.symlink_to(real)
        with self.assertRaises(InvalidContent):
            InstructionStore('house', link / 'CLAUDE.md', self.work).read()

    def test_non_regular_file_is_refused(self):
        self.path.unlink(); self.path.mkdir()
        with self.assertRaises((InvalidContent, OSError)):
            self.store.read()

    def test_symlinked_lock_and_backup_folder_are_refused(self):
        rev = self.store.read()['revision']; other = self.root / 'other'; other.write_text('original')
        self.work.mkdir(parents=True); (self.work / 'house.lock').symlink_to(other)
        with self.assertRaises(OSError):
            self.store.save('changed', rev)
        (self.work / 'house.lock').unlink()
        outside = self.root / 'outside'; outside.mkdir(); (self.work / 'backups').symlink_to(outside)
        with self.assertRaises(OSError):
            self.store.save('changed', rev)
        self.assertEqual((other.read_text(), list(outside.iterdir()), self.path.read_text()), ('original', [], ORIGINAL))

    def test_parallel_saves_only_one_wins(self):
        revision = self.store.read()['revision']; barrier = threading.Barrier(2); results = []
        def save(text):
            barrier.wait()
            try:
                InstructionStore('house', self.path, self.work).save(text, revision); results.append('saved')
            except Conflict:
                results.append('conflict')
        threads = [threading.Thread(target=save, args=(s,)) for s in ['one', 'two']]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertCountEqual(results, ['saved', 'conflict'])
        self.assertEqual(len(list((self.work / 'backups').iterdir())), 1)

    def test_backups_are_pruned_and_audit_is_capped_and_has_no_content(self):
        with patch.object(store_mod, 'MAX_BACKUPS_PER_FILE', 3), patch.object(store_mod, 'MAX_AUDIT_ENTRIES', 4):
            for i in range(6):
                self.store.save('SECRET-TEXT version %d\n' % i, self.store.read()['revision'])
        self.assertEqual(len(self.store.backups()), 3)
        entries = store_mod.audit_entries(self.work)
        self.assertEqual(len(entries), 4)
        self.assertEqual(set(entries[0]), {'time', 'file', 'bytes_before', 'bytes_after', 'backup'})
        self.assertNotIn('SECRET-TEXT', (self.work / store_mod.AUDIT_FILE).read_text())
        self.assertEqual((self.work / store_mod.AUDIT_FILE).stat().st_mode & 0o777, 0o600)

    def test_backup_names_cannot_leave_the_backup_folder(self):
        self.store.save('changed\n', self.store.read()['revision'])
        name = self.store.backups()[0]['name']
        self.assertEqual(self.store.read_backup(name)['content'], ORIGINAL)
        (self.root / 'data' / 'secret.bak').write_text('secret')
        for bad in ['../secret.bak', '../../cfg/CLAUDE.md', '/etc/passwd', name + '/..', 'other.20260101T000000Z.1.bak', None, '']:
            with self.subTest(bad=bad), self.assertRaises(InvalidContent):
                self.store.read_backup(bad)


class AccessTests(Base):
    def test_missing_or_unlisted_user_never_reaches_the_file(self):
        ed = self.editor(user_ids=[USER])
        class Trap:
            def __getattr__(self, name): raise AssertionError('store must not be touched')
        ed.stores = {'house': Trap()}
        for headers, want in [({}, 401), ({api.USER_HEADER: ''}, 401), ({api.USER_HEADER: 'x' * 200}, 401), ({api.USER_HEADER: OTHER}, 403)]:
            for method, route in [('GET', 'state'), ('GET', 'file'), ('GET', 'backups'), ('GET', 'audit'), ('POST', 'preview'), ('POST', 'save')]:
                with self.subTest(headers=headers, route=route):
                    self.assertEqual(ed.handle(method, route, dict(WRITE, **headers) if headers else {}, {'id': 'house'}, b'{}')[0], want)

    def test_listed_user_and_empty_list_are_allowed(self):
        self.assertEqual(self.editor(user_ids=[USER]).handle('GET', 'file', READ, {'id': 'house'})[0], 200)
        self.assertEqual(self.editor().handle('GET', 'file', {api.USER_HEADER: OTHER}, {'id': 'house'})[0], 200)

    def test_off_mode_serves_nothing(self):
        ed = self.editor('off')
        self.assertEqual(ed.handle('GET', 'state', READ), (200, {'mode': 'off', 'max_bytes': store_mod.MAX_BYTES, 'files': []}))
        for method, route in [('GET', 'file'), ('GET', 'backups'), ('GET', 'audit'), ('POST', 'preview'), ('POST', 'save')]:
            self.assertEqual(ed.handle(method, route, WRITE, {'id': 'house'}, b'{}')[0], 404)
        self.assertEqual(api.clean_mode('EDIT '), 'edit'); self.assertEqual(api.clean_mode('anything'), 'off'); self.assertEqual(api.clean_mode(None), 'off')

    def test_view_mode_reads_but_refuses_every_write(self):
        ed = self.editor('view'); code, d = ed.handle('GET', 'file', READ, {'id': 'house'})
        self.assertEqual((code, d['content'], d['editable']), (200, ORIGINAL, False))
        for route in ('preview', 'save'):
            self.assertEqual(self.post(ed, route, {'id': 'house', 'content': 'x', 'revision': d['revision'], 'preview': 'x'})[0], 403)
        self.assertEqual(self.path.read_text(), ORIGINAL)

    def test_only_listed_ids_exist_and_paths_are_never_accepted(self):
        ed = self.editor(files=('house',))
        self.assertEqual([f['id'] for f in ed.state()['files']], ['house'])
        for bad in ['claude_user', 'claude_addon', '../cfg/CLAUDE.md', str(self.path), '/etc/passwd', '', None, 'house/../house', 'HOUSE']:
            with self.subTest(bad=bad):
                self.assertEqual(ed.handle('GET', 'file', READ, {'id': bad})[0], 404)
                self.assertEqual(ed.handle('GET', 'backups', READ, {'id': bad})[0], 404)
        rev = ed.handle('GET', 'file', READ, {'id': 'house'})[1]['revision']
        for bad in [str(self.path), ['house'], {'a': 1}, None]:
            self.assertEqual(self.post(ed, 'preview', {'id': bad, 'content': 'x', 'revision': rev})[0], 404)
        self.assertEqual(self.post(ed, 'preview', {'id': 'house', 'content': 'x', 'revision': rev, 'path': '/etc/passwd'})[0], 400)
        self.assertEqual(self.post(ed, 'save', {'id': 'house', 'content': 'x', 'revision': rev, 'preview': 'x', 'path': 'y'})[0], 400)
        self.assertEqual(api.clean_files(['house', '/etc/passwd', 'nope']), ['house']); self.assertEqual(api.clean_files('house'), ['house'])

    def test_locked_file_is_view_only_even_in_edit_mode(self):
        ed = self.editor(); code, d = ed.handle('GET', 'file', READ, {'id': 'claude_addon'})
        self.assertEqual((code, d['editable']), (200, False))
        self.assertEqual(self.post(ed, 'preview', {'id': 'claude_addon', 'content': 'x', 'revision': d['revision']})[0], 403)
        self.assertEqual(self.post(ed, 'save', {'id': 'claude_addon', 'content': 'x', 'revision': d['revision'], 'preview': 'x'})[0], 403)
        self.assertEqual(self.locked.read_text(), 'managed\n')

    def test_writes_need_json_the_page_header_and_same_origin(self):
        ed = self.editor(); rev = ed.handle('GET', 'file', READ, {'id': 'house'})[1]['revision']
        body = {'id': 'house', 'content': 'x', 'revision': rev}
        cases = [({'Content-Type': 'text/plain'}, 415), ({'Content-Type': 'application/x-www-form-urlencoded'}, 415),
                 ({api.CSRF_HEADER: None}, 403), ({api.CSRF_HEADER: '0'}, 403), ({'Sec-Fetch-Site': 'cross-site'}, 403), ({'Sec-Fetch-Site': 'same-site'}, 403)]
        for change, want in cases:
            headers = {k: v for k, v in dict(WRITE, **change).items() if v is not None}
            with self.subTest(change=change):
                self.assertEqual(self.post(ed, 'preview', body, headers)[0], want)
        no_fetch_metadata = {k: v for k, v in WRITE.items() if k != 'Sec-Fetch-Site'}
        self.assertEqual(self.post(ed, 'preview', body, no_fetch_metadata)[0], 200)
        self.assertEqual(ed.handle('POST', 'preview', WRITE, body=b'{not json')[0], 400)
        self.assertEqual(ed.handle('POST', 'preview', WRITE, body=b'x' * (api.MAX_REQUEST_BYTES + 1))[0], 413)
        self.assertEqual(ed.handle('DELETE', 'file', WRITE, {'id': 'house'})[0], 405)

    def test_save_needs_a_preview_of_exactly_this_text_and_revision(self):
        ed = self.editor(); rev = ed.handle('GET', 'file', READ, {'id': 'house'})[1]['revision']
        code, p = self.post(ed, 'preview', {'id': 'house', 'content': 'New text\n', 'revision': rev})
        self.assertEqual((code, p['changed'], p['stale'], p['added'], p['removed']), (200, True, False, 1, 2))
        self.assertIn('+New text', p['diff']); self.assertIn('-Original', p['diff'])
        for content, token in [('New text\n', 'f' * 64), ('New text\n', None), ('Different text\n', p['preview'])]:
            self.assertEqual(self.post(ed, 'save', {'id': 'house', 'content': content, 'revision': rev, 'preview': token})[0], 428)
        self.assertEqual(self.post(self.editor(), 'save', {'id': 'house', 'content': 'New text\n', 'revision': rev, 'preview': p['preview']})[0], 428)   # another process's token
        self.assertEqual(self.path.read_text(), ORIGINAL)
        code, s = self.post(ed, 'save', {'id': 'house', 'content': 'New text\n', 'revision': rev, 'preview': p['preview']})
        self.assertEqual((code, s['changed']), (200, True)); self.assertEqual(self.path.read_text(), 'New text\n')
        self.assertEqual([b['size'] for b in ed.handle('GET', 'backups', READ, {'id': 'house'})[1]['backups']], [len(ORIGINAL)])
        audit = ed.handle('GET', 'audit', READ)[1]['entries']
        self.assertEqual((audit[0]['file'], audit[0]['bytes_before'], audit[0]['bytes_after']), ('house', len(ORIGINAL), 9))

    def test_stale_edit_is_flagged_in_preview_and_refused_on_save(self):
        ed = self.editor(); rev = ed.handle('GET', 'file', READ, {'id': 'house'})[1]['revision']
        code, p = self.post(ed, 'preview', {'id': 'house', 'content': 'Mine\n', 'revision': rev})
        self.path.write_text('Changed by someone else\n')
        code2, p2 = self.post(ed, 'preview', {'id': 'house', 'content': 'Mine\n', 'revision': rev})
        self.assertEqual((code2, p2['stale'], p2['preview']), (200, True, None))
        code3, d = self.post(ed, 'save', {'id': 'house', 'content': 'Mine\n', 'revision': rev, 'preview': p['preview']})
        self.assertEqual(code3, 409); self.assertEqual(self.path.read_text(), 'Changed by someone else\n')

    def test_unreadable_file_reports_unavailable_without_detail(self):
        self.path.unlink(); ed = self.editor()
        self.assertEqual(ed.handle('GET', 'file', READ, {'id': 'house'}), (503, {'error': 'Instructions are unavailable'}))
        self.assertFalse(ed.state()['files'][0]['available'])

    def test_fixed_table_lists_exactly_the_documented_files(self):
        self.assertEqual({k: (v['path'], v['locked']) for k, v in api.FILES.items()},
                         {'house': ('/homeassistant/CLAUDE.md', False), 'claude_user': ('/homeassistant/.claudecode/CLAUDE.user.md', False),
                          'claude_addon': ('/homeassistant/.claudecode/CLAUDE.md', True)})
        self.assertEqual(api.DEFAULT_FILES, ('house',))
        self.assertEqual(api.InstructionsEditor().mode, 'off')


class RouteTests(Base):
    """Through the real ingress web server."""
    def setUp(self):
        super().setUp()
        brain = brainlib.Brain(self.root / 'notes', self.root / 'bdata')
        self.ed = self.editor()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ui_server.make_handler(brain, self.ed))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), self.thread.join()))
        self.gateway = patch.object(ui_server, 'GATEWAY_PEER', '127.0.0.1'); self.gateway.start(); self.addCleanup(self.gateway.stop)

    def call(self, path, payload=None, headers=WRITE, raw=None):
        data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
        req = urllib.request.Request('http://127.0.0.1:%d/%s' % (self.server.server_port, path), data=data, headers=headers if data is not None else READ if headers is WRITE else headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            body = e.read(); e.close()
            return e.code, body, e.headers

    def test_page_and_assets_are_served_with_strict_headers(self):
        code, body, headers = self.call('instructions')
        self.assertEqual(code, 200); self.assertIn(b'instructions.js', body); self.assertNotIn(b'<script>', body)
        self.assertEqual(headers['Cache-Control'], 'no-store'); self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        for asset in ('instructions.js', 'instructions.css'):
            self.assertEqual(self.call(asset)[0], 200)
        self.assertIn(b'./instructions', self.call('')[1])

    def test_full_edit_through_http(self):
        code, body, _ = self.call('api/instructions/file?id=house'); d = json.loads(body)
        self.assertEqual((code, d['content']), (200, ORIGINAL))
        code, body, _ = self.call('api/instructions/preview', {'id': 'house', 'content': 'Via HTTP\n', 'revision': d['revision']}); p = json.loads(body)
        self.assertEqual(code, 200)
        code, body, _ = self.call('api/instructions/save', {'id': 'house', 'content': 'Via HTTP\n', 'revision': d['revision'], 'preview': p['preview']})
        self.assertEqual(code, 200); self.assertEqual(self.path.read_text(), 'Via HTTP\n')
        code, body, _ = self.call('api/instructions/save', {'id': 'house', 'content': 'Via HTTP\n', 'revision': d['revision'], 'preview': p['preview']})
        self.assertEqual(code, 409)   # the same request again is now stale

    def test_requests_not_from_the_ingress_gateway_are_refused(self):
        with patch.object(ui_server, 'GATEWAY_PEER', '10.0.0.1'):
            for path in ('instructions', 'instructions.js', 'api/instructions/state', 'api/instructions/file?id=house'):
                self.assertEqual(self.call(path)[0], 403)
            self.assertEqual(self.call('api/instructions/save', {'id': 'house', 'content': 'x', 'revision': 'x', 'preview': 'x'})[0], 403)
        self.assertEqual(self.path.read_text(), ORIGINAL)

    def test_no_user_header_and_traversal_and_big_bodies_are_refused(self):
        self.assertEqual(self.call('api/instructions/file?id=house', headers={})[0], 401)
        self.assertEqual(self.call('api/instructions/save', {'id': 'house'}, headers={'Content-Type': 'application/json'})[0], 401)
        for path in ('api/instructions/file?id=../cfg/CLAUDE.md', 'api/instructions/file?id=%2Fetc%2Fpasswd', 'api/instructions/../../cfg/CLAUDE.md',
                     'api/instructions/backup?id=house&name=..%2F..%2Fcfg%2FCLAUDE.md', 'instructions/../../cfg/CLAUDE.md', 'api/instructions/file'):
            with self.subTest(path=path):
                code, body, _ = self.call(path)
                self.assertIn(code, (400, 404)); self.assertNotIn(b'Original', body)
        self.assertEqual(self.call('api/instructions/preview', raw=b'x' * (api.MAX_REQUEST_BYTES + 1))[0], 413)
        self.assertEqual(self.call('api/summary', {'x': 1})[0], 404)   # no other route accepts POST

    def test_server_without_editor_has_no_instruction_routes(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), ui_server.make_handler(brainlib.Brain(self.root / 'n2', self.root / 'd2')))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        for path, payload in (('api/instructions/state', None), ('api/instructions/save', {'id': 'house'})):
            req = urllib.request.Request('http://127.0.0.1:%d/%s' % (server.server_port, path), data=json.dumps(payload).encode() if payload else None, headers=WRITE)
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req)
            self.assertEqual(e.exception.code, 404); e.exception.close()


class OptionTests(unittest.TestCase):
    def test_options_default_to_off_and_clean_bad_values(self):
        import main
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'options.json'
            f.write_text('{}'); o = main.load_options(str(f))
            self.assertEqual((o['instructions_mode'], o['instructions_files'], o['instructions_user_ids']), ('off', ['house'], []))
            f.write_text(json.dumps({'instructions_mode': 'edit', 'instructions_files': ['house', 'claude_user', '/etc/passwd'], 'instructions_user_ids': [USER, ' ']}))
            o = main.load_options(str(f))
            self.assertEqual((o['instructions_mode'], o['instructions_files'], o['instructions_user_ids']), ('edit', ['house', 'claude_user'], [USER]))
            f.write_text(json.dumps({'instructions_mode': 'write'})); self.assertEqual(main.load_options(str(f))['instructions_mode'], 'off')


if __name__ == '__main__':
    unittest.main()
