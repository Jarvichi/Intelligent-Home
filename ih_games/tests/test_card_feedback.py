"""Game card feedback (0.11.0): PSN play time from sessions, the card stat box, the per-tier trophy list. No network."""
import datetime, enum, json, os, re, shutil, subprocess, sys, tempfile, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, '..', 'app')
sys.path.insert(0, APP)
import card_stat, library as L, main, trophies
from providers import psn_sessions as S, psn_trophy_names as N
from tools import psn_backfill

PLAYER = "player_a"
PLAYERS = [{"key": PLAYER, "display_name": "Player A", "trophy_prefix": "player_a", "psn_online_id": "ExamplePlayer"}]
GAME = "Example Quest"


def at(minute):
    return "2026-11-01T%02d:%02d:00+00:00" % (10 + minute // 60, minute % 60)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh)


def titles_feed(d, titles):
    write_json(os.path.join(d, "feeds", "psn_trophy_titles_%s.json" % PLAYER), {"updated": at(0), "events": [], "titles": titles})


def title_row(title=GAME, earned=(21, 3, 0, 0), defined=(39, 10, 5, 1)):
    keys = ("bronze", "silver", "gold", "platinum")
    return {"title": title, "platform": "PS5", "progress": 34, "earned": dict(zip(keys, earned)), "defined": dict(zip(keys, defined)),
            "last_updated": at(0)}


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)


class SessionStepTest(unittest.TestCase):
    def games(self, seq):
        st, d = {}, {"games": {}}
        for minute, game in seq:
            S.step(st, d, game, at(minute))
        return d

    def test_dropouts_and_short_gaps_do_not_cut_a_session(self):
        d = self.games([(0, "X"), (5, S.UNREADABLE), (10, "X"), (12, None), (15, None), (16, "X"), (30, "X"), (31, None), (45, None)])
        self.assertEqual((d["games"]["X"]["minutes"], d["games"]["X"]["sessions"]), (30, 1))
        self.assertEqual(d["recent"], [{"game": "X", "start": at(0), "end": at(30), "minutes": 30}])

    def test_title_switch_closes_and_short_session_is_dropped(self):
        d = self.games([(0, "A"), (19, "A"), (20, "B"), (21, None), (40, None)])
        self.assertEqual(d["games"]["A"]["minutes"], 19)
        self.assertNotIn("B", d["games"])

    def test_sessions_add_up(self):
        gap = S.SESSION_GAP_MIN
        d = self.games([(0, "A"), (20, "A"), (21, None), (21 + gap, None), (60, "A"), (75, "A"), (76, None), (76 + gap, None)])
        self.assertEqual((d["games"]["A"]["minutes"], d["games"]["A"]["sessions"]), (35, 2))

    def test_open_session_is_not_counted_yet(self):
        self.assertEqual(self.games([(0, "A"), (50, "A")])["games"], {})

    def test_reading(self):
        self.assertEqual([S.reading(x) for x in ("unavailable", None, "", "unknown", "A")], [S.UNREADABLE] * 3 + [None, "A"])


class FakeCtx:
    def __init__(self, data_dir, state="A", fail=False):
        self.data_dir, self.state, self.fail, self.asked = data_dir, state, fail, []

    def ha(self, path):
        self.asked.append(path)
        if self.fail:
            raise RuntimeError("no connection")
        return {"state": self.state}


