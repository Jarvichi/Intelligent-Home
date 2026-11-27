"""The page: shared top bar and current copies of the shared UI files, the standard filter block, no markup built from text, versions in step."""
import json, os, re, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / 'ih_tasks'
UI = ADDON / 'app' / 'ui'
HUB = '/app/32c8114c_ih_orchestrator'


class Files(unittest.TestCase):
    def test_nav_and_shared_copies(self):
        page = (UI / 'index.html').read_text()
        self.assertIn('<ih-nav data-home-href="%s"' % HUB, page)
        self.assertEqual((UI / 'ih-ui.js').read_text(), (ROOT / 'ih_ui' / 'components.js').read_text())
        self.assertEqual((UI / 'ih-ui.css').read_text(), (ROOT / 'ih_ui' / 'tokens.css').read_text())
        self.assertEqual((UI / 'ih-patterns.css').read_text(), (ROOT / 'ih_ui' / 'patterns.css').read_text())

    def test_standard_filter_block(self):
        page = (UI / 'index.html').read_text()
        css = (UI / 'ui.css').read_text()
        self.assertIn('type="search"', page)
        self.assertIn('id="f-count"', page)
        for sel in ('f-stage', 'f-waiting'):
            self.assertRegex(page, r'<label for="%s">[^<]+</label>' % sel)
            self.assertRegex(page, r'<select id="%s">\s*<option value="">All</option>' % sel)
        self.assertRegex(page, r'<button id="f-clear"[^>]*>Clear</button>')
        self.assertNotIn('chip', page + css)
        self.assertRegex(css, r'\.t-filter-grid\{[^}]*grid-template-rows:auto 44px')
        self.assertNotRegex(css, r'@media[^{]*\{[^}]*\.t-filter-grid')              # one row at every width

    def test_script_builds_no_markup_from_text(self):
        js = (UI / 'ui.js').read_text()
        for bad in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'confirm(', 'alert('):
            self.assertNotIn(bad, js)
        self.assertIn("rel: 'noopener noreferrer'", js)

    def test_plan_markdown_is_loaded_before_the_page(self):
        page = (UI / 'index.html').read_text()
        self.assertLess(page.index('src="markdown.js"'), page.index('src="ui.js"'))
        self.assertIn('IHTasksMarkdown.render(t.plan)', (UI / 'ui.js').read_text())

    def test_merge_controls(self):
        page = (UI / 'index.html').read_text()
        css = (UI / 'ui.css').read_text()
        js = (UI / 'ui.js').read_text()
        self.assertRegex(page, r'<button id="merge-toggle"[^>]*aria-pressed="false">Merge</button>')
        self.assertRegex(page, r'<button id="dup-toggle"[^>]*>Find duplicates</button>')
        self.assertIn('id="merge-bar"', page)
        self.assertRegex(css, r'\.t-mergebar\{position:fixed;left:0;right:0;bottom:0')
        self.assertRegex(css, r'\.t-mergebar\{[^}]*grid-template-columns:minmax\(0,1fr\)')      # never wider than a phone screen
        self.assertRegex(css, r'\.t-pick\{[^}]*min-height:56px')
        self.assertRegex(css, r'\.t-mergebar \.t-button[^{]*\{min-height:48px')
        self.assertIn("confirmButton(label(tasks.length, keep.id)", js)                        # merging takes two taps
        self.assertIn("api('POST', '/tasks/merge', { keep: keep.id, merge: others })", js)
        self.assertIn("api('POST', '/tasks/duplicates/dismiss', { ids: g.ids })", js)

    def test_new_task_modal(self):
        page = (UI / 'index.html').read_text()
        js = (UI / 'ui.js').read_text()
        self.assertIn('id="new-open"', page)
        self.assertRegex(page, r'<div id="new-modal"[^>]*hidden>')
        for f in ('new-fields', 'new-msg', 'new-cancel', 'new-save'):
            self.assertIn('id="%s"' % f, page)
        self.assertIn("type: f.type === 'date' ? 'date' : 'text'", js)      # the fields themselves come from the shared list (see test_wave1)
        self.assertIn("ev.key === 'Escape'", js)
        self.assertIn("api('POST', '/tasks', body)", js)

    def test_versions_and_ports_agree(self):
        cfg = (ADDON / 'config.yaml').read_text()
        version = re.search(r'^version: "([^"]+)"', cfg, re.M).group(1)
        self.assertEqual(json.loads((ADDON / 'app' / 'manifest.json').read_text())['version'], version)
        self.assertIn("VERSION = '%s'" % version, (ADDON / 'app' / 'main.py').read_text())
        self.assertIn('## %s' % version, (ADDON / 'CHANGELOG.md').read_text())
        self.assertIn('boot: auto', cfg)
        self.assertIn('watchdog: "http://[HOST]:[PORT:8818]/healthz"', cfg)
        self.assertIn('ingress_port: 8823', cfg)
        run = (ADDON / 'run.sh').read_text()
        self.assertIn('IHT_PORT=8818', run)
        self.assertIn('IHT_UI_PORT=8823', run)
        self.assertEqual(json.loads((UI / 'ui.json').read_text())['health_port'], 8818)
        self.assertTrue((ROOT / 'ih_meta' / 'ih_tasks.json').exists())

    def test_ports_are_not_used_by_another_add_on(self):
        for cfg in ROOT.glob('ih_*/config.yaml'):
            if cfg.parent.name == 'ih_tasks':
                continue
            text = cfg.read_text()
            self.assertNotRegex(text, r'(?m)^\s+\d+/tcp: (8818|8823)\s*$', cfg.parent.name)
            self.assertNotRegex(text, r'(?m)^ingress_port: (8818|8823)\s*$', cfg.parent.name)

    def test_own_copy_of_ihlib_is_current(self):
        for f in (ROOT / 'ihlib').glob('*.py'):
            self.assertEqual((ADDON / 'app' / 'ihlib' / f.name).read_text(), f.read_text(), f.name)


if __name__ == '__main__':
    unittest.main()
