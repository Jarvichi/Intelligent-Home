import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import library as L


class Requeue(unittest.TestCase):
    def make(self):
        lib = L.Library(tempfile.mkdtemp(), players=[{"key": "p1"}])
        for t in ("Grand Theft Auto VI", "Grand Theft Auto: Vice City", "Has Tool"):
            e = lib.add("p1", t, "psn", tools=["x"] if t == "Has Tool" else None)
            lib.mark_todo("p1", e["id"])
        return lib

    def test_clears_only_exact_title_and_relists_it(self):
        lib = self.make()
        self.assertEqual(lib.games_without_todo(), [])
        self.assertEqual(lib.requeue_todo("p1", ["Grand Theft Auto VI"]), ["Grand Theft Auto VI"])
        rows = lib.games_without_todo()
        self.assertEqual([r["title"] for r in rows], ["Grand Theft Auto VI"])  # Vice City stays marked

    def test_games_with_a_tool_are_left_alone(self):
        lib = self.make()
        self.assertEqual(lib.requeue_todo("p1", ["Has Tool"]), [])

    def test_unknown_player_or_title_clears_nothing(self):
        lib = self.make()
        self.assertEqual(lib.requeue_todo("nobody", ["Grand Theft Auto VI"]), [])
        self.assertEqual(lib.requeue_todo("p1", ["No such game"]), [])


if __name__ == "__main__":
    unittest.main()