class SessionRunTest(TmpCase):
    def now(self, minute):
        return datetime.datetime.fromisoformat(at(minute))

    def load(self):
        with open(os.path.join(self.d, "psn_playtime_%s.json" % PLAYER)) as fh:
            return json.load(fh)

    def test_run_tracks_sensor_and_writes_outside_feeds(self):
        ctx = FakeCtx(self.d)
        S.run(ctx, PLAYERS[0], now=self.now(0)); S.run(ctx, PLAYERS[0], now=self.now(25))
        ctx.state = "unknown"
        S.run(ctx, PLAYERS[0], now=self.now(26)); S.run(ctx, PLAYERS[0], now=self.now(26 + S.SESSION_GAP_MIN))
        data = self.load()
        self.assertEqual(ctx.asked[0], "states/sensor.player_a_now_playing")
        self.assertEqual(data["games"]["A"]["minutes"], 25)
        self.assertEqual(data["tracked_since"], "2026-11-01")
        self.assertFalse(os.path.exists(os.path.join(self.d, "feeds")))

    def test_read_error_is_ignored_and_no_prefix_does_nothing(self):
        ctx = FakeCtx(self.d)
        S.run(ctx, PLAYERS[0], now=self.now(0))
        ctx.fail = True
        S.run(ctx, PLAYERS[0], now=self.now(30))
        self.assertEqual(self.load()["state"]["last"], at(0))
        S.run(FakeCtx(self.d), {"key": "player_b"})
        self.assertFalse(os.path.exists(os.path.join(self.d, "psn_playtime_player_b.json")))

    def test_rides_along_with_psn_trophies_only(self):
        mod = main.Module({"players": PLAYERS, "providers_enabled": ["psn_trophies"]}, data=self.d)
        self.assertEqual(mod.providers_to_run(), ["psn_trophies", S.NAME])
        mod = main.Module({"players": PLAYERS, "providers_enabled": ["steam_library"]}, data=self.d)
        self.assertEqual(mod.providers_to_run(), ["steam_library"])
        mod = main.Module({"players": PLAYERS, "providers_enabled": [S.NAME, "psn_trophies"]}, data=self.d)
        self.assertEqual(mod.providers_to_run().count(S.NAME), 1)


class BackfillTest(TmpCase):
    def test_backfill_reads_recorder_rows_and_writes_only_on_request(self):
        import sqlite3
        db, out = os.path.join(self.d, "rec.db"), os.path.join(self.d, "out", "psn_playtime_x.json")
        c = sqlite3.connect(db)
        c.execute("create table states_meta (metadata_id integer, entity_id text)")
        c.execute("create table states (state text, last_updated_ts real, metadata_id integer)")
        c.execute("insert into states_meta values (1, 'sensor.player_a_now_playing')")
        t0 = datetime.datetime(2026, 11, 1, 10, tzinfo=datetime.timezone.utc).timestamp()
        minute = S.SECONDS_PER_MINUTE
        rows = [("Old", t0 - 5 * 86400), ("unknown", t0 - 4 * 86400), ("A", t0), ("A", t0 + 40 * minute), ("unknown", t0 + 41 * minute),
                ("unknown", t0 + 60 * minute)]
        c.executemany("insert into states values (?, ?, 1)", rows); c.commit(); c.close()
        data = psn_backfill.main(db, "sensor.player_a_now_playing", out, "2026-11-01")
        self.assertEqual(data["games"], {"A": {"minutes": 40, "sessions": 1, "last_played": data["games"]["A"]["last_played"]}})
        self.assertFalse(os.path.exists(out))
        psn_backfill.main(db, "sensor.player_a_now_playing", out, "2026-11-01", write=True)
        self.assertTrue(os.path.exists(out))


class LibraryPlaytimeTest(TmpCase):
    def lib(self):
        return L.Library(self.d, players=PLAYERS)

    def playtime(self, games, since="2026-11-01"):
        write_json(os.path.join(self.d, L.PLAYTIME_FILE % PLAYER), {"tracked_since": since, "games": games})

    def test_tracked_minutes_fill_psn_entry(self):
        lib = self.lib(); lib.add(PLAYER, GAME, "psn")
        self.playtime({GAME: {"minutes": 356, "sessions": 4}})
        lib.refresh()
        e = lib.find(PLAYER, GAME)
        self.assertEqual((e["playtime_min"], e["playtime_src"], e["playtime_since"]), (356, L.PLAYTIME_SRC_TRACKED, "2026-11-01"))
        self.playtime({GAME: {"minutes": 400, "sessions": 5}})
        lib.refresh()
        self.assertEqual(lib.find(PLAYER, GAME)["playtime_min"], 400)

    def test_platform_play_time_is_never_overwritten(self):
        lib = self.lib()
        write_json(os.path.join(self.d, "feeds", "steam_library_%s.json" % PLAYER),
                   {"owned_games": [{"game": GAME, "playtime_min": 90, "time": at(0)}]})
        self.playtime({GAME: {"minutes": 356}})
        lib.refresh()
        e = lib.find(PLAYER, GAME)
        self.assertEqual(e["playtime_min"], 90)
        self.assertNotIn("playtime_src", e)

    def test_game_without_sessions_keeps_no_play_time(self):
        lib = self.lib(); lib.add(PLAYER, GAME, "psn")
        self.playtime({"Another Game": {"minutes": 50}})
        lib.refresh()
        self.assertIsNone(lib.find(PLAYER, GAME)["playtime_min"])


