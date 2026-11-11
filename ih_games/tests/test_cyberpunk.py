"""Cyberpunk 2077 quest tick list: bundled data file, prerequisite graph, available / locked / missed states, ticks, undo,
progress hook, card stat, API route."""
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
# Prerequisite data recorded when it was fetched (see DOCS.md): quests with a prerequisite from the wiki, quests the wiki
# shows as a first job (no prerequisite), and quests whose prerequisite could not be confirmed (always shown).
WITH_PREREQ, NO_PREREQ, UNKNOWN_PREREQ = 219, 21, 41
LIFEPATHS = {"q000_corpo": "The Corpo-Rat", "q000_nomad": "The Nomad", "q000_street_kid": "The Streetkid"}
NOMAD, CORPO, STREETKID = "q000_nomad", "q000_corpo", "q000_street_kid"
LIFEPATH_ONLY = {CORPO: "mq041_corpo", NOMAD: "mq042_n", STREETKID: "mq017_streetkid"}
RESCUE, RIPPERDOC = "q001_intro", "q001_01_victor"
FIRESTARTER, KILLING_MOON, BLACK_STEEL = "q304_deal", "q306_devils_bargain", "q305_prison_convoy"
BLACK_STEEL_PATH = {"q305_prison_convoy", "q305_bunker", "q305_border_crossing", "q305_reed_epilogue", "q305_postcontent"}
REED_EPILOGUE, MOON_EPILOGUE, TOWER, TOWER_EPILOGUE = "q306_reed_epilogue", "q306_somi_epilogue", "q307_before_tomorrow", "q307_tomorrow"
NOCTURNE = "02_sickness"
UNKNOWN_ID = "mq049_edgerunners"      # a quest marked requires_unknown
PICKUP, INFORMATION = "q003_maelstrom", "q004_braindance"
PATCH_AVAILABLE_AT_START = 3          # the fourth patch quest, You Tell Me, needs Fool on the Hill


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


