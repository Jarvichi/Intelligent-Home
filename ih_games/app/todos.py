"""'Games: research ways to track progress/help for <title>' to-do items (owner request, 31 Oct 2026).
Created on todo.claude_to_do through the add-on's own HA access (homeassistant_api: true). Idempotent: an open item whose
summary or description mentions the title (case-insensitive) blocks a new one. Games with a tool module never get one."""
import datetime
import library as L

ENTITY = "todo.claude_to_do"
DUE_DAYS = 14


def open_items(ha, entity=ENTITY):
    r = ha("services/todo/get_items?return_response", {"entity_id": entity, "status": ["needs_action"]})
    sr = (r or {}).get("service_response") or {}
    return (sr.get(entity) or {}).get("items") or []


def mentions(items, title):
    n = L.norm(title)
    return any(n and n in L.norm((i.get("summary") or "") + " " + (i.get("description") or "")) for i in items)


def sync(lib, ctx, today=None, entity=ENTITY):
    """Create missing to-dos. Dry: only logs. Returns (created titles, skipped titles)."""
    today = today or datetime.date.today()
    due = (today + datetime.timedelta(days=DUE_DAYS)).isoformat()
    cand = lib.games_without_todo()
    if not cand:
        return [], []
    items = open_items(ctx.ha, entity)
    created, skipped = [], []
    for r in cand:
        if mentions(items, r["title"]):
            skipped.append(r["title"])
            for pk, gid in r["ids"].items():
                if not ctx.dry:
                    lib.mark_todo(pk, gid)
            continue
        desc = ("Research and try ways to track progress/help/etc for %s (library entry %s, players: %s). "
                "Check free APIs/data sets, community tools; propose ONE option to the player via their assistant DM. "
                "How to add a tool once approved: brain note projects/ih-games-hub-design, section 'Adding a game tool' (games_cli.py newtool)"
                % (r["title"], r["id"], ", ".join(r["players"])))
        if ctx.dry:
            ctx.log("[DRY] would add to-do: Games: research ways to track progress/help for %s" % r["title"])
            continue
        ctx.ha("services/todo/add_item", {"entity_id": entity, "item": "Games: research ways to track progress/help for %s" % r["title"],
                                          "description": desc, "due_date": due})
        for pk, gid in r["ids"].items():
            lib.mark_todo(pk, gid)
        created.append(r["title"])
    return created, skipped
