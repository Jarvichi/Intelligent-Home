"""Manager page: pure logic (node), served assets, and rules the page must keep (no innerHTML, no dialogs, relative URLs)."""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
UI = HERE.parent / "app" / "ui"
sys.path.insert(0, str(HERE.parent / "app"))
import manager  # noqa: E402


class ManagerLogic(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_pure_logic(self):
        r = subprocess.run(["node", str(HERE / "manager_logic_test.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_wizard_logic(self):
        r = subprocess.run(["node", str(HERE / "wizard_ui_test.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_page_rules(self):
        page = (UI / "index.html").read_text(encoding="utf-8")
        logic = (UI / "manager-logic.js").read_text(encoding="utf-8")
        wiz = (UI / "wizard-ui.js").read_text(encoding="utf-8")
        self.assertIn('src="wizard-ui.js"', page)
        for src in (page, logic, wiz):
            self.assertNotIn("innerHTML", src)
            self.assertNotIn("confirm(", src.replace("Tap again to confirm", ""))
            self.assertNotIn("alert(", src)
            self.assertNotIn("http://", src)
            self.assertNotIn("https://", src)
        self.assertIn('src="manager-logic.js"', page)
        self.assertIn("ih-ui.css", page)
        # five tabs, each reachable
        for t in ("today", "addons", "jobs", "requests", "health"):
            self.assertIn('data-t="%s"' % t, page)

    def test_every_api_call_kept(self):
        page = (UI / "index.html").read_text(encoding="utf-8")
        for call in ("api/addons", "api/health", "api/usage", "api/plugins", "api/settings", "api/traces", "api/trace/", "api/logs/",
                     "api/job/", "api/action/", "api/plugin/", "api/provision", "api/wizards"):
            self.assertIn(call, page, call)

    def test_assets_are_served_and_ingress_only(self):
        # the handler serves the logic file next to the page, and only to the ingress gateway
        src = (HERE.parent / "app" / "manager.py").read_text(encoding="utf-8")
        self.assertIn('"/manager-logic.js"', src)
        self.assertIn('"/wizard-ui.js"', src)
        self.assertTrue((UI / "manager-logic.js").is_file())
        self.assertTrue(manager.client_allowed("172.30.32.2"))
        self.assertFalse(manager.client_allowed("203.0.113.5"))


if __name__ == "__main__":
    unittest.main()