class QuestTool:
    NAME = "questgame"

    def __init__(self, answer):
        self.answer = answer

    def progress(self, tc, player):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class CardStatTest(TmpCase):
    TOTALS = {L.norm(GAME): {"earned": 24, "total": 55}}

    def entry(self, **kw):
        return dict({"title": GAME, "platform": "psn", "playtime_min": None, "tools": []}, **kw)

    def test_order_quests_then_trophies_then_playtime_then_nothing(self):
        quest = card_stat.tool_progress(QuestTool({"done": 101, "total": 250}), None, PLAYER)
        e = self.entry(playtime_min=356)
        self.assertEqual(card_stat.for_entry(e, self.TOTALS, quest), {"kind": "quests", "done": 101, "total": 250, "label": "Quests"})
        self.assertEqual(card_stat.for_entry(e, self.TOTALS), {"kind": "trophies", "earned": 24, "total": 55})
        self.assertEqual(card_stat.for_entry(e, {}), {"kind": "playtime", "minutes": 356, "tracked": False, "since": None})
        self.assertIsNone(card_stat.for_entry(self.entry(), {}))

    def test_zero_minutes_is_hidden_not_shown(self):
        self.assertIsNone(card_stat.for_entry(self.entry(playtime_min=0), {}))

    def test_tracked_play_time_carries_its_start_date(self):
        e = self.entry(playtime_min=40, playtime_src=L.PLAYTIME_SRC_TRACKED, playtime_since="2026-11-01")
        self.assertEqual(card_stat.for_entry(e, {}), {"kind": "playtime", "minutes": 40, "tracked": True, "since": "2026-11-01"})

    def test_psn_trophies_are_not_shown_on_another_platform(self):
        self.assertEqual(card_stat.for_entry(self.entry(platform="steam", playtime_min=30), self.TOTALS)["kind"], "playtime")

    def test_bad_tool_progress_is_ignored(self):
        for answer in (None, {}, {"done": 1}, {"done": 3, "total": 0}, {"done": "x", "total": 5}, {"done": -1, "total": 5}, RuntimeError("x")):
            self.assertIsNone(card_stat.tool_progress(QuestTool(answer), None, PLAYER), answer)
        self.assertIsNone(card_stat.tool_progress(object(), None, PLAYER))
        self.assertEqual(card_stat.tool_progress(QuestTool({"done": 9, "total": 5, "label": "Missions"}), None, PLAYER),
                         {"kind": "quests", "done": 5, "total": 5, "label": "Missions"})

    def test_totals_index_sums_tiers_and_skips_games_without_totals(self):
        titles_feed(self.d, [title_row(), title_row("No Totals", defined=(0, 0, 0, 0)), {"title": "Broken", "earned": "x"}])
        self.assertEqual(trophies.totals_index(self.d, PLAYER), self.TOTALS)

    def test_library_api_adds_card_stat(self):
        mod = main.Module({"players": PLAYERS}, data=self.d)
        mod.library.add(PLAYER, GAME, "psn"); mod.library.add(PLAYER, "Bare Game", "psn")
        titles_feed(self.d, [title_row()])
        code, obj = mod.api("GET", "/library", {"player": PLAYER}, {})
        stats = {g["title"]: g["card_stat"] for g in obj["games"]}
        self.assertEqual(code, 200)
        self.assertEqual(stats, {GAME: {"kind": "trophies", "earned": 24, "total": 55}, "Bare Game": None})


def psn_rows():
    return [{"id": "0", "group": "default", "name": "First Steps", "detail": "Finish the intro", "tier": "bronze", "earned": True,
             "earned_at": "2026-11-02T10:00:00+00:00", "hidden": False},
            {"id": "1", "group": "default", "name": "Long Road", "detail": "Finish the story", "tier": "gold", "earned": False,
             "earned_at": None, "hidden": False},
            {"id": "2", "group": "default", "name": "Second Wind", "detail": "", "tier": "bronze", "earned": True,
             "earned_at": "2026-11-05T10:00:00+00:00", "hidden": False},
            {"id": "3", "group": "default", "name": "Secret Door", "detail": "", "tier": "bronze", "earned": False,
             "earned_at": None, "hidden": True},
            {"id": "4", "group": "default", "name": "Not A Tier", "tier": "copper", "earned": True}]


