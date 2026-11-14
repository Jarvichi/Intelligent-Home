"""Shared top bar (ih-nav): it exists on the adopted pages, links to the hub in the whole window, and never uses innerHTML.
Run: python3 -m unittest discover -s ih_ui/tests"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB = "/app/32c8114c_ih_orchestrator"
ADOPTED = ["ih_games", "ih_jobs", "ih_orchestrator", "ih_assistant", "ih_ai_layer", "ih_ai_layer_claude_cli", "ih_ai_layer_ollama", "ih_discord",
           "ih_discord_codex", "ih_discord_house", "ih_discord_ai_bot", "ih_discord_taskchannels", "ih_jobs_metoffice",
           "ih_orchestration_inbox", "ih_jobs_trust", "ih_jobs_backup", "ih_work"]


class NavBar(unittest.TestCase):
    def test_component_rules(self):
        js = (ROOT / "ih_ui" / "components.js").read_text()
        self.assertNotIn("innerHTML", js)
        self.assertIn("customElements.define('ih-nav'", js)
        self.assertIn("'_top'", js)          # real link target, works without script
        self.assertIn("location-changed", js)  # same-origin Home Assistant navigation, no reload
        css = (ROOT / "ih_ui" / "tokens.css").read_text()
        self.assertRegex(css, r"ih-nav\s*\{[^}]*position:\s*sticky;\s*top:\s*0")
        self.assertIn("min-height: 44px", css)

    def test_pages_have_bar_and_copies_are_current(self):
        for d in ADOPTED:
            ui = ROOT / d / "app" / "ui"
            page = (ui / "index.html").read_text()
            self.assertIn("<ih-nav", page, d)
            self.assertIn("ih-ui.js", page, d)
            self.assertEqual((ui / "ih-ui.js").read_text(), (ROOT / "ih_ui" / "components.js").read_text(), d)
            self.assertEqual((ui / "ih-ui.css").read_text(), (ROOT / "ih_ui" / "tokens.css").read_text(), d)
            if d != "ih_orchestrator":  # the hub has no back link
                self.assertIn('data-home-href="%s"' % HUB, page, d)
            for a in re.findall(r'<a [^>]*href="%s"[^>]*>' % re.escape(HUB), page):
                self.assertIn('target="_top"', a, d)  # inside the ingress frame a plain link would nest Home Assistant


if __name__ == "__main__":
    unittest.main()