class PrerequisiteDataTests(unittest.TestCase):
    def setUp(self):
        with open(C.QUESTS_FILE, encoding="utf-8") as f:
            self.doc = json.load(f)
        self.quests = self.doc["quests"]
        self.by = {q["id"]: q for q in self.quests}

    def test_graph_has_no_unknown_ids_and_no_cycles(self):
        self.assertEqual(C.graph_problems(self.quests), [])

    def test_graph_check_finds_faults(self):
        base = {"name": "x", "group": "base", "type": "side"}
        rows = [dict(base, id="a", requires=["b"]), dict(base, id="b", requires_any=["c"]), dict(base, id="c", requires=["a"]),
                dict(base, id="d", requires=["nope"], excludes=["d"]), dict(base, id="e", requires=["a"], excludes=["a"])]
        text = " | ".join(C.graph_problems(rows))
        for part in ("cycle: a -> b -> c -> a", "d: unknown id nope in requires", "d names itself in excludes", "e both needs and excludes a"):
            self.assertIn(part, text)

    def test_field_types(self):
        for q in self.quests:
            for k in C.LINK_FIELDS:
                self.assertIsInstance(q.get(k, []), list, q["id"])
                self.assertEqual(len(set(q.get(k, []))), len(q.get(k, [])), q["id"])
            for k in C.TEXT_FIELDS:
                self.assertTrue(q.get(k, "x").strip(), q["id"])
            self.assertIn(q.get("requires_unknown", True), (True,), q["id"])

    def test_unknown_quests_have_no_prerequisite_and_counts_match(self):
        unknown = [q for q in self.quests if q.get("requires_unknown")]
        self.assertFalse(any(q.get("requires") or q.get("requires_any") for q in unknown))
        known = [q for q in self.quests if not q.get("requires_unknown")]
        with_prereq = [q for q in known if q.get("requires") or q.get("requires_any")]
        self.assertEqual((len(with_prereq), len(known) - len(with_prereq), len(unknown)), (WITH_PREREQ, NO_PREREQ, UNKNOWN_PREREQ))
        self.assertEqual(WITH_PREREQ + NO_PREREQ + UNKNOWN_PREREQ, TOTAL)

    def test_lifepaths_exclude_each_other(self):
        for i in LIFEPATHS:
            self.assertEqual(set(self.by[i]["excludes"]), set(LIFEPATHS) - {i})
            self.assertTrue(self.by[i]["choice"])
            self.assertIn(i, self.by[LIFEPATH_ONLY[i]]["requires"])
        self.assertEqual(set(self.by[RESCUE]["requires_any"]), set(LIFEPATHS))

    def test_exclusions_are_mutual_and_explained(self):
        for q in self.quests:
            for i in q.get("excludes", []):
                self.assertIn(q["id"], self.by[i].get("excludes", []), "%s -> %s" % (q["id"], i))
            if q.get("excludes"):
                self.assertTrue(q.get("choice"), q["id"])

    def test_source_record(self):
        rec = self.doc["prerequisites"]
        self.assertRegex(rec["fetched"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("https://", rec["source"])
        self.assertEqual(set(rec["fields"]), set(C.LINK_FIELDS) | set(C.TEXT_FIELDS) | {"requires_unknown"})


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tc = ToolCtx(self.tmp.name, {})

    def run_cmd(self, name, *args):
        return C.commands[name](self.tc, PLAYER, list(args))

    def tick(self, *ids):
        C.save_state(self.tc, PLAYER, {C.STATE_KEY: C.load_state(self.tc, PLAYER)[C.STATE_KEY] + list(ids)})

    def ids(self, rows):
        return {r["id"] for r in rows}

    def test_start_every_quest_is_available_or_locked(self):
        avail, lock = C.available(self.tc, PLAYER), C.locked(self.tc, PLAYER)
        self.assertEqual((len(avail), len(lock), C.blocked(self.tc, PLAYER)), (NO_PREREQ + UNKNOWN_PREREQ, WITH_PREREQ, []))
        self.assertTrue(set(LIFEPATHS) | {UNKNOWN_ID} <= self.ids(avail))
        self.assertIn(RESCUE, self.ids(lock))

    def test_prerequisite_unlocks_and_untick_locks_again(self):
        row = next(r for r in C.locked(self.tc, PLAYER) if r["id"] == RESCUE)
        self.assertEqual(row["reason"], "Needs one of: %s." % ", ".join(LIFEPATHS.values()))
        self.tick(NOMAD)
        self.assertIn(RESCUE, self.ids(C.available(self.tc, PLAYER)))
        row = next(r for r in C.locked(self.tc, PLAYER) if r["id"] == RIPPERDOC)
        self.assertEqual((row["state"], row["reason"]), (C.LOCKED, "Needs: The Rescue."))
        self.run_cmd("done", RESCUE)
        self.assertIn(RIPPERDOC, self.ids(C.available(self.tc, PLAYER)))
        self.assertNotIn(RESCUE, self.ids(C.available(self.tc, PLAYER)))
        self.run_cmd("undone", RESCUE)
        self.assertIn(RIPPERDOC, self.ids(C.locked(self.tc, PLAYER)))

    def test_requires_needs_all_of_them(self):
        heist = lambda: next(r for r in C.quest_rows(self.tc, PLAYER) if r["id"] == MAIN_ID)
        self.tick(PICKUP)
        self.assertEqual((heist()["state"], heist()["reason"]), (C.LOCKED, "Needs: The Information."))
        self.tick(INFORMATION)
        self.assertEqual((heist()["state"], heist()["reason"]), (C.AVAILABLE, ""))

    def test_lifepath_blocks_the_other_two_with_the_reason(self):
        answer = self.run_cmd("done", NOMAD)
        self.assertTrue(answer.startswith("Marked done: The Nomad. Now missed this run: "), answer)
        missed = {r["id"]: r for r in C.blocked(self.tc, PLAYER)}
        self.assertEqual(set(missed), {CORPO, STREETKID, LIFEPATH_ONLY[CORPO], LIFEPATH_ONLY[STREETKID]})
        self.assertTrue(missed[CORPO]["reason"].startswith("Blocked by The Nomad. Lifepath"), missed[CORPO]["reason"])
        self.assertTrue(missed[LIFEPATH_ONLY[CORPO]]["reason"].startswith("Needs The Corpo-Rat, which is blocked by The Nomad."))
        self.assertEqual(missed[CORPO]["blocked_by"], "The Nomad")
        self.assertNotIn(LIFEPATH_ONLY[NOMAD], missed)
        answer = self.run_cmd("undone", NOMAD)
        self.assertTrue(answer.startswith("Marked not done: The Nomad. Possible again: "), answer)
        self.assertEqual(C.blocked(self.tc, PLAYER), [])

    def test_available_row_warns_what_a_tick_rules_out(self):
        row = next(r for r in C.available(self.tc, PLAYER) if r["id"] == NOMAD)
        self.assertEqual(set(row["blocks"]), {"The Corpo-Rat", "The Streetkid", "War Pigs", "Small Man, Big Mouth"})
        self.assertTrue(row["hint"].startswith("Lifepath"))
        self.assertEqual(next(r for r in C.available(self.tc, PLAYER) if r["id"] == UNKNOWN_ID)["hint"], C.UNKNOWN_HINT)

    def test_branch_choice_blocks_the_other_path_and_what_follows_it(self):
        self.tick(FIRESTARTER)
        self.assertTrue({KILLING_MOON, BLACK_STEEL} <= self.ids(C.available(self.tc, PLAYER)))
        self.run_cmd("done", KILLING_MOON)
        self.assertEqual(self.ids(C.blocked(self.tc, PLAYER)), BLACK_STEEL_PATH)
        self.run_cmd("done", MOON_EPILOGUE)
        missed = {r["id"]: r for r in C.blocked(self.tc, PLAYER)}
        self.assertEqual(set(missed), BLACK_STEEL_PATH | {REED_EPILOGUE, TOWER, TOWER_EPILOGUE})
        self.assertIn("Choice", missed[REED_EPILOGUE]["reason"])
        self.run_cmd("undone", MOON_EPILOGUE)
        self.assertNotIn(TOWER, self.ids(C.blocked(self.tc, PLAYER)))   # one of its two ways in is possible again

    def test_requires_any_is_missed_only_when_every_way_in_is_missed(self):
        self.tick(FIRESTARTER, BLACK_STEEL)
        self.assertNotIn(TOWER, self.ids(C.blocked(self.tc, PLAYER)))
        self.assertIn(TOWER, self.ids(C.locked(self.tc, PLAYER)))

    def test_endings_do_not_block_each_other(self):
        self.tick(NOCTURNE, "q113_rescuing_hanako", "q113_corpo", "q201_heir", "q115_afterlife", "09_solo")
        self.assertEqual(C.blocked(self.tc, PLAYER), [])

    def test_ticked_quest_is_never_missed_and_total_stays(self):
        self.tick(NOMAD, CORPO)
        self.assertNotIn(CORPO, self.ids(C.blocked(self.tc, PLAYER)))
        d = C.details(self.tc, PLAYER)
        self.assertEqual((d["done"], d["total"]), (2, TOTAL))
        self.assertEqual(d["done"] + d["available"] + d["locked"] + d["missed"], TOTAL)

    def test_details_rows_and_counts(self):
        self.tick(NOMAD)
        d = C.details(self.tc, PLAYER)
        self.assertEqual((d["done"], d["total"], d["missed"]), (1, TOTAL, 4))
        self.assertEqual(sum(g["missed"] for g in d["groups"]), 4)
        for g in d["groups"]:
            self.assertEqual(g["done"] + g["available"] + g["locked"] + g["missed"], g["total"])
            for r in g["quests"]:
                self.assertIn(r["state"], (C.DONE, C.AVAILABLE, C.LOCKED, C.MISSED))
                self.assertEqual(bool(r["reason"]), r["state"] in (C.LOCKED, C.MISSED), r["id"])
        self.assertEqual(C.progress(self.tc, PLAYER), {"done": 1, "total": TOTAL, "label": C.PROGRESS_LABEL})

    def test_missed_and_available_commands(self):
        self.assertEqual(self.run_cmd("missed"), "No quests missed this run.")
        self.assertTrue(self.run_cmd("available").startswith("%d available: The Corpo-Rat, " % (NO_PREREQ + UNKNOWN_PREREQ)))
        self.assertTrue(self.run_cmd("available", "nope").startswith("Use: available"))
        self.assertEqual(self.run_cmd("available", "patch").split(":")[0], "%d available" % PATCH_AVAILABLE_AT_START)
        self.run_cmd("done", NOMAD)
        self.assertEqual(self.run_cmd("missed"), "4 missed this run: The Corpo-Rat (blocked by The Nomad), The Streetkid (blocked by "
                         "The Nomad), Small Man, Big Mouth (blocked by The Nomad), War Pigs (blocked by The Nomad).")
        self.assertNotIn("The Corpo-Rat", self.run_cmd("available"))
        self.assertIn("Missed this run: 4.", self.run_cmd("status"))
        state, attrs = C.sensor(self.tc, PLAYER)
        self.assertEqual((attrs["missed"], attrs["done"], attrs["total"]), (4, 1, TOTAL))


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
