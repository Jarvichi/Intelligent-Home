import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import extras


def note(root, rel, text):
    p = os.path.join(root, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w').write(text)


class Names(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.data = tempfile.mkdtemp()
        extras.NAMES.clear(); extras.CHOICES.clear(); extras.RELAXED_TOO = False

    def test_detects_names_not_ordinary_words(self):
        note(self.d, 'a.md', 'We met Berk in Taunton. berk is not a word. Today the house is warm.\nBerk came again to Taunton.')
        note(self.d, 'b.md', 'Taunton again. See https://Example.com/Foo and `CodeThing` and ```\nBlockWord\n```')
        r = {x['name']: x for x in extras.detect_names(self.d)}
        self.assertNotIn('Berk', r)            # also appears in lower case: treated as an ordinary word
        self.assertEqual(r['Taunton']['notes'], 2)
        for skip in ('CodeThing', 'BlockWord', 'Example', 'Foo'):
            self.assertNotIn(skip, r)          # code, links are ignored
    def test_skips_private_and_mapped(self):
        note(self.d, 'a.md', '---\nshare: false\n---\nSecretname here')
        note(self.d, 'inbox/x.md', 'Inboxname here')
        note(self.d, 'b.md', 'Mappedname and Freename here')
        extras.set_names('Mappedname=X')
        r = [x['name'] for x in extras.detect_names(self.d)]
        self.assertNotIn('Secretname', r); self.assertNotIn('Inboxname', r); self.assertNotIn('Mappedname', r)
        self.assertIn('Freename', r)

    def test_save_load_and_scrub(self):
        extras.save_choices(self.data, {'Taunton': {'scrub': True, 'repl': '[town]'}, 'Keepme': {'scrub': False}}, False)
        mode = os.stat(os.path.join(self.data, extras.CHOICES_FILE)).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        extras.CHOICES.clear(); extras.load_choices(self.data)
        self.assertEqual(extras.scrub('Went to Taunton and Keepme.'), 'Went to [town] and Keepme.')
        self.assertEqual(extras.scrub('Taunton.', relaxed=True), 'Taunton.')    # relaxed notes keep names by default
        extras.save_choices(self.data, {'Taunton': {'scrub': True, 'repl': '[town]'}}, True)
        self.assertEqual(extras.scrub('Taunton.', relaxed=True), '[town].')     # unless the switch is on

    def test_regex_chars_in_name_are_literal(self):
        extras.save_choices(self.data, {'A.B': {'scrub': True, 'repl': 'X'}}, False)
        self.assertEqual(extras.scrub('A.B and AxB'), 'X and AxB')

    def test_bad_file_means_no_choices(self):
        open(os.path.join(self.data, extras.CHOICES_FILE), 'w').write('{not json')
        self.assertEqual(extras.load_choices(self.data), {})

    def test_relaxed_notes_listed(self):
        note(self.d, 'r.md', '---\nscrub: relaxed\n---\nText'); note(self.d, 'n.md', 'Text')
        self.assertEqual(extras.relaxed_notes(self.d), ['r.md'])


class NamesApi(unittest.TestCase):
    def setUp(self):
        import threading, urllib.request
        from http.server import ThreadingHTTPServer
        from pathlib import Path
        from unittest.mock import patch
        import brainlib, ui_server
        self.urllib = urllib.request
        root = tempfile.mkdtemp(); self.data = os.path.join(root, 'data')
        self.brain = brainlib.Brain(Path(root) / 'notes', Path(self.data))
        note(str(self.brain.base), 'a.md', 'Met Zorblat in Quuxville.'); note(str(self.brain.base), 'b.md', 'Zorblat again.')
        extras.NAMES.clear(); extras.CHOICES.clear(); extras.RELAXED_TOO = False
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ui_server.make_handler(self.brain, None, self.data))
        t = threading.Thread(target=self.server.serve_forever, daemon=True); t.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), t.join()))
        p = patch.object(ui_server, 'GATEWAY_PEER', '127.0.0.1'); p.start(); self.addCleanup(p.stop)

    def call(self, path, body=None):
        req = self.urllib.Request('http://127.0.0.1:%d/%s' % (self.server.server_port, path),
                                  data=None if body is None else json.dumps(body).encode(), method='GET' if body is None else 'POST')
        with self.urllib.urlopen(req) as r:
            return json.loads(r.read())

    def test_list_save_and_apply(self):
        names = {n['name']: n for n in self.call('api/names')['names']}
        self.assertEqual(names['Zorblat']['notes'], 2); self.assertFalse(names['Zorblat']['scrub'])
        out = self.call('api/names', {'names': {'Zorblat': {'scrub': True, 'repl': '[friend]'}}, 'relaxed_too': False})
        self.assertEqual(out['scrubbed'], ['Zorblat'])
        again = {n['name']: n for n in self.call('api/names')['names']}
        self.assertTrue(again['Zorblat']['scrub']); self.assertEqual(again['Zorblat']['repl'], '[friend]')
        sel, _ = extras.select_notes(str(self.brain.base))
        self.assertIn('[friend]', sel['note:a.md']); self.assertNotIn('Zorblat', sel['note:a.md'])

    def test_page_is_served(self):
        with self.urllib.urlopen('http://127.0.0.1:%d/names' % self.server.server_port) as r:
            self.assertIn(b'Names to scrub', r.read())


if __name__ == '__main__':
    unittest.main()
