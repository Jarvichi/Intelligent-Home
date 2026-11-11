"""Tool: Cyberpunk 2077. A manual quest tick list (the console gives no quest data). The quest list is the static file
cyberpunk_quests.json next to this module (public wiki data; sources and fetch date are inside the file). Each player's ticks
are kept in /data/cyberpunk/state_<player>.json and changed by the commands done / undone. Nothing is ticked at the start.

Each quest has one state, worked out from the ticks:
  done       ticked.
  missed     cannot be done in this playthrough: a ticked quest excludes it, or a quest it needs is itself missed.
  locked     a prerequisite is not ticked yet (requires = all of them, requires_any = at least one of them).
  available  everything else, including every quest marked requires_unknown (never hidden on guesswork)."""
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
MAX_LISTED = 8          # quests listed by the commands left / available / missed
DONE, AVAILABLE, LOCKED, MISSED = "done", "available", "locked", "missed"
LINK_FIELDS = ("requires", "requires_any", "excludes")   # quest fields that hold other quest ids
TEXT_FIELDS = ("choice", "note")
UNKNOWN_HINT = "Prerequisites not confirmed."
_cache = {}


def matches(entry):
    return L.norm(entry.get("title")) in TITLES


def _clean(q, known):
    """One quest row with every prerequisite field present. Ids that are not in the list are dropped."""
    row = {k: q[k] for k in ("id", "name", "group", "type")}
    for k in LINK_FIELDS:
        v = q.get(k)
        row[k] = [i for i in v if i in known and i != q["id"]] if isinstance(v, list) else []
    row["requires_unknown"] = q.get("requires_unknown") is True
    for k in TEXT_FIELDS:
        row[k] = q[k].strip() if isinstance(q.get(k), str) else ""
    return row


def quests(path=None):
    """The quest list [{id, name, group, type, requires, requires_any, excludes, requires_unknown, choice, note}], read once
    per file. A missing or broken file gives []."""
    path = path or QUESTS_FILE
    if path not in _cache:
        try:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f).get("quests", [])
        except (OSError, ValueError, AttributeError):
            rows = []
        groups, types = dict(GROUPS), dict(TYPES)
        rows = [q for q in rows if isinstance(q, dict) and q.get("id") and q.get("name")
                and q.get("group") in groups and q.get("type") in types]
        known = {q["id"] for q in rows}
        _cache[path] = [_clean(q, known) for q in rows]
    return _cache[path]


def graph_problems(rows):
    """Faults in the prerequisite data of raw quest rows, as text: unknown ids, a quest naming itself, a cycle through
    requires / requires_any, and a quest that needs a quest it excludes. An empty list means the graph is sound."""
    ids = {q.get("id") for q in rows}
    out = []
    for q in rows:
        for k in LINK_FIELDS:
            for i in q.get(k) or []:
                if i == q.get("id"):
                    out.append("%s names itself in %s" % (i, k))
                elif i not in ids:
                    out.append("%s: unknown id %s in %s" % (q.get("id"), i, k))
        both = set(q.get("excludes") or []) & set((q.get("requires") or []) + (q.get("requires_any") or []))
        out.extend("%s both needs and excludes %s" % (q.get("id"), i) for i in sorted(both))
    needs = {q.get("id"): [i for k in ("requires", "requires_any") for i in (q.get(k) or []) if i in ids] for q in rows}
    seen, on_path = set(), []

    def walk(i):
        if i in on_path:
            out.append("cycle: %s" % " -> ".join(on_path[on_path.index(i):] + [i]))
            return
        if i in seen:
            return
        seen.add(i)
        on_path.append(i)
        for n in needs.get(i, ()):
            walk(n)
        on_path.pop()

    for i in sorted(i for i in needs if i):
        walk(i)
    return out


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


def blockers(done):
    """{quest id: (ticked quest that rules it out, the needed quest that is ruled out or None)} for every missed quest.
    A quest is missed when a ticked quest excludes it, when one of its 'requires' is missed, or when none of its
    'requires_any' is ticked and all of them are missed. A ticked quest is never missed."""
    by = {q["id"]: q for q in quests()}
    excluded_by = {}
    for q in quests():
        if q["id"] in done:
            for i in q["excludes"]:
                excluded_by.setdefault(i, q["id"])
    memo, on_path = {}, set()

    def find(i):
        if i in done or i in on_path:
            return None
        if i not in memo:
            on_path.add(i)
            q, found = by[i], None
            if i in excluded_by:
                found = (excluded_by[i], None)
            elif not q["requires_unknown"]:
                for r in q["requires"]:
                    b = find(r)
                    if b:
                        found = (b[0], r)
                        break
                if not found and q["requires_any"] and not any(r in done for r in q["requires_any"]):
                    each = [find(r) for r in q["requires_any"]]
                    if all(each):
                        found = (each[0][0], q["requires_any"][0])
            on_path.discard(i)
            memo[i] = found
        return memo[i]

    return {i: b for i, b in ((i, find(i)) for i in by) if b}


