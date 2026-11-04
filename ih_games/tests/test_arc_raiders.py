"""Arc Raiders tool: details ids and undo-capable commands. Synthetic game data only."""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from tools import ToolCtx  # noqa: E402
from tools import arc_raiders as A  # noqa: E402


def _w(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w"))


def make_data(root):
    n = lambda s: {"en": s}
    _w(root + "/quests/q_one.json", {"id": "q_one", "name": n("First Quest"), "map": ["m1"], "requiredItemIds": [{"itemId": "i_a", "quantity": 3}]})
    _w(root + "/quests/q_two.json", {"id": "q_two", "name": n("Second Quest"), "previousQuestIds": ["q_one"], "map": []})
    _w(root + "/items/i_a.json", {"id": "i_a", "name": n("Widget"), "value": 10})
    _w(root + "/hideout/bench_x.json", {"id": "bench_x", "name": n("Bench X"), "maxLevel": 3,
       "levels": [{"level": i, "requirementItemIds": [{"itemId": "i_a", "quantity": 1}]} for i in (1, 2, 3)]})
    _w(root + "/projects.json", [{"id": "proj_y", "name": n("Project Y"), "phases": [{"phase": 1, "requirementItemIds": []}, {"phase": 2, "requirementItemIds": []}]}])
    _w(root + "/maps.json", [{"id": "m1", "name": n("Map One")}])


class ArcTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        make_data(self.tmp.name + "/arc_data")
        A._cache.clear()
        self.tc = ToolCtx(self.tmp.name, {"arc_data_dir": self.tmp.name + "/arc_data"})

    def run_cmd(self, name, *args):
        return A.commands[name](self.tc, "p1", list(args))

    def test_details_ids(self):
        d = A.details(self.tc, "p1")
        self.assertEqual([q["id"] for q in d["open_quests"]], ["q_one"])
        self.assertEqual(d["hideout"], [{"id": "bench_x", "name": "Bench X", "level": 0, "max_level": 3}])
        self.assertEqual(d["projects"], [{"id": "proj_y", "name": "Project Y", "active": False, "done_phases": 0, "total_phases": 2}])
        self.assertEqual(d["needs"][0]["id"], "i_a")

    def test_done_undone_roundtrip(self):
        self.assertTrue(self.run_cmd("done", "q_one").startswith("Marked done"))
        d = A.details(self.tc, "p1")
        self.assertEqual([q["id"] for q in d["open_quests"]], ["q_two"])
        self.assertTrue(self.run_cmd("undone", "q_one").startswith("Marked not done"))
        self.assertEqual([q["id"] for q in A.details(self.tc, "p1")["open_quests"]], ["q_one"])
        self.assertIn("not found", self.run_cmd("undone", "nope"))

    def test_done_quests_list(self):
        self.assertEqual(A.details(self.tc, "p1")["done_quests"], [])
        self.run_cmd("done", "q_two")
        self.run_cmd("done", "q_one")
        self.assertEqual(A.details(self.tc, "p1")["done_quests"], [{"id": "q_one", "name": "First Quest"}, {"id": "q_two", "name": "Second Quest"}])
        self.run_cmd("undone", "q_one")
        self.assertEqual([q["id"] for q in A.details(self.tc, "p1")["done_quests"]], ["q_two"])

    def test_hideout_set_checked(self):
        self.assertTrue(self.run_cmd("hideout_set", "bench_x", "2").startswith("Saved"))
        self.assertEqual(A.details(self.tc, "p1")["hideout"][0]["level"], 2)
        self.assertIn("must be 0 to 3", self.run_cmd("hideout_set", "bench_x", "4"))
        self.assertIn("Unknown", self.run_cmd("hideout_set", "zzz", "1"))
        self.assertEqual(A.details(self.tc, "p1")["hideout"][0]["level"], 2)

    def test_project_set_checked(self):
        self.assertTrue(self.run_cmd("project_set", "proj_y", "1").startswith("Saved"))
        p = A.details(self.tc, "p1")["projects"][0]
        self.assertEqual((p["active"], p["done_phases"]), (True, 1))
        self.assertIn("must be 0 to 2", self.run_cmd("project_set", "proj_y", "3"))
        self.assertIn("Unknown", self.run_cmd("project_set", "nope", "1"))

    def test_have_in_inventory_rows(self):
        self.run_cmd("have", "i_a", "5")
        row = A.details(self.tc, "p1")["inventory"][0]
        self.assertEqual((row["id"], row["have"]), ("i_a", 5))

    def test_old_commands_unchanged(self):
        self.assertTrue(self.run_cmd("hideout", "bench_x", "9").startswith("Saved"))
        self.assertTrue(self.run_cmd("project", "proj_y", "7").startswith("Saved"))
        self.assertTrue({"need", "maps", "inv", "done", "have", "hideout", "project"} <= set(A.commands))


if __name__ == "__main__":
    unittest.main()
