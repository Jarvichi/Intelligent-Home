import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import datetime
import todos


class Lib:
    def __init__(self, rows):
        self.rows, self.marked = rows, []

    def games_without_todo(self):
        return self.rows

    def mark_todo(self, pk, gid):
        self.marked.append((pk, gid))


class Ctx:
    def __init__(self, items, dry=False):
        self.dry, self.calls, self.items, self.logs = dry, [], items, []

    def log(self, m):
        self.logs.append(m)

    def ha(self, path, data=None):
        self.calls.append((path, data))
        if path.startswith("services/todo/get_items"):
            return {"service_response": {"todo.claude_to_do": {"items": self.items}}}
        return {}


def row(t):
    return {"title": t, "id": "i-" + t, "players": ["p1"], "ids": {"p1": "i-" + t}}


class WeeklyTodo(unittest.TestCase):
    def test_creates_one_item_for_many_games(self):
        lib, ctx = Lib([row("A"), row("B"), row("C")]), Ctx([])
        made, _ = todos.sync(lib, ctx, today=datetime.date(2026, 11, 11))
        adds = [c for c in ctx.calls if c[0] == "services/todo/add_item"]
        self.assertEqual(len(adds), 1)
        self.assertEqual(adds[0][1]["item"], "Games: check for new games")
        self.assertEqual(adds[0][1]["due_date"], "2026-11-18")
        self.assertIn("- B (p1)", adds[0][1]["description"])
        self.assertEqual(len(lib.marked), 3)
        self.assertEqual(made, ["A", "B", "C"])

    def test_appends_to_open_item(self):
        item = {"uid": "u1", "summary": "Games: check for new games", "description": "x\n- A (p1)"}
        lib, ctx = Lib([row("A"), row("D")]), Ctx([item])
        todos.sync(lib, ctx)
        upd = [c for c in ctx.calls if c[0] == "services/todo/update_item"]
        self.assertEqual(len(upd), 1)
        self.assertTrue(upd[0][1]["description"].endswith("- D (p1)"))
        self.assertNotIn("add_item", " ".join(c[0] for c in ctx.calls))

    def test_dry_changes_nothing(self):
        lib, ctx = Lib([row("A")]), Ctx([], dry=True)
        todos.sync(lib, ctx)
        self.assertEqual(ctx.calls, [])
        self.assertEqual(lib.marked, [])

    def test_nothing_new(self):
        lib, ctx = Lib([]), Ctx([])
        self.assertEqual(todos.sync(lib, ctx), ([], []))
        self.assertEqual(ctx.calls, [])


if __name__ == "__main__":
    unittest.main()
