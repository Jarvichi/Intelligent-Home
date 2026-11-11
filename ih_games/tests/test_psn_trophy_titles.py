"""psn_trophy_titles provider, library import (no duplicates, no to-dos, hidden stays hidden), trophy text. No network."""
import datetime, enum, json, os, sys, tempfile, types, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import library as L, played, trophies
from providers import psn_trophy_titles as P


class PlatformType(enum.Enum):
    PS5 = "PS5"


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def tier(b=0, s=0, g=0, p=0):
    return Obj(bronze=b, silver=s, gold=g, platinum=p)


CALLS = []


class FakeMe:
    online_id = "ExamplePlayer"

    def trophy_titles(self, limit=None):
        CALLS.append(limit)
        return iter([
            Obj(title_name="Cyberpunk 2077", title_platform=frozenset({PlatformType.PS5}), progress=34,
                earned_trophies=tier(12, 3, 1, 0), defined_trophies=tier(40, 10, 5, 1),
                last_updated_date_time=datetime.datetime(2026, 11, 1, 12, 0)),
            Obj(title_name="Untouched", title_platform=frozenset({PlatformType.PS5}), progress=0,
                earned_trophies=tier(), defined_trophies=tier(10), last_updated_date_time=None),
            Obj(title_name="Hidden Game", title_platform=frozenset({PlatformType.PS5}), progress=5,
                earned_trophies=tier(1), defined_trophies=tier(20), last_updated_date_time=datetime.datetime(2026, 10, 1)),
            Obj(title_name="Steam Twin", title_platform=frozenset({PlatformType.PS5}), progress=5,
                earned_trophies=tier(1), defined_trophies=tier(20), last_updated_date_time=datetime.datetime(2026, 10, 2)),
        ])


class FakeClient:
    def __init__(self, npsso_cookie=None):
        pass

    def me(self):
        return FakeMe()


class Ctx:
    def __init__(self, d, token="tok"):
        self.data_dir, self.opts, self.logs = d, {"psn_npsso": token}, []

    def log(self, m):
        self.logs.append(m)


PLAYER = {"key": "player_a", "psn_online_id": "ExamplePlayer"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.d = self.tmp.name
        self.saved = {k: sys.modules.get(k) for k in ("psnawp_api",)}
        sys.modules["psnawp_api"] = types.ModuleType("psnawp_api"); sys.modules["psnawp_api"].PSNAWP = FakeClient
        del CALLS[:]

    def tearDown(self):
        self.tmp.cleanup()
        if self.saved["psnawp_api"]: sys.modules["psnawp_api"] = self.saved["psnawp_api"]
        else: sys.modules.pop("psnawp_api", None)

    def feed(self):
        return json.load(open(os.path.join(self.d, "feeds", "psn_trophy_titles_player_a.json")))


class ProviderTest(Base):
    def test_writes_titles_with_earned_trophies_only(self):
        P.run(Ctx(self.d), PLAYER)
        f = self.feed()
        self.assertEqual(f["source"], "psn_trophy_titles"); self.assertEqual(f["events"], [])
        self.assertEqual([t["title"] for t in f["titles"]], ["Cyberpunk 2077", "Hidden Game", "Steam Twin"])
        t = f["titles"][0]
        self.assertEqual((t["platform"], t["progress"], t["earned"]["bronze"], t["defined"]["platinum"]), ("PS5", 34, 12, 1))
        self.assertTrue(t["last_updated"].startswith("2026-11-01"))
        self.assertEqual(CALLS, [None])

    def test_runs_once_per_refresh_window(self):
        P.run(Ctx(self.d), PLAYER); P.run(Ctx(self.d), PLAYER)
        self.assertEqual(len(CALLS), 1)

    def test_no_credentials_does_nothing(self):
        P.run(Ctx(self.d, token=""), PLAYER)
        P.run(Ctx(self.d), {"key": "player_a"})
        self.assertEqual(CALLS, []); self.assertFalse(os.path.exists(os.path.join(self.d, "feeds")))

    def test_failure_logs_type_only_and_retries_later(self):
        class Boom(FakeClient):
            def me(self): raise RuntimeError("secret-token-xyz")
        sys.modules["psnawp_api"].PSNAWP = Boom
        c = Ctx(self.d); P.run(c, PLAYER)
        self.assertEqual(c.logs, ["psn_trophy_titles: lookup failed for player_a (RuntimeError)"])
        self.assertFalse(os.path.exists(os.path.join(self.d, "feeds", "psn_trophy_titles_player_a.json")))
        P.run(c, PLAYER); self.assertEqual(len(c.logs), 1)   # not retried straight away


class LibraryTest(Base):
    def lib(self):
        return L.Library(self.d, players=[{"key": "player_a", "display_name": "A"}])

    def test_adds_new_games_without_duplicates_todos_or_unhiding(self):
        P.run(Ctx(self.d), PLAYER)
        lib = self.lib()
        lib.add("player_a", "Hidden Game", "psn"); lib.hide("player_a", "Hidden Game")
        lib.add("player_a", "Steam Twin", "steam")
        lib.refresh(); lib.refresh()
        ents = lib._load()["entries"]["player_a"]
        titles = sorted(e["title"] for e in ents.values())
        self.assertEqual(titles, ["Cyberpunk 2077", "Hidden Game", "Steam Twin"])
        cp = ents[L.make_id("Cyberpunk 2077", "psn")]
        self.assertEqual((cp["platform"], cp["source"], cp["hidden"]), ("psn", "psn_trophy_titles", False))
        self.assertTrue(ents["hidden-game-psn"]["hidden"])
        self.assertNotIn("Cyberpunk 2077", [r["title"] for r in lib.games_without_todo()])

    def test_bulk_games_create_no_todos(self):
        lib = self.lib()
        os.makedirs(os.path.join(self.d, "feeds"))
        json.dump({"updated": "2026-11-10T10:00:00+00:00", "titles": [{"title": "Game %d" % i} for i in range(145)]},
                  open(os.path.join(self.d, "feeds", "psn_trophy_titles_player_a.json"), "w"))
        lib.refresh()
        self.assertEqual(len(lib.list("player_a")), 145)
        self.assertEqual(lib.games_without_todo(), [])


class TextTest(Base):
    def test_summary_text_has_trophy_line_and_latest_named(self):
        P.run(Ctx(self.d), PLAYER)
        events = [{"kind": "trophy_named", "game": "Cyberpunk 2077", "achievement": "Trophy %d" % i, "tier": "bronze",
                   "time": "2026-11-0%dT10:00:00" % i} for i in range(1, 8)]
        json.dump({"events": events}, open(os.path.join(self.d, "feeds", "psn_trophy_names_player_a.json"), "w"))
        entry = {"title": "Cyberpunk 2077", "platform": "psn"}
        pl = played.played(self.d, "player_a", entry)
        self.assertIn("Trophies: 12 bronze, 3 silver, 1 gold, 0 platinum, 34% complete", played.text(entry, pl))
        latest = pl["trophies"]["latest"]
        self.assertEqual(len(latest), trophies.LATEST_NAMED_MAX); self.assertEqual(latest[0]["name"], "Trophy 7")

    def test_no_data_means_no_trophy_block(self):
        self.assertIsNone(trophies.for_game(self.d, "player_a", "Nothing"))
        self.assertNotIn("Trophies", played.text({"title": "Nothing"}, played.played(self.d, "player_a", {"title": "Nothing"})))


if __name__ == "__main__":
    unittest.main()
