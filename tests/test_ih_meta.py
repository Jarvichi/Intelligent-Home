"""Every add-on has ih_meta/<slug>.json (what it needs, what its page is for): the Add-ons page reads it. Kept outside the add-on folders so editing it needs no add-on version bump. 13 Nov 2026."""
import json, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = {"mqtt"}


class IhMeta(unittest.TestCase):
    def addons(self):
        return sorted(d.name for d in ROOT.iterdir() if d.is_dir() and d.name.startswith("ih_") and (d / "config.yaml").exists())

    def test_every_addon_has_valid_ih_meta_file(self):
        names = set(self.addons())
        self.assertTrue(names)
        for n in names:
            f = ROOT / "ih_meta" / (n + ".json")
            self.assertTrue(f.exists(), n + " has no ih_meta file")
            d = json.loads(f.read_text())
            self.assertEqual(set(d), {"requires", "ui"}, n)
            self.assertIsInstance(d["requires"], list)
            for r in d["requires"]:
                self.assertTrue(r in names or r in EXTERNAL, "%s needs unknown %s" % (n, r))
            self.assertTrue(d["ui"].get("title"), n + " has no title")
            self.assertTrue(d["ui"].get("purpose"), n + " has no purpose")
            self.assertLessEqual(len(d["ui"]["title"]), 40)
            self.assertLessEqual(len(d["ui"]["purpose"]), 160)


if __name__ == "__main__":
    unittest.main()
