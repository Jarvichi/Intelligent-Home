"""Title display and ingress asset coverage with disposable notes only."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import brainlib
import ui_server

class ReaderTitles(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        self.brain=brainlib.Brain(root/'notes',root/'data')
    def test_quoted_plain_and_escaped_titles_are_display_values(self):
        for value,expected in [('"A title: example"','A title: example'),("'A visitor''s note'","A visitor's note"),('Plain title','Plain title'),('"A \\"quoted\\" title"','A "quoted" title')]:
            self.assertEqual(self.brain._title('example.md','---\ntitle: '+value+'\n---\n# Heading\n'),expected)
        self.assertEqual(self.brain._title('example.md','# Heading\n'),'Heading')
        self.assertEqual(self.brain._title('example.md','Plain body'),'example.md')
    def test_nested_fields_do_not_override_root_title_or_share_policy(self):
        text='---\ntitle: "Root title"\nshare: false\nextra:\n  title: "Nested label"\n  share: true\n---\nBody\n'
        (self.brain.base/'private.md').write_text(text)
        self.assertEqual(self.brain._title('private.md',text),'Root title')
        self.assertEqual(self.brain._front(text)[0]['share'],'false')
        self.assertIn('No shareable note',self.brain.t_codex_brain_read({'path':'private.md'}))
        self.assertEqual(self.brain.t_brain_read({'path':'private.md'}),text)
    def test_existing_search_index_rebuilds_quoted_titles_without_a_note_edit(self):
        note=self.brain.base/'legacy.md';note.write_text('---\ntitle: "Legacy title"\n---\nA legacy example.\n')
        signature='1:'+str(note.stat().st_mtime_ns)
        with patch.object(self.brain,'_signature',return_value=signature),patch.object(self.brain,'_title',return_value='"Legacy title"'):
            self.brain._reindex().close()
        result=self.brain.t_brain_search({'query':'legacy'})
        self.assertIn('— Legacy title',result);self.assertNotIn('— "Legacy title"',result)

    def test_malformed_quoted_title_is_preserved(self):
        self.assertEqual(self.brain._title('example.md','---\ntitle: "unfinished\n---\nBody'),'"unfinished')
    def test_reader_script_served_only_through_ingress_gate(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),ui_server.make_handler(self.brain));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url='http://127.0.0.1:%d/markdown.js'%server.server_port
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(url)
            self.assertEqual(error.exception.code,403);error.exception.close()
            with patch.object(ui_server,'GATEWAY_PEER','127.0.0.1'):
                with urllib.request.urlopen(url) as response:self.assertIn(b'IHBrainMarkdown',response.read())
                with urllib.request.urlopen(url.replace('markdown.js','index.html')) as response:self.assertIn(b'./markdown.js',response.read())
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
