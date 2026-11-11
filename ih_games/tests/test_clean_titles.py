import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import library as L


class CleanTitles(unittest.TestCase):
    def test_clean_title(self):
        for raw, want in [("MultiVersus Trophies", "MultiVersus"), ("SSX TROPHY SET", "SSX"), ("Crimson Moon Base Trophy Set", "Crimson Moon"),
                          ("WipEout® HD Trophies", "WipEout® HD"), ("Savage Moon™ Veteran's Awards", "Savage Moon™"),
                          ("Trophies", "Trophies"), ("Cyberpunk 2077", "Cyberpunk 2077"), ("Award Winning Game", "Award Winning Game")]:
            self.assertEqual(L.clean_title(raw), want)

    def test_existing_entries_renamed_keep_id_and_skip_collisions(self):
        d = tempfile.mkdtemp()
        lib = L.Library(d, players=[{"key": "p1"}])
        a = lib.add("p1", "Foo Trophies", "psn")
        # add() cleans the title now; simulate old data by editing the stored title
        data = lib._load()
        data["entries"]["p1"][a["id"]]["title"] = "Foo Trophies"
        b = {"id": "bar-steam", "title": "Bar", "platform": "steam", "first_seen": None, "last_seen": None, "playtime_min": None,
             "source": "steam", "tools": [], "hidden": False, "manual": False, "todo": False}
        c = dict(b, id="bar-trophies-steam", title="Bar Trophies")
        data["entries"]["p1"]["bar-steam"], data["entries"]["p1"]["bar-trophies-steam"] = b, c
        lib._clean_existing(data)
        ents = data["entries"]["p1"]
        self.assertEqual(ents[a["id"]]["title"], "Foo")
        self.assertEqual(ents["bar-trophies-steam"]["title"], "Bar Trophies")  # collision, not a feed duplicate: left alone
        c["source"] = L.TITLES_FEED
        lib._clean_existing(data)
        self.assertNotIn("bar-trophies-steam", data["entries"]["p1"])  # feed-made duplicate dropped
        self.assertIn("bar-steam", data["entries"]["p1"])


if __name__ == "__main__":
    unittest.main()
