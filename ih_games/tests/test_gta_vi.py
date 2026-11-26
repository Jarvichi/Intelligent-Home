"""GTA VI checklist: data file, matching, counters, persistence, card hook."""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import card_stat  # noqa: E402
from tools import ToolCtx, all_tools  # noqa: E402
from tools import gta_vi as G  # noqa: E402

P = "player_a"


class T(unittest.TestCase):
    def setUp(self):
        self.tc = ToolCtx(tempfile.mkdtemp(), {})

    def test_matches_and_discovery(self):
        for t in ("Grand Theft Auto VI", "GTA VI"):
            self.assertTrue(G.matches({"title": t}))
        self.assertFalse(G.matches({"title": "Grand Theft Auto V"}))
        self.assertIn(G, all_tools())

    def test_data(self):
        its = G.items()
        self.assertEqual(len(its), 35)
        self.assertEqual(len({i["id"] for i in its}), 35)
        self.assertEqual(len({i["alias"] for i in its}), 35)
        self.assertFalse(G._load()["verified"])
        self.assertEqual(G.progress(self.tc, P), {"done": 0, "total": 35, "label": G.PROGRESS_LABEL})

    def test_counter_and_done(self):
        self.assertIn("120 of 200", G.commands["done"](self.tc, P, ["vehicles", "120"]))
        self.assertEqual(G.progress(self.tc, P)["done"], 0)
        self.assertIn("130 of 200", G.commands["add"](self.tc, P, ["vehicles", "10"]))
        G.commands["done"](self.tc, P, ["vehicles"])
        self.assertEqual(G.progress(self.tc, P)["done"], 1)
        G.commands["undone"](self.tc, P, ["vehicles"])
        self.assertEqual(G.progress(self.tc, P)["done"], 0)

    def test_ambiguous_and_missing(self):
        self.assertIn("match", G.commands["done"](self.tc, P, ["fenced"]))
        self.assertIn("not found", G.commands["done"](self.tc, P, ["zzz"]))

    def test_money_and_target_override(self):
        self.assertIn("$50,000 of $100,000", G.commands["done"](self.tc, P, ["narcotics", "$50,000"]))
        G.commands["target"](self.tc, P, ["narcotics", "40000"])
        self.assertEqual(G.progress(self.tc, P)["done"], 1)

    def test_persistence_and_players(self):
        G.commands["done"](self.tc, P, ["story"])
        tc2 = ToolCtx(self.tc.data_dir, {})
        self.assertEqual(G.progress(tc2, P)["done"], 1)
        self.assertEqual(G.progress(tc2, "player_b")["done"], 0)
        self.assertTrue(os.path.exists(os.path.join(self.tc.data_dir, "gta_vi", "state_player_a.json")))

    def test_broken_state_file(self):
        p = G.state_path(self.tc, P)
        os.makedirs(os.path.dirname(p))
        open(p, "w").write("not json")
        self.assertEqual(G.progress(self.tc, P)["done"], 0)

    def test_outputs(self):
        self.assertIn("not verified", G.summary(self.tc, P))
        self.assertTrue(G.sensor(self.tc, P)[0].startswith("0 / 35"))
        d = G.details(self.tc, P)
        self.assertEqual(sum(len(g["items"]) for g in d["groups"]), 35)
        self.assertIn("left", G.commands["left"](self.tc, P, []))
        self.assertEqual(card_stat.tool_progress(G, self.tc, P)["total"], 35)


if __name__ == "__main__":
    unittest.main()