class TierListTest(TmpCase):
    NOW = datetime.datetime(2026, 11, 11, 12, tzinfo=datetime.timezone.utc)

    def later(self, seconds):
        return self.NOW + datetime.timedelta(seconds=seconds)

    def test_fetch_gives_complete_list_earned_first_and_is_saved(self):
        calls = []
        out = trophies.tier_list(self.d, PLAYER, GAME, fetch=lambda t: calls.append(t) or psn_rows(), now=self.NOW)
        self.assertEqual((out["complete"], out["source"], out["stale"], out["missing"], out["note"]), (True, "psn", False, None, None))
        self.assertEqual([t["name"] for t in out["trophies"]], ["Second Wind", "First Steps", "Long Road", "Secret Door"])
        self.assertEqual(calls, [GAME])
        again = trophies.tier_list(self.d, PLAYER, GAME, fetch=lambda t: calls.append(t) or [], now=self.later(trophies.LIST_CACHE_S - 1))
        self.assertEqual(len(calls), 1)       # served from the saved list
        self.assertEqual(again["trophies"], out["trophies"])

    def test_old_saved_list_is_refreshed_and_kept_when_the_lookup_fails(self):
        trophies.tier_list(self.d, PLAYER, GAME, fetch=lambda t: psn_rows(), now=self.NOW)

        def boom(title):
            raise RuntimeError("secret-token-xyz")
        out = trophies.tier_list(self.d, PLAYER, GAME, fetch=boom, now=self.later(trophies.LIST_CACHE_S + 1))
        self.assertEqual((out["complete"], out["stale"], out["note"]), (True, True, trophies.STALE_NOTE))
        self.assertEqual(len(out["trophies"]), 4)
        self.assertNotIn("secret", json.dumps(out))
        fresh = [dict(psn_rows()[1], earned=True, earned_at="2026-11-11T11:00:00+00:00")]
        out = trophies.tier_list(self.d, PLAYER, GAME, fetch=lambda t: fresh, now=self.later(trophies.LIST_CACHE_S + 2))
        self.assertEqual((out["stale"], [t["name"] for t in out["trophies"]]), (False, ["Long Road"]))

    def history(self):
        with open(os.path.join(self.d, trophies.HISTORY_FILE % PLAYER), "w") as fh:
            fh.write(json.dumps({"time": at(1), "id": "7", "game": GAME, "tier": "bronze", "name": "First Steps", "earned_at": at(0)}) + "\n")
            fh.write("not json\n")
            fh.write(json.dumps({"time": at(2), "id": "8", "game": "Another Game", "tier": "gold", "name": "Elsewhere"}) + "\n")
        write_json(os.path.join(self.d, "feeds", "psn_trophy_names_%s.json" % PLAYER), {"events": [
            {"kind": "trophy_named", "game": GAME, "achievement": "First Steps", "tier": "bronze", "time": at(1)},
            {"kind": "trophy_named", "game": GAME, "achievement": "Second Wind", "tier": "bronze", "time": at(30)},
            {"kind": "achievement", "game": GAME, "count": 1, "time": at(31)}]})

    def test_without_psn_lookup_only_recorded_earned_names_and_it_says_so(self):
        self.history()
        out = trophies.tier_list(self.d, PLAYER, GAME, fetch=None, now=self.NOW)
        self.assertEqual((out["complete"], out["source"], out["missing"]), (False, "history", trophies.MISSING_NOT_CONFIGURED))
        self.assertEqual([(t["name"], t["earned"], t["earned_at"]) for t in out["trophies"]],
                         [("Second Wind", True, at(30)), ("First Steps", True, at(0))])
        self.assertTrue(out["note"].startswith(trophies.INCOMPLETE_NOTE))
        self.assertIn("Unearned trophies", out["note"])

    def test_failed_and_empty_lookups_name_the_reason(self):
        def boom(title):
            raise RuntimeError("x")
        self.assertEqual(trophies.tier_list(self.d, PLAYER, GAME, fetch=boom, now=self.NOW)["missing"], trophies.MISSING_LOOKUP_FAILED)
        out = trophies.tier_list(self.d, PLAYER, GAME, fetch=lambda t: [], now=self.NOW)
        self.assertEqual((out["missing"], out["trophies"], out["complete"]), (trophies.MISSING_NOT_FOUND, [], False))
        self.assertFalse(os.path.exists(os.path.join(self.d, trophies.LIST_DIR)))

    def test_api_route_and_fetcher_need_token_and_online_id(self):
        self.history()
        mod = main.Module({"players": PLAYERS}, data=self.d)
        mod.library.add(PLAYER, GAME, "psn")
        self.assertIsNone(mod.trophy_fetcher(PLAYER))
        code, obj = mod.api("GET", "/games/%s/trophies" % L.make_id(GAME, "psn"), {"player": PLAYER}, {})
        self.assertEqual((code, obj["ok"], obj["game"], obj["complete"], len(obj["trophies"])), (200, True, GAME, False, 2))
        self.assertEqual(mod.api("GET", "/games/no-such-game/trophies", {"player": PLAYER}, {})[0], 404)
        mod = main.Module({"players": PLAYERS, "psn_npsso": "example-token"}, data=self.d)
        self.assertTrue(callable(mod.trophy_fetcher(PLAYER)))
        self.assertIsNone(mod.trophy_fetcher("player_b"))


