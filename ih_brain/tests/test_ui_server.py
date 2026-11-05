"""Summary, links, recent and read endpoints of the Brain page, with disposable synthetic notes only."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import brainlib
import ui_server

NOTES = {
    'index.md': '---\ntitle: Home index\nstatus: current\nupdated: 2026-11-01\nshare: true\n---\n# Home index\nSee [[alpha]] and [[area/beta]] and [[Gamma title]].\n',
    'alpha.md': '---\ntitle: Alpha\nstatus: draft\nshare: true\n---\nAlpha body links to [[area/beta]].\n',
    'area/beta.md': '---\ntitle: Beta\nstatus: current\nshare: false\n---\n# Beta\nA **bold** private example sentence about widgets.\n```\ncode hidden\n```\n',
    'area/gamma.md': '---\ntitle: Gamma title\nstatus: draft\n---\nGamma has no links in.\n',
    'area/lonely.md': 'No front matter and nothing links here.\n',
}


class UiServer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.brain = brainlib.Brain(root / 'notes', root / 'data')
        for rel, text in NOTES.items():
            f = self.brain.base / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(text)
        old = time.time() - 30 * 86400
        os.utime(self.brain.base / 'area' / 'lonely.md', (old, old))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ui_server.make_handler(self.brain))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), self.thread.join()))
        patcher = patch.object(ui_server, 'GATEWAY_PEER', '127.0.0.1'); patcher.start(); self.addCleanup(patcher.stop)

    def get(self, path):
        with urllib.request.urlopen('http://127.0.0.1:%d/%s' % (self.server.server_port, path)) as r:
            return json.loads(r.read())

    def test_summary_counts_and_orphan_rule(self):
        s = self.get('api/summary')
        self.assertEqual((s['notes'], s['areas']), (5, 2))
        self.assertEqual(s['links'], 4)
        self.assertEqual(s['drafts'], 2)
        self.assertEqual((s['private'], s['shareable'], s['unmarked']), (1, 2, 2))
        self.assertEqual(s['changed_7d'], 4)
        # index.md has no inbound link; alpha, beta and gamma do; lonely does not.
        self.assertEqual(s['orphans'], 2)
        self.assertIn('[[', s['orphan_rule'])

    def test_links_use_graph_resolution(self):
        d = self.get('api/links?path=area/beta.md')
        self.assertEqual([r['path'] for r in d['in']], ['alpha.md', 'index.md'])
        self.assertEqual(d['out'], [])
        d = self.get('api/links?path=index.md')
        self.assertEqual([r['title'] for r in d['out']], ['Alpha', 'Beta', 'Gamma title'])
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get('api/links?path=missing.md')
        self.assertEqual(e.exception.code, 404); e.exception.close()

    def test_recent_has_date_excerpt_private_and_no_code(self):
        rows = {r['path']: r for r in self.get('api/recent')['notes']}
        beta = rows['area/beta.md']
        self.assertTrue(beta['private']); self.assertFalse(rows['alpha.md']['private'])
        self.assertIn('bold private example', beta['excerpt']); self.assertNotIn('code hidden', beta['excerpt'])
        self.assertEqual(rows['index.md']['date'], '2026-11-01')
        self.assertRegex(rows['alpha.md']['date'], r'^\d{4}-\d{2}-\d{2}$')
        order = [r['path'] for r in self.get('api/recent')['notes']]
        self.assertEqual(order[-1], 'area/lonely.md')

    def test_search_and_read_carry_private_flag(self):
        notes = self.get('api/search?q=widgets')['notes']
        self.assertEqual([n['path'] for n in notes], ['area/beta.md']); self.assertTrue(notes[0]['private'])
        r = self.get('api/read?path=area/beta.md'); self.assertTrue(r['private']); self.assertIn('content', r)

    def test_existing_routes_still_answer_and_gateway_check_stays(self):
        self.assertEqual(self.get('api/index')['count'], 5)
        self.assertEqual(len(self.get('api/graph')['links']), 4)
        self.assertEqual(len(self.get('api/notes?area=area')['notes']), 3)
        with urllib.request.urlopen('http://127.0.0.1:%d/logic.js' % self.server.server_port) as r:
            self.assertIn(b'IHBrainLogic', r.read())
        with patch.object(ui_server, 'GATEWAY_PEER', '10.0.0.1'):
            for p in ('api/summary', 'api/links?path=index.md', 'logic.js'):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    self.get(p)
                self.assertEqual(e.exception.code, 403); e.exception.close()


if __name__ == '__main__':
    unittest.main()
