"""Tool: Cyberpunk 2077. A manual quest tick list (the console gives no quest data). The quest list is the static file
cyberpunk_quests.json next to this module (public wiki data; sources and fetch date are inside the file). Each player's ticks
are kept in /data/cyberpunk/state_<player>.json and changed by the commands done / undone. Nothing is ticked at the start."""
import json, os
import library as L

NAME = "cyberpunk"
TITLES = ("cyberpunk 2077", "cyberpunk 2077 ultimate edition", "cyberpunk 2077 phantom liberty")
UI_COMPONENT = "cyberpunk"
QUESTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cyberpunk_quests.json")
STATE_DIR = "cyberpunk"
STATE_KEY = "quests_done"
GROUPS = (("base", "Base game"), ("phantom_liberty", "Phantom Liberty"), ("patch", "Patch quests"))
TYPES = (("main", "Main jobs"), ("side", "Side jobs"), ("gig", "Gigs"))
PROGRESS_LABEL = "Quests"
MAX_MATCHES_SHOWN = 5   # names listed when a command matches more than one quest
MAX_LEFT_SHOWN = 8      # quests listed by the 'left' command
_cache = {}


def matches(entry):
    return L.norm(entry.get("title")) in TITLES


def quests(path=None):
    """The quest list [{id, name, group, type}], read once per file. A missing or broken file gives []."""
    path = path or QUESTS_FILE
    if path not in _cache:
        try:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f).get("quests", [])
        except (OSError, ValueError, AttributeError):
            rows = []
        groups, types = dict(GROUPS), dict(TYPES)
        _cache[path] = [q for q in rows if isinstance(q, dict) and q.get("id") and q.get("name")
                        and q.get("group") in groups and q.get("type") in types]
    return _cache[path]


def state_path(tc, player):
    return os.path.join(tc.data_dir, STATE_DIR, "state_%s.json" % player)


def load_state(tc, player):
    try:
        with open(state_path(tc, player), encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    if not isinstance(s, dict):
        s = {}
    if not isinstance(s.get(STATE_KEY), list):
        s[STATE_KEY] = []
    return s


def save_state(tc, player, s):
    p = state_path(tc, player)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, p)


def done_ids(tc, player):
    """Ticked quest ids that are in the quest list (an id the list no longer has is not counted)."""
    known = {q["id"] for q in quests()}
    return {i for i in load_state(tc, player)[STATE_KEY] if i in known}


def progress(tc, player):
    """Card stat box: ticked quests out of all quests."""
    return {"done": len(done_ids(tc, player)), "total": len(quests()), "label": PROGRESS_LABEL}


def group_rows(tc, player):
    done = done_ids(tc, player)
    types = dict(TYPES)
    out = []
    for gid, label in GROUPS:
        rows = [{"id": q["id"], "name": q["name"], "type": q["type"], "type_label": types[q["type"]], "done": q["id"] in done}
                for q in quests() if q["group"] == gid]
        out.append({"id": gid, "label": label, "done": sum(1 for r in rows if r["done"]), "total": len(rows), "quests": rows})
    return out


def _nodata():
    return "The Cyberpunk 2077 quest list is missing from the add-on."


def details(tc, player):
    """Everything the Games UI needs: totals and every quest per group with its tick."""
    if not quests():
        return {"error": _nodata()}
    p = progress(tc, player)
    return {"done": p["done"], "total": p["total"], "groups": group_rows(tc, player)}


def summary(tc, player):
    if not quests():
        return _nodata()
    parts = ["%s %d/%d" % (g["label"], g["done"], g["total"]) for g in group_rows(tc, player)]
    p = progress(tc, player)
    return "Cyberpunk 2077: %d of %d quests ticked (%s)." % (p["done"], p["total"], ", ".join(parts))


def sensor(tc, player):
    """(state, attributes) for sensor.games_cyberpunk_<player>."""
    if not quests():
        return "no quest list", {"done": 0, "total": 0, "groups": [], "error": _nodata()}
    p = progress(tc, player)
    groups = [{"id": g["id"], "label": g["label"], "done": g["done"], "total": g["total"]} for g in group_rows(tc, player)]
    return "%d / %d quests" % (p["done"], p["total"]), {"done": p["done"], "total": p["total"], "groups": groups, "error": ""}


def upcoming(tc, player):
    return []


def find(text):
    """Quests matching an id or a name. An exact match wins; else every quest whose name contains the text."""
    t = L.norm(text)
    raw = str(text or "").strip().lower()
    if not t and not raw:
        return []
    exact = [q for q in quests() if raw == q["id"] or (t and t == L.norm(q["name"]))]
    return exact or [q for q in quests() if t and t in L.norm(q["name"])]


def _pick(args):
    """(quest, None) for exactly one match, else (None, message)."""
    if not quests():
        return None, _nodata()
    m = find(" ".join(args))
    if len(m) == 1:
        return m[0], None
    if not m:
        return None, "Quest not found. Use the quest name or id."
    names = ", ".join(q["name"] for q in m[:MAX_MATCHES_SHOWN])
    return None, "%d quests match: %s%s. Use the full name." % (len(m), names, ", ..." if len(m) > MAX_MATCHES_SHOWN else "")


def _set(tc, player, args, tick):
    q, why = _pick(args)
    if not q:
        return why
    s = load_state(tc, player)
    has = q["id"] in s[STATE_KEY]
    if tick and not has:
        s[STATE_KEY].append(q["id"])
    elif not tick and has:
        s[STATE_KEY] = [i for i in s[STATE_KEY] if i != q["id"]]
    if tick != has:
        save_state(tc, player, s)
    return "Marked %s: %s." % ("done" if tick else "not done", q["name"])


def cmd_done(tc, player, args):
    return _set(tc, player, args, True)


def cmd_undone(tc, player, args):
    return _set(tc, player, args, False)


def cmd_status(tc, player, args):
    return summary(tc, player)


def cmd_left(tc, player, args):
    """Quests not ticked yet, optionally for one group id (base, phantom_liberty, patch)."""
    if not quests():
        return _nodata()
    want = (args[0].lower() if args else "")
    if want and want not in dict(GROUPS):
        return "Use: left [%s]" % "|".join(g for g, _ in GROUPS)
    done = done_ids(tc, player)
    rows = [q for q in quests() if q["id"] not in done and (not want or q["group"] == want)]
    if not rows:
        return "No quests left."
    return "%d left: %s%s" % (len(rows), ", ".join(q["name"] for q in rows[:MAX_LEFT_SHOWN]), ", ..." if len(rows) > MAX_LEFT_SHOWN else ".")


commands = {"done": cmd_done, "undone": cmd_undone, "status": cmd_status, "left": cmd_left}
