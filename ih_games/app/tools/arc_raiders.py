"""Tool: Arc Raiders. Needs-list, quest maps and keep/sell/recycle advice from the public RaidTheory game data set (MIT licence,
folder given by the option arc_data_dir) and a per-player state file /data/arc/state_<player>.json that chat commands edit
(the game has no player API). Logic ported from the host helper arc.py."""
import collections, glob, json, os
import library as L

NAME = "arc_raiders"
TITLES = ("arc raiders", "arc raiders game")
_cache = {}


def matches(entry):
    return L.norm(entry.get("title")) in TITLES


def _en(o):
    return o.get("en") if isinstance(o, dict) else o


def _load_json(p):
    return json.load(open(p, encoding="utf-8"))


def data_dir(tc):
    for d in (tc.opts.get("arc_data_dir"), "/app/arcdata", os.path.join(tc.data_dir, "arc_data")):
        if d and os.path.isdir(d):
            return d
    return None


def game_data(tc):
    d = data_dir(tc)
    if not d:
        return None
    if d in _cache:
        return _cache[d]
    g = {"quests": {q["id"]: q for q in map(_load_json, glob.glob(d + "/quests/*.json"))},
         "items": {i["id"]: i for i in map(_load_json, glob.glob(d + "/items/*.json"))},
         "hideout": {h["id"]: h for h in map(_load_json, glob.glob(d + "/hideout/*.json"))}}
    try:
        g["projects"] = [p for p in _load_json(d + "/projects.json") if not p.get("disabled")]
    except OSError:
        g["projects"] = []
    try:
        g["maps"] = {m["id"]: _en(m["name"]) for m in _load_json(d + "/maps.json")}
    except OSError:
        g["maps"] = {}
    _cache[d] = g
    return g


def state_path(tc, player):
    return os.path.join(tc.data_dir, "arc", "state_%s.json" % player)


def load_state(tc, player):
    try:
        s = _load_json(state_path(tc, player))
    except (OSError, ValueError):
        s = {}
    for k, v in (("quests_done", []), ("hideout", {}), ("projects_done_phases", {}), ("inventory", {}), ("projects_active", [])):
        s.setdefault(k, v)
    return s


def save_state(tc, player, s):
    p = state_path(tc, player)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(s, open(p, "w"), indent=1)


def compute(g, s):
    quests, items = g["quests"], g["items"]
    qn = lambda q: _en(quests[q]["name"])
    done = set(s["quests_done"])
    open_q = [q for q in quests.values() if q["id"] not in done and all(p in done for p in q.get("previousQuestIds", []))]
    future_q = [q for q in quests.values() if q["id"] not in done]
    now, later, why = collections.Counter(), collections.Counter(), collections.defaultdict(list)

    def add(c, iid, n, label):
        c[iid] += n
        why[iid].append("%s x%d" % (label, n))
    openids = {q["id"] for q in open_q}
    for q in future_q:
        c = now if q["id"] in openids else later
        for r in q.get("requiredItemIds", []):
            add(c, r["itemId"], r["quantity"], "quest " + qn(q["id"]))
    for hid, h in g["hideout"].items():
        lvl = s["hideout"].get(hid, 0)
        for lv in h["levels"]:
            if lv["level"] <= lvl:
                continue
            c = now if lv["level"] == lvl + 1 else later
            for r in lv.get("requirementItemIds", []):
                add(c, r["itemId"], r["quantity"], "%s L%d" % (_en(h["name"]), lv["level"]))
    for p in g["projects"]:
        if p["id"] not in s["projects_active"]:
            continue
        dp = s["projects_done_phases"].get(p["id"], 0)
        for ph in p.get("phases", []):
            if ph["phase"] <= dp:
                continue
            c = now if ph["phase"] == dp + 1 else later
            for r in ph.get("requirementItemIds", []):
                add(c, r["itemId"], r["quantity"], "%s ph%d" % (_en(p["name"]), ph["phase"]))
    return open_q, now, later, why


def _iname(g, i):
    return _en(g["items"][i]["name"]) if i in g["items"] else i


def need_rows(g, s):
    _, now, later, why = compute(g, s)
    rows = []
    for iid in set(now) | set(later):
        n, l, have = now.get(iid, 0), later.get(iid, 0), s["inventory"].get(iid, 0)
        rows.append({"item": _iname(g, iid), "now": n, "later": l, "have": have, "short": max(0, n - have),
                     "status": "NEED" if have < n else ("Later" if have < n + l else "Enough"), "for": ", ".join(why[iid][:2])})
    rows.sort(key=lambda r: (-r["short"], r["item"]))
    return rows


