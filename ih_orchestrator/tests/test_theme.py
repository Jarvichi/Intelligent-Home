import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import manager


class ThemeTest(unittest.TestCase):
    def test_persists_and_validates_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "theme.json")
            instance = manager.Manager([], usage_path=str(Path(directory) / "usage.json"), theme_path=path)
            self.assertEqual(instance.theme(), (200, {"palette": "neutral", "mode": "system"}))
            choice = {"palette": "coastal", "mode": "dark"}
            self.assertEqual(instance.set_theme(choice), (200, choice))
            self.assertEqual(json.loads(Path(path).read_text()), choice)
            self.assertEqual(manager.Manager([], theme_path=path).theme(), (200, choice))
            for palette in ('slate', 'lavender', 'rose', 'midnight', 'sunshine'):
                selected = {'palette': palette, 'mode': 'dark'}
                self.assertEqual(instance.set_theme(selected), (200, selected))
                self.assertEqual(instance.theme(), (200, selected))
            instance.set_theme(choice)
            for invalid in ({"palette": "bad", "mode": "dark"}, {"palette": "coastal", "mode": "dark", "extra": 1}, None):
                self.assertEqual(instance.set_theme(invalid)[0], 400)
                self.assertEqual(instance.theme(), (200, choice))

    def test_selector_is_available_in_orchestrator(self):
        page = (Path(manager.__file__).parent / "ui" / "index.html").read_text()
        self.assertIn('href="theme-preview.html"', page)
        self.assertIn('id="save-theme"', (Path(manager.__file__).parent / "ui" / "theme-preview.html").read_text())