class PlatformType(enum.Enum):
    PS4 = "PS4"
    PS5 = "PS5"


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeUser:
    online_id = "ExamplePlayer"

    def trophy_titles(self, limit=None):
        return [Obj(title_name=GAME, np_communication_id="NPWR0", title_platform=frozenset({PlatformType.PS5}))]

    def trophy_groups_summary(self, comm_id, platform):
        return Obj(trophy_groups=[Obj(trophy_group_id="default"), Obj(trophy_group_id="001")])

    def trophies(self, comm_id, platform, include_progress=True, trophy_group_id="default", limit=None):
        if trophy_group_id == "001":
            return [Obj(earned=False, trophy_name="Add-on Ace", trophy_detail="Finish the add-on", earned_date_time=None,
                        trophy_type="TrophyType.SILVER", trophy_id=0, trophy_hidden=True)]
        return [Obj(earned=True, trophy_name="First Steps", trophy_detail="Finish the intro",
                    earned_date_time=datetime.datetime(2026, 11, 2, 10, 0), trophy_type="TrophyType.BRONZE", trophy_id=0, trophy_hidden=False),
                Obj(earned=False, trophy_name="Long Road", trophy_detail="", earned_date_time=None, trophy_type="TrophyType.GOLD",
                    trophy_id=1, trophy_hidden=False)]


class FakeClient:
    def __init__(self, npsso_cookie=None):
        pass

    def me(self):
        return FakeUser()


class TitleTrophiesTest(unittest.TestCase):
    NAMES = ("psnawp_api", "psnawp_api.models", "psnawp_api.models.trophies", "psnawp_api.models.trophies.trophy_constants")

    def setUp(self):
        saved = {k: sys.modules.get(k) for k in self.NAMES}
        for name in self.NAMES:
            sys.modules[name] = types.ModuleType(name)
        sys.modules["psnawp_api"].PSNAWP = FakeClient
        sys.modules[self.NAMES[-1]].PlatformType = PlatformType

        def restore():
            for k, v in saved.items():
                if v:
                    sys.modules[k] = v
                else:
                    sys.modules.pop(k, None)
        self.addCleanup(restore)

    def test_all_trophies_of_all_groups_with_earned_flag(self):
        rows = N.title_trophies("example-token", "ExamplePlayer", GAME)
        self.assertEqual([(r["group"], r["name"], r["tier"], r["earned"], r["hidden"]) for r in rows],
                         [("default", "First Steps", "bronze", True, False), ("default", "Long Road", "gold", False, False),
                          ("001", "Add-on Ace", "silver", False, True)])
        self.assertEqual((rows[0]["earned_at"], rows[1]["earned_at"], rows[0]["detail"]), ("2026-11-02T10:00:00", None, "Finish the intro"))

    def test_recent_trophies_still_returns_earned_named_only(self):
        self.assertEqual(N._recent_trophies("example-token", "ExamplePlayer", GAME),
                         [{"name": "First Steps", "tier": "bronze", "earned_at": "2026-11-02T10:00:00", "id": "0"}])

    def test_unknown_title_gives_no_rows(self):
        self.assertEqual(N.title_trophies("example-token", "ExamplePlayer", "zzz"), [])