def map_rows(g, s):
    open_q, *_ = compute(g, s)
    by, anyq = collections.defaultdict(list), []
    for q in open_q:
        m = q.get("map") or []
        if not m:
            anyq.append(q)
        for x in m:
            by[x].append(q)
    rows = [{"map": g["maps"].get(mid, mid.replace("_", " ").title()), "count": len(qs), "quests": [_en(q["name"]) for q in qs]}
            for mid, qs in sorted(by.items(), key=lambda kv: -len(kv[1]))]
    if anyq:
        rows.append({"map": "anywhere", "count": len(anyq), "quests": [_en(q["name"]) for q in anyq]})
    return rows


def inv_rows(g, s):
    _, now, later, why = compute(g, s)
    items, rows = g["items"], []
    for iid, have in s["inventory"].items():
        it = items.get(iid)
        if not it:
            rows.append({"item": iid, "surplus": have, "action": "unknown item id", "gain": 0})
            continue
        keep = now.get(iid, 0) + later.get(iid, 0)
        if it.get("questItem"):
            keep = max(keep, have)
        surplus = max(0, have - keep)
        rec = it.get("recyclesInto") or {}
        rv = sum(items[k]["value"] * v for k, v in rec.items() if k in items)
        wanted = any(now.get(k, 0) + later.get(k, 0) > s["inventory"].get(k, 0) for k in rec)
        if surplus == 0:
            act = "KEEP"
        elif rec and (wanted or rv > it["value"]):
            act = "RECYCLE %d" % surplus
        else:
            act = "SELL %d" % surplus
        rows.append({"item": _iname(g, iid), "surplus": surplus, "action": act, "gain": surplus * (it.get("value") or 0)})
    rows.sort(key=lambda r: -r["gain"])
    return rows


def _nodata():
    return "Arc Raiders game data is not installed (set the arc_data_dir option)."


def summary(tc, player):
    g = game_data(tc)
    if not g:
        return _nodata()
    s = load_state(tc, player)
    open_q, *_ = compute(g, s)
    short = [r for r in need_rows(g, s) if r["status"] == "NEED"]
    return "Arc Raiders: %d quests done, %d open now, %d item types short for what is open." % (len(s["quests_done"]), len(open_q), len(short))


def upcoming(tc, player):
    return []


def cmd_need(tc, player, args):
    g = game_data(tc)
    if not g:
        return _nodata()
    rows = [r for r in need_rows(g, load_state(tc, player)) if r["status"] != "Enough"][:8]
    return "; ".join("%s: need %d, have %d (%s)" % (r["item"], r["now"], r["have"], r["for"]) for r in rows) or "Nothing short right now."


def cmd_maps(tc, player, args):
    g = game_data(tc)
    if not g:
        return _nodata()
    return "; ".join("%s (%d): %s" % (r["map"], r["count"], ", ".join(r["quests"][:3])) for r in map_rows(g, load_state(tc, player))[:6]) or "No open quests."


def cmd_inv(tc, player, args):
    g = game_data(tc)
    if not g:
        return _nodata()
    rows = [r for r in inv_rows(g, load_state(tc, player)) if r["surplus"]][:8]
    return "; ".join("%s: %s" % (r["item"], r["action"]) for r in rows) or "Nothing to sell or recycle."


def cmd_done(tc, player, args):
    g = game_data(tc)
    if not g:
        return _nodata()
    q = " ".join(args).strip().lower()
    m = [i for i, x in g["quests"].items() if q in (i, _en(x["name"]).lower())]
    if len(m) != 1:
        return "Quest not found or not exact (%d matches)." % len(m)
    s = load_state(tc, player)
    if m[0] not in s["quests_done"]:
        s["quests_done"].append(m[0])
        save_state(tc, player, s)
    return "Marked done: %s." % _en(g["quests"][m[0]]["name"])


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def cmd_have(tc, player, args):
    if len(args) < 2 or _num(args[-1]) is None:
        return "Use: have <item id> <count>"
    s = load_state(tc, player)
    s["inventory"][" ".join(args[:-1])] = _num(args[-1])
    save_state(tc, player, s)
    return "Saved: %s x%d." % (" ".join(args[:-1]), _num(args[-1]))


def cmd_hideout(tc, player, args):
    if len(args) != 2 or _num(args[1]) is None:
        return "Use: hideout <station id> <level>"
    s = load_state(tc, player)
    s["hideout"][args[0]] = _num(args[1])
    save_state(tc, player, s)
    return "Saved: %s level %d." % (args[0], _num(args[1]))


def cmd_project(tc, player, args):
    if len(args) != 2 or _num(args[1]) is None:
        return "Use: project <project id> <phases done>"
    s = load_state(tc, player)
    if args[0] not in s["projects_active"]:
        s["projects_active"].append(args[0])
    s["projects_done_phases"][args[0]] = _num(args[1])
    save_state(tc, player, s)
    return "Saved: project %s, %d phases done." % (args[0], _num(args[1]))


commands = {"need": cmd_need, "maps": cmd_maps, "inv": cmd_inv, "done": cmd_done, "have": cmd_have, "hideout": cmd_hideout, "project": cmd_project}