def classify(done):
    """{quest id: {"state", "needs", "needs_any", "blocked_by", "via"}} for every quest, from the set of ticked ids.
    needs = unticked ids of 'requires'; needs_any = the 'requires_any' ids when none of them is ticked."""
    missed = blockers(done)
    out = {}
    for q in quests():
        i = q["id"]
        needs = [r for r in q["requires"] if r not in done]
        needs_any = q["requires_any"] if q["requires_any"] and not any(r in done for r in q["requires_any"]) else []
        row = {"state": AVAILABLE, "needs": [], "needs_any": [], "blocked_by": None, "via": None}
        if i in done:
            row["state"] = DONE
        elif i in missed:
            row.update(state=MISSED, blocked_by=missed[i][0], via=missed[i][1])
        elif not q["requires_unknown"] and (needs or needs_any):
            row.update(state=LOCKED, needs=needs, needs_any=list(needs_any))
        out[i] = row
    return out


def _reason(q, c, by):
    """Plain text for a locked or missed quest: what it needs, or what ruled it out and by which choice."""
    name = lambda i: by[i]["name"]
    if c["state"] == LOCKED:
        parts = []
        if c["needs"]:
            parts.append("Needs: %s." % ", ".join(name(i) for i in c["needs"]))
        if c["needs_any"]:
            parts.append("Needs one of: %s." % ", ".join(name(i) for i in c["needs_any"]))
        return " ".join(parts)
    if c["state"] == MISSED:
        text = ("Needs %s, which is blocked by %s." % (name(c["via"]), name(c["blocked_by"])) if c["via"]
                else "Blocked by %s." % name(c["blocked_by"]))
        choice = by[c["blocked_by"]]["choice"]
        return text + (" " + choice if choice else "")
    return ""


def _would_block(q, done, missed_now, by):
    """Names of the quests that ticking q would newly rule out. Only a quest with 'excludes' can rule others out."""
    if not q["excludes"] or q["id"] in done:
        return []
    after = blockers(done | {q["id"]})
    return [by[i]["name"] for i in by if i in after and i not in missed_now]


def quest_rows(tc, player, group=None, state=None):
    """Every quest as a row for the UI and the commands, in list order, optionally for one group id and one state."""
    done = done_ids(tc, player)
    by = {q["id"]: q for q in quests()}
    cls = classify(done)
    missed_now = {i for i, c in cls.items() if c["state"] == MISSED}
    types = dict(TYPES)
    rows = []
    for q in quests():
        c = cls[q["id"]]
        if (group and q["group"] != group) or (state and c["state"] != state):
            continue
        hints = [t for t in (q["choice"], q["note"], UNKNOWN_HINT if q["requires_unknown"] else "") if t]
        rows.append({"id": q["id"], "name": q["name"], "group": q["group"], "type": q["type"], "type_label": types[q["type"]],
                     "done": c["state"] == DONE, "state": c["state"], "reason": _reason(q, c, by), "hint": " ".join(hints),
                     "requires_unknown": q["requires_unknown"],
                     "blocked_by": by[c["blocked_by"]]["name"] if c["blocked_by"] else "",
                     "blocks": _would_block(q, done, missed_now, by)})
    return rows


def available(tc, player, group=None):
    """Quests that can be done now: not ticked, not missed, every known prerequisite ticked."""
    return quest_rows(tc, player, group, AVAILABLE)


def locked(tc, player, group=None):
    """Quests whose prerequisite is not ticked yet. Each row's 'reason' names what is needed."""
    return quest_rows(tc, player, group, LOCKED)


def blocked(tc, player, group=None):
    """Quests missed in this playthrough. Each row's 'reason' names the ticked quest and the choice that ruled it out."""
    return quest_rows(tc, player, group, MISSED)


def _count(rows, state):
    return sum(1 for r in rows if r["state"] == state)


