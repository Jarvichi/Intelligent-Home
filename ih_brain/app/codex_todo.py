"""Codex to-do tools for ih_brain: list, add, note, complete on ONE Home Assistant to-do list (todo.claude_to_do), nothing else.
Why: Codex's bundled Home Assistant MCP cannot read to-do items (owner request, 10 Nov 2026). These tools run through this add-on's own
Home Assistant access and are served on /codex-todo-mcp behind a separate token (codex_todo_token)."""
import datetime
import json
import os
import urllib.request

ENTITY = "todo.claude_to_do"
HA_API = "http://supervisor/core/api/"
HA_TIMEOUT_S = 15
MAX_TEXT_CHARS = 2000        # one summary, note or description
MAX_LIST_ITEMS = 100         # items returned by todo_list
STATUS_OPEN, STATUS_DONE = "needs_action", "completed"
NOTE_AUTHOR_DEFAULT = "codex"
CODEX_TODO_INSTRUCTIONS = "Claude to-do list (todo.claude_to_do) only: list open items, add an item, add a note to an item, mark an item done."


def _tool(name, desc, props=None, required=None):
    return {"name": name, "description": desc, "inputSchema": {"type": "object", "properties": props or {}, "required": required or []}}


UID = {"type": "string", "description": "item uid from todo_list"}
TOOLS = [
    _tool("todo_list", "List open items on the Claude to-do list (uid, summary, due, description).",
          {"include_done": {"type": "boolean", "description": "also list completed items"}}),
    _tool("todo_add", "Add an item. Give a due date (YYYY-MM-DD) when there is one.",
          {"summary": {"type": "string"}, "description": {"type": "string"}, "due": {"type": "string"}}, ["summary"]),
    _tool("todo_note", "Add a dated note to an item (appended to its description; earlier text is kept).",
          {"uid": UID, "note": {"type": "string"}, "author": {"type": "string"}}, ["uid", "note"]),
    _tool("todo_complete", "Mark an item done. Optionally add a closing note first.",
          {"uid": UID, "note": {"type": "string"}, "author": {"type": "string"}}, ["uid"]),
]


def ha_call(path, body, token=None, opener=urllib.request.urlopen):
    token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        raise RuntimeError("no Home Assistant access (SUPERVISOR_TOKEN missing; homeassistant_api must be on)")
    req = urllib.request.Request(HA_API + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with opener(req, timeout=HA_TIMEOUT_S) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def _text(v, name, required=False):
    v = str(v or "").strip()
    if required and not v:
        raise ValueError("%s is required" % name)
    if len(v) > MAX_TEXT_CHARS:
        raise ValueError("%s is longer than %d characters" % (name, MAX_TEXT_CHARS))
    return v


class CodexTodo:
    def __init__(self, ha=ha_call, today=datetime.date.today):
        self.ha, self.today = ha, today

    def _items(self, statuses):
        r = self.ha("services/todo/get_items?return_response", {"entity_id": ENTITY, "status": statuses})
        return (((r or {}).get("service_response") or {}).get(ENTITY) or {}).get("items") or []

    def _find(self, uid):
        uid = _text(uid, "uid", True)
        for it in self._items([STATUS_OPEN, STATUS_DONE]):
            if it.get("uid") == uid:
                return it
        raise ValueError("no item with that uid on the Claude to-do list")

    def _append(self, item, note, author):
        stamp = "[%s %s] %s" % (self.today().isoformat(), _text(author, "author") or NOTE_AUTHOR_DEFAULT, note)
        old = (item.get("description") or "").rstrip()
        return (old + "\n" if old else "") + stamp

    def t_todo_list(self, a):
        want = [STATUS_OPEN, STATUS_DONE] if a.get("include_done") is True else [STATUS_OPEN]
        rows = [{k: it.get(k) for k in ("uid", "summary", "status", "due", "description")} for it in self._items(want)]
        return json.dumps(rows[:MAX_LIST_ITEMS], indent=1)

    def t_todo_add(self, a):
        body = {"entity_id": ENTITY, "item": _text(a.get("summary"), "summary", True)}
        if a.get("description"):
            body["description"] = _text(a["description"], "description")
        if a.get("due"):
            body["due_date"] = datetime.date.fromisoformat(str(a["due"])).isoformat()
        self.ha("services/todo/add_item", body)
        return "added: " + body["item"]

    def t_todo_note(self, a):
        item = self._find(a.get("uid"))
        desc = self._append(item, _text(a.get("note"), "note", True), a.get("author"))
        self.ha("services/todo/update_item", {"entity_id": ENTITY, "item": item["uid"], "description": desc})
        return "note added to: " + item.get("summary", "")

    def t_todo_complete(self, a):
        item = self._find(a.get("uid"))
        body = {"entity_id": ENTITY, "item": item["uid"], "status": STATUS_DONE}
        if a.get("note"):
            body["description"] = self._append(item, _text(a["note"], "note"), a.get("author"))
        self.ha("services/todo/update_item", body)
        return "completed: " + item.get("summary", "")