NODE = shutil.which("node")
CARD_JS = os.path.join(APP, "ui", "card-stat.js")


@unittest.skipUnless(NODE, "node is not installed")
class CardStatScriptTest(unittest.TestCase):
    def js(self, expression):
        code = "const c = require(%s); console.log(JSON.stringify(%s));" % (json.dumps(CARD_JS), expression)
        return json.loads(subprocess.run([NODE, "-e", code], check=True, capture_output=True, text=True, timeout=30).stdout)

    def stat(self, game):
        return self.js("c.cardStat(%s, v => 'D' + v)" % json.dumps(game))

    def test_box_text_for_each_kind(self):
        self.assertEqual(self.stat({"card_stat": {"kind": "quests", "done": 101, "total": 250, "label": "Quests"}}),
                         {"kind": "quests", "label": "Quests", "value": "101 / 250"})
        self.assertEqual(self.stat({"card_stat": {"kind": "trophies", "earned": 24, "total": 55}}),
                         {"kind": "trophies", "label": "Trophies", "value": "24 out of 55 \U0001F3C6"})
        self.assertEqual(self.stat({"card_stat": {"kind": "playtime", "minutes": 356, "tracked": True, "since": "2026-11-01"}}),
                         {"kind": "playtime", "label": "Play time since D2026-11-01", "value": "5 hours 56 min"})
        self.assertEqual(self.stat({"card_stat": {"kind": "playtime", "minutes": 61, "tracked": False, "since": None}}),
                         {"kind": "playtime", "label": "Play time", "value": "1 hour 1 min"})

    def test_no_box_without_data_or_with_zero_minutes(self):
        for game in ({}, {"card_stat": None}, {"playtime_min": 0}, {"card_stat": {"kind": "playtime", "minutes": 0}},
                     {"card_stat": {"kind": "trophies", "earned": 0, "total": 0}}, {"card_stat": {"kind": "quests", "done": 1}}):
            self.assertIsNone(self.stat(game), game)

    def test_merged_platform_entries_use_the_same_order(self):
        members = [{"card_stat": {"kind": "playtime", "minutes": 30, "tracked": False}},
                   {"card_stat": {"kind": "trophies", "earned": 2, "total": 9}},
                   {"card_stat": {"kind": "playtime", "minutes": 45, "tracked": True, "since": "2026-11-01"}}]
        self.assertEqual(self.stat({"platformEntries": members})["value"], "2 out of 9 \U0001F3C6")
        self.assertEqual(self.stat({"platformEntries": [members[0], members[2]]}), {"kind": "playtime", "label": "Play time", "value": "1 hour 15 min"})

    def test_tier_view_counts_and_labels(self):
        listing = {"complete": True, "trophies": psn_rows()[:4]}
        view = self.js("c.tierView(%s, 'bronze')" % json.dumps(listing))
        self.assertEqual((view["title"], view["summary"], len(view["rows"])), ("Bronze trophies", "2 of 3 earned", 3))
        partial = self.js("c.tierView(%s, 'bronze')" % json.dumps({"complete": False, "trophies": psn_rows()[:1]}))
        self.assertEqual(partial["summary"], "1 earned with a recorded name")
        self.assertEqual(self.js("c.tierView(null, 'gold')")["rows"], [])


class ReleaseTest(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(HERE, "..", *parts)) as fh:
            return fh.read()

    def test_versions_match_and_changelog_has_the_entry(self):
        config = re.search(r'^version: "([^"]+)"', self.read("config.yaml"), re.M).group(1)
        self.assertEqual(json.loads(self.read("app", "manifest.json"))["version"], config)
        self.assertEqual(main.VERSION, config)
        self.assertIn("## [%s]" % config, self.read("CHANGELOG.md"))

    def test_ui_wording_and_assets(self):
        ui = self.read("app", "ui", "ui.js")
        self.assertIn("'Last played'", ui)
        self.assertNotIn("'Last seen'", ui)
        self.assertNotIn("row('Play time'", ui)
        self.assertIn("card-stat.js", self.read("app", "ui", "index.html"))
        self.assertIn("'/card-stat.js':'card-stat.js'", self.read("app", "ui_server.py"))


if __name__ == "__main__":
    unittest.main()
