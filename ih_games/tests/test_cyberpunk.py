"""Cyberpunk 2077 quest tick list: bundled data file, ticks, undo, progress hook, card stat, API route."""
import collections, json, os, re, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import card_stat  # noqa: E402
import main  # noqa: E402
from tools import ToolCtx, all_tools  # noqa: E402
from tools import cyberpunk as C  # noqa: E402

PLAYER, OTHER = "player_a", "player_b"
GAME = "Cyberpunk 2077"
# Counts recorded when the list was fetched (see DOCS.md). A change to the data file must be a deliberate one.
EXPECTED = {("base", "main"): 42, ("base", "side"): 112, ("base", "gig"): 72,
            ("phantom_liberty", "main"): 22, ("phantom_liberty", "side"): 20, ("phantom_liberty", "gig"): 9,
            ("patch", "side"): 4}
TOTAL = sum(EXPECTED.values())
MAIN_ID, MAIN_NAME = "q005_heist", "The Heist"
PL_ID = "q301_crash"
PATCH_NAMES = {"Over the Edge", "Clampdown", "Welcome to Paradise", "You Tell Me"}
ID_PATTERN = r"[a-z0-9_]+"


class DataFileTests(unittest.TestCase):
    def setUp(self):
        with open(C.QUESTS_FILE, encoding="utf-8") as f:
            self.doc = json.load(f)
        self.quests = self.doc["quests"]

    def test_counts_per_group_and_type(self):
        self.assertEqual(dict(collections.Counter((q["group"], q["type"]) for q in self.quests)), EXPECTED)
        self.assertEqual(len(C.quests()), TOTAL)

    def test_fields_ids_and_names(self):
        groups, types = dict(C.GROUPS), dict(C.TYPES)
        for q in self.quests:
            self.assertRegex(q["id"], "^%s$" % ID_PATTERN)
            self.assertTrue(q["name"].strip())
            self.assertIn(q["group"], groups)
            self.assertIn(q["type"], types)
        self.assertEqual(len({q["id"] for q in self.quests}), TOTAL)
        self.assertEqual(len({q["name"].lower() for q in self.quests}), TOTAL)

    def test_patch_group_and_source_record(self):
        self.assertEqual({q["name"] for q in self.quests if q["group"] == "patch"}, PATCH_NAMES)
        self.assertTrue(all(q.get("patch") for q in self.quests if q["group"] == "patch"))
        self.assertRegex(self.doc["fetched"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertGreaterEqual(len(self.doc["sources"]), 2)
        self.assertTrue(all(s["url"].startswith("https://") for s in self.doc["sources"]))


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tc = ToolCtx(self.tmp.name, {})

    def run_cmd(self, name, *args, player=PLAYER):
        return C.commands[name](self.tc, player, list(args))

    def test_matches_and_discovery(self):
        self.assertTrue(C.matches({"title": GAME}))
        self.assertTrue(C.matches({"title": "Cyberpunk 2077: Phantom Liberty"}))
        self.assertFalse(C.matches({"title": "Another Game"}))
        self.assertIn("cyberpunk", [t.NAME for t in all_tools()])

    def test_starts_with_nothing_ticked(self):
        self.assertEqual(C.progress(self.tc, PLAYER), {"done": 0, "total": TOTAL, "label": C.PROGRESS_LABEL})
        d = C.details(self.tc, PLAYER)
        self.assertEqual((d["done"], d["total"]), (0, TOTAL))
        self.assertEqual([(g["id"], g["done"], g["total"]) for g in d["groups"]],
                         [("base", 0, 226), ("phantom_liberty", 0, 51), ("patch", 0, 4)])
        self.assertFalse(any(q["done"] for g in d["groups"] for q in g["quests"]))
        self.assertFalse(os.path.exists(C.state_path(self.tc, PLAYER)))

    def test_done_undone_roundtrip_by_id_and_name(self):
        self.assertEqual(self.run_cmd("done", MAIN_ID), "Marked done: %s." % MAIN_NAME)
        self.assertEqual(self.run_cmd("done", MAIN_ID), "Marked done: %s." % MAIN_NAME)
        self.assertEqual(C.progress(self.tc, PLAYER)["done"], 1)
        row = next(q for q in C.details(self.tc, PLAYER)["groups"][0]["quests"] if q["id"] == MAIN_ID)
        self.assertEqual((row["done"], row["type"], row["type_label"]), (True, "main", "Main jobs"))
        self.assertTrue(self.run_cmd("done", "the", "heist").startswith("Marked done"))
        self.assertEqual(C.progress(self.tc, PLAYER)["done"], 1)
        self.assertEqual(self.run_cmd("undone", "The", "Heist"), "Marked not done: %s." % MAIN_NAME)
        self.assertEqual(C.progress(self.tc, PLAYER)["done"], 0)
        self.assertTrue(self.run_cmd("undone", MAIN_ID).startswith("Marked not done"))

    def test_group_counts_and_players_are_separate(self):
        self.run_cmd("done", MAIN_ID)
        self.run_cmd("done", PL_ID)
        self.run_cmd("done", "Clampdown")
        self.assertEqual([g["done"] for g in C.details(self.tc, PLAYER)["groups"]], [1, 1, 1])
        self.assertEqual(C.progress(self.tc, OTHER)["done"], 0)
        self.assertIn("3 of %d" % TOTAL, self.run_cmd("status"))

    def test_unknown_and_ambiguous_names_change_nothing(self):
        self.assertIn("not found", self.run_cmd("done", "no", "such", "quest"))
        self.assertIn("not found", self.run_cmd("done"))
        answer = self.run_cmd("done", "epistrophy")
        self.assertTrue(answer.startswith("Marked done: Epistrophy."), answer)   # an exact name wins over longer names
        many = self.run_cmd("done", "epistrophy", "north")
        self.assertTrue(many.startswith("2 quests match: "), many)
        self.assertEqual(C.progress(self.tc, PLAYER)["done"], 1)

    def test_unknown_ids_in_state_are_not_counted(self):
        C.save_state(self.tc, PLAYER, {C.STATE_KEY: [MAIN_ID, "not_a_quest"]})
        self.assertEqual(C.progress(self.tc, PLAYER)["done"], 1)

    def test_broken_state_file_reads_as_empty(self):
        os.makedirs(os.path.dirname(C.state_path(self.tc, PLAYER)))
        for text in ("{not json", "[]", '{"quests_done": 7}'):
            with open(C.state_path(self.tc, PLAYER), "w") as f:
                f.write(text)
            self.assertEqual(C.progress(self.tc, PLAYER)["done"], 0, text)

    def test_left_command(self):
        self.assertTrue(self.run_cmd("left", "patch").startswith("4 left: "))
        for name in PATCH_NAMES:
            self.run_cmd("done", name)
        self.assertEqual(self.run_cmd("left", "patch"), "No quests left.")
        self.assertTrue(self.run_cmd("left").startswith("%d left: " % (TOTAL - len(PATCH_NAMES))))
        self.assertTrue(self.run_cmd("left", "nope").startswith("Use: left"))

    def test_sensor_and_summary(self):
        self.run_cmd("done", MAIN_ID)
        state, attrs = C.sensor(self.tc, PLAYER)
        self.assertEqual(state, "1 / %d quests" % TOTAL)
        self.assertEqual((attrs["done"], attrs["total"], attrs["error"]), (1, TOTAL, ""))
        self.assertIn("Base game 1/226", C.summary(self.tc, PLAYER))
        self.assertEqual(C.upcoming(self.tc, PLAYER), [])

    def test_missing_quest_file(self):
        self.assertEqual(C.quests(os.path.join(self.tmp.name, "absent.json")), [])

    def test_card_stat_shows_ticked_over_total(self):
        self.run_cmd("done", MAIN_ID)
        self.assertEqual(card_stat.tool_progress(C, self.tc, PLAYER),
                         {"kind": card_stat.KIND_QUESTS, "done": 1, "total": TOTAL, "label": C.PROGRESS_LABEL})


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mod = main.Module({"players": [{"key": PLAYER}]}, data=self.tmp.name)
        self.game = self.mod.library.add(PLAYER, GAME, "psn", None)

    def test_library_details_and_cmd(self):
        self.assertEqual(self.game["tools"], ["cyberpunk"])
        gid = self.game["id"]
        code, obj = self.mod.api("POST", "/games/%s/cmd" % gid, {"player": PLAYER}, {"cmd": "done", "args": [MAIN_ID]})
        self.assertEqual((code, obj["text"]), (200, "Marked done: %s." % MAIN_NAME))
        code, obj = self.mod.api("GET", "/games/%s/details" % gid, {"player": PLAYER}, None)
        self.assertEqual((code, obj["ui_component"], obj["details"]["done"], obj["details"]["total"]), (200, "cyberpunk", 1, TOTAL))
        code, obj = self.mod.api("GET", "/library", {"player": PLAYER}, None)
        stat = next(g for g in obj["games"] if g["id"] == gid)["card_stat"]
        self.assertEqual((stat["kind"], stat["done"], stat["total"]), (card_stat.KIND_QUESTS, 1, TOTAL))
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "cyberpunk", "state_%s.json" % PLAYER)))

    def test_ui_component_file_registers_the_same_name(self):
        path = os.path.join(os.path.dirname(C.QUESTS_FILE), "..", "ui", "games", C.UI_COMPONENT + ".js")
        with open(path, encoding="utf-8") as f:
            self.assertTrue(re.search(r"h\.%s\s*=" % C.UI_COMPONENT, f.read()))


if __name__ == "__main__":
    unittest.main()
