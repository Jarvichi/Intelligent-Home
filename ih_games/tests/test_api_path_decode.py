"""The library API decodes percent-encoded game names in the URL path (0.9.6)."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import main

PLAYERS = [{"key": "player_a", "display_name": "Player A"}]


class PathDecodeTest(unittest.TestCase):
    def setUp(self):
        self.mod = main.Module({"players": PLAYERS}, data=tempfile.mkdtemp())
        self.mod.library.add("player_a", "Cyberpunk 2077", "psn", None)

    def summary(self, ref):
        return self.mod.api("GET", "/games/%s/summary" % ref, {"player": "player_a"}, {})

    def test_encoded_title_with_spaces_is_found(self):
        code, obj = self.summary("Cyberpunk%202077")
        self.assertEqual(code, 200, obj)
        self.assertEqual(obj["game"], "Cyberpunk 2077")

    def test_slug_still_works(self):
        self.assertEqual(self.summary("cyberpunk-2077")[0], 200)

    def test_unknown_game_still_404(self):
        self.assertEqual(self.summary("No%20Such%20Game")[0], 404)


if __name__ == "__main__":
    unittest.main()