def group_rows(tc, player):
    rows = quest_rows(tc, player)
    out = []
    for gid, label in GROUPS:
        mine = [r for r in rows if r["group"] == gid]
        out.append({"id": gid, "label": label, "done": _count(mine, DONE), "total": len(mine), "available": _count(mine, AVAILABLE),
                    "locked": _count(mine, LOCKED), "missed": _count(mine, MISSED), "quests": mine})
    return out


def _nodata():
    return "The Cyberpunk 2077 quest list is missing from the add-on."


def _totals(groups):
    return {k: sum(g[k] for g in groups) for k in ("done", "total", "available", "locked", "missed")}


def details(tc, player):
    """Everything the Games UI needs: totals and every quest per group with its tick, state, reason and hint."""
    if not quests():
        return {"error": _nodata()}
    groups = group_rows(tc, player)
    return dict(_totals(groups), groups=groups)


def summary(tc, player):
    if not quests():
        return _nodata()
    groups = group_rows(tc, player)
    t = _totals(groups)
    parts = ["%s %d/%d" % (g["label"], g["done"], g["total"]) for g in groups]
    return "Cyberpunk 2077: %d of %d quests ticked (%s). Available now: %d. Locked: %d. Missed this run: %d." % (
        t["done"], t["total"], ", ".join(parts), t["available"], t["locked"], t["missed"])


def sensor(tc, player):
    """(state, attributes) for sensor.games_cyberpunk_<player>."""
    if not quests():
        return "no quest list", {"done": 0, "total": 0, "available": 0, "locked": 0, "missed": 0, "groups": [], "error": _nodata()}
    full = group_rows(tc, player)
    t = _totals(full)
    groups = [{k: g[k] for k in ("id", "label", "done", "total", "available", "locked", "missed")} for g in full]
    return "%d / %d quests" % (t["done"], t["total"]), dict(t, groups=groups, error="")


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
    before = set(blockers(done_ids(tc, player)))
    if tick and not has:
        s[STATE_KEY].append(q["id"])
    elif not tick and has:
        s[STATE_KEY] = [i for i in s[STATE_KEY] if i != q["id"]]
    if tick != has:
        save_state(tc, player, s)
    after = set(blockers(done_ids(tc, player)))
    text = "Marked %s: %s." % ("done" if tick else "not done", q["name"])
    for label, ids in (("Now missed this run", after - before), ("Possible again", before - after)):
        names = [x["name"] for x in quests() if x["id"] in ids]
        if names:
            text += " %s: %s." % (label, _listed(names))
    return text


def _listed(names):
    return ", ".join(names[:MAX_LISTED]) + (", ..." if len(names) > MAX_LISTED else "")


def cmd_done(tc, player, args):
    return _set(tc, player, args, True)


def cmd_undone(tc, player, args):
    return _set(tc, player, args, False)


def cmd_status(tc, player, args):
    return summary(tc, player)


def _group_arg(args, cmd):
    """(group id or "", None), or (None, usage text) for a group id that does not exist."""
    want = (args[0].lower() if args else "")
    if want and want not in dict(GROUPS):
        return None, "Use: %s [%s]" % (cmd, "|".join(g for g, _ in GROUPS))
    return want, None


def cmd_left(tc, player, args):
    """Quests not ticked yet, optionally for one group id (base, phantom_liberty, patch)."""
    if not quests():
        return _nodata()
    want, usage = _group_arg(args, "left")
    if usage:
        return usage
    names = [r["name"] for r in quest_rows(tc, player, want or None) if not r["done"]]
    if not names:
        return "No quests left."
    return "%d left: %s%s" % (len(names), _listed(names), "" if len(names) > MAX_LISTED else ".")


def cmd_available(tc, player, args):
    """Quests that can be done now, optionally for one group id."""
    if not quests():
        return _nodata()
    want, usage = _group_arg(args, "available")
    if usage:
        return usage
    names = [r["name"] for r in available(tc, player, want or None)]
    if not names:
        return "No quests available."
    return "%d available: %s%s" % (len(names), _listed(names), "" if len(names) > MAX_LISTED else ".")


def cmd_missed(tc, player, args):
    """Quests that cannot be done in this playthrough, each with the ticked quest that ruled it out."""
    if not quests():
        return _nodata()
    rows = blocked(tc, player)
    if not rows:
        return "No quests missed this run."
    names = ["%s (blocked by %s)" % (r["name"], r["blocked_by"]) for r in rows]
    return "%d missed this run: %s%s" % (len(rows), _listed(names), "" if len(names) > MAX_LISTED else ".")


commands = {"done": cmd_done, "undone": cmd_undone, "status": cmd_status, "left": cmd_left,
            "available": cmd_available, "missed": cmd_missed}
