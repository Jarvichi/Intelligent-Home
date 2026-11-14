"""The ih_games wizard definition must match ih_games itself: real option names, text types, real provider names."""
import re, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ih_orchestrator" / "app"))
import wizard


class GamesWizard(unittest.TestCase):
    def test_definition_matches_ih_games(self):
        d = wizard.load("ih_games")
        self.assertIsNotNone(d)
        cfg = (ROOT / "ih_games" / "config.yaml").read_text(encoding="utf-8")
        schema = cfg.split("\nschema:", 1)[1]
        providers = {p.stem for p in (ROOT / "ih_games" / "app" / "providers").glob("*.py") if p.stem != "__init__"}
        self.assertIn("\n  %s: " % d["enable_option"], schema)
        for g in d["groups"]:
            for v in g["enable_values"]:
                self.assertIn(v, providers, v)
            for f in g["fields"]:
                m = re.search(r"^  %s: (\w+)\??$" % f["option"], schema, re.M)
                self.assertIsNotNone(m, f["option"])
                self.assertIn(m.group(1), ("str", "password"), f["option"])


if __name__ == "__main__":
    unittest.main()
