"""Codex to-do tools: one list only, notes keep earlier text, token separate from the read token."""
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import codex_todo
import mcp

ITEMS = [{"uid": "u1", "summary": "Fix art", "status": "needs_action", "description": "old text", "due": "2026-11-12"},
         {"uid": "u2", "summary": "Done thing", "status": "completed"}]


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, path, body):
        self.calls.append((path, body))
        if "get_items" in path:
            want = body["status"]
            return {"service_response": {body["entity_id"]: {"items": [i for i in ITEMS if i["status"] in want]}}}
        return {}


class T(unittest.TestCase):
    def setUp(self):
        self.ha = Fake()
        self.t = codex_todo.CodexTodo(self.ha, today=lambda: __import__("datetime").date(2026, 11, 10))

    def test_list_open_only(self):
        rows = json.loads(self.t.t_todo_list({}))
        self.assertEqual([r["uid"] for r in rows], ["u1"])

    def test_every_call_uses_the_one_list(self):
        self.t.t_todo_add({"summary": "x", "due": "2026-11-20"})
        self.t.t_todo_note({"uid": "u1", "note": "hi"})
        self.t.t_todo_complete({"uid": "u1"})
        self.assertTrue(all(b["entity_id"] == codex_todo.ENTITY for _, b in self.ha.calls))

    def test_note_appends(self):
        self.t.t_todo_note({"uid": "u1", "note": "checked", "author": "codex"})
        body = self.ha.calls[-1][1]
        self.assertEqual(body["description"], "old text\n[2026-11-10 codex] checked")

    def test_complete_with_note(self):
        self.t.t_todo_complete({"uid": "u1", "note": "done"})
        body = self.ha.calls[-1][1]
        self.assertEqual(body["status"], "completed")
        self.assertIn("done", body["description"])

    def test_unknown_uid_and_bad_input(self):
        with self.assertRaises(ValueError):
            self.t.t_todo_note({"uid": "nope", "note": "x"})
        with self.assertRaises(ValueError):
            self.t.t_todo_add({"summary": ""})
        with self.assertRaises(ValueError):
            self.t.t_todo_add({"summary": "x" * (codex_todo.MAX_TEXT_CHARS + 1)})

    def test_rpc_lists_only_todo_tools(self):
        r = mcp.handle_rpc(self.t, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, tools=codex_todo.TOOLS)
        self.assertEqual({t["name"] for t in r["result"]["tools"]}, {"todo_list", "todo_add", "todo_note", "todo_complete"})
        r = mcp.handle_rpc(self.t, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "brain_write", "arguments": {}}},
                           tools=codex_todo.TOOLS)
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
