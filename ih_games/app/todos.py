"""One standing 'Games: check for new games' to-do (owner request, 11 Nov 2026; replaces one to-do per game, which flooded the list with 100+ items).
Created on todo.claude_to_do through the add-on's own HA access (homeassistant_api: true). New library games that have no tool and no
to-do yet are listed in the description of the ONE open item (created when none is open, due in DUE_DAYS). Each game is marked once, so it is
listed once. Games with a tool module never appear."""
import datetime
import library as L

ENTITY = "todo.claude_to_do"
DUE_DAYS = 7
SUMMARY = "Games: check for new games"
MAX_LISTED = 60
HOW = ("Research and try ways to track progress/help/etc for each game listed below (check free APIs/data sets, community tools; "
       "propose ONE option to the player via their assistant DM). How to add a tool once approved: brain note "
       "projects/ih-games-hub-design, section 'Adding a game tool' (games_cli.py newtool). Tick the item when the list is dealt with; "
       "a new item appears when more games arrive.")


def open_items(ha, entity=ENTITY):
    r = ha("services/todo/get_items?return_response", {"entity_id": entity, "status": ["needs_action"]})
    sr = (r or {}).get("service_response") or {}
    return (sr.get(entity) or {}).get("items") or []


def summary_for(title=None):
    return SUMMARY


def standing_item(items):
    """The open 'check for new games' item, or None."""
    n = L.norm(SUMMARY)
    for i in items:
        if L.norm(i.get("summary") or "") == n:
            return i
    return None


def _lines(rows):
    return "\n".join("- %s (%s)" % (r["title"], ", ".join(r["players"])) for r in rows[:MAX_LISTED]) + (
        "\n- ... and %d more" % (len(rows) - MAX_LISTED) if len(rows) > MAX_LISTED else "")


def sync(lib, ctx, today=None, entity=ENTITY):
    """Add new games to the standing to-do. Dry: only logs. Returns (listed titles, []). Second value kept for the API shape."""
    today = today or datetime.date.today()
    cand = lib.games_without_todo()
    if not cand:
        return [], []
    titles = [r["title"] for r in cand]
    if ctx.dry:
        ctx.log("[DRY] would list %d new game(s) in the to-do '%s'" % (len(cand), SUMMARY))
        return [], []
    item = standing_item(open_items(ctx.ha, entity))
    if item:
        old = item.get("description") or ""
        fresh = [r for r in cand if L.norm(r["title"]) not in L.norm(old)]
        if fresh:
            ctx.ha("services/todo/update_item", {"entity_id": entity, "item": item.get("uid") or item.get("summary"),
                                                  "description": old.rstrip() + "\n" + _lines(fresh)})
    else:
        due = (today + datetime.timedelta(days=DUE_DAYS)).isoformat()
        ctx.ha("services/todo/add_item", {"entity_id": entity, "item": SUMMARY, "due_date": due,
                                          "description": HOW + "\n\nNew games:\n" + _lines(cand)})
    for r in cand:
        for pk, gid in r["ids"].items():
            lib.mark_todo(pk, gid)
    return titles, []
