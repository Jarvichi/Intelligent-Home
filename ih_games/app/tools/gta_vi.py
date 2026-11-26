"""Tool: Grand Theft Auto VI. A hand-ticked checklist of the game's own 100% completion requirements (the game has no
player API). The list is the static file gta_vi_checklist.json next to this module. It is NOT verified in game: the
in-game list appears only after the story. Story missions are never listed by name. Each requirement has a counter.
Counts are kept per player in /data/gta_vi/state_<player>.json (updates do not reset them). Trophies and play time are
automatic (PlayStation feeds); the checklist is "ticked by you"."""
import json, os, re
import library as L
import trophies

NAME = "gta_vi"
TITLES = ("grand theft auto vi", "gta vi", "gta 6", "grand theft auto 6")
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gta_vi_checklist.json")
STATE_DIR = "gta_vi"
PROGRESS_LABEL = "100% list"
MAX_LISTED = 8
MAX_MATCHES_SHOWN = 5
_cache = {}


def matches(entry):
    return L.norm(entry.get("title")) in TITLES


def _load(path=None):
    path = path or DATA_FILE
    if path not in _cache:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        groups = {g["id"]: g["label"] for g in d.get("groups", []) if isinstance(g, dict) and g.get("id")}
        items = []
        for i in d.get("items", []):
            try:
                if i["group"] in groups and int(i["target"]) > 0:
                    items.append({"id": str(i["id"]), "name": str(i["name"]), "group": i["group"], "target": int(i["target"]),
                                  "total": int(i["total"]) if i.get("total") else None,
                                  "unit": i.get("unit") or "count", "alias": str(i.get("alias") or "")})
            except (KeyError, TypeError, ValueError):
                continue
        _cache[path] = {"groups": groups, "items": items, "verified": d.get("verified") is True, "source": d.get("source", "")}
    return _cache[path]


def items():
    return _load()["items"]


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
    for k in ("counts", "targets"):
        if not isinstance(s.get(k), dict):
            s[k] = {}
    return s


def save_state(tc, player, s):
    p = state_path(tc, player)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, p)


def _int(v):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def rows(tc, player):
    """Every requirement with count, target (player override wins), done flag. A count above target is kept as is."""
    s = load_state(tc, player)
    out = []
    for i in items():
        target = _int(s["targets"].get(i["id"])) or i["target"]
        count = _int(s["counts"].get(i["id"]))
        out.append(dict(i, count=count, target=target, done=count >= target))
    return out


def _fmt(r, n):
    return ("$%s" % format(n, ",")) if r["unit"] == "money" else str(n)


def _row_text(r):
    if r["unit"] == "flag":
        return "%s: %s" % (r["name"], "done" if r["done"] else "not yet")
    return "%s: %s of %s" % (r["name"], _fmt(r, r["count"]), _fmt(r, r["target"]))


def progress(tc, player):
    rs = rows(tc, player)
    return {"done": sum(1 for r in rs if r["done"]), "total": len(rs), "label": PROGRESS_LABEL}


def _nodata():
    return "The GTA VI checklist is missing from the add-on."


def _auto(tc, player, title="Grand Theft Auto VI"):
    t = trophies.for_game(tc.data_dir, player, title)
    return trophies.line(t) if t else None


def summary(tc, player):
    if not items():
        return _nodata()
    p = progress(tc, player)
    text = "GTA VI checklist (ticked by you, list not verified in game): %d of %d requirements done." % (p["done"], p["total"])
    auto = _auto(tc, player)
    return text + (" Automatic: %s." % auto if auto else "")


def sensor(tc, player):
    if not items():
        return "no checklist", {"done": 0, "total": 0, "error": _nodata()}
    p = progress(tc, player)
    return "%d / %d requirements" % (p["done"], p["total"]), {"done": p["done"], "total": p["total"], "verified": _load()["verified"], "error": ""}


def details(tc, player):
    if not items():
        return {"error": _nodata()}
    d = _load()
    rs = rows(tc, player)
    groups = [{"id": gid, "label": label, "items": [r for r in rs if r["group"] == gid]} for gid, label in d["groups"].items()]
    return {"verified": d["verified"], "source": d["source"], "labels": {"checklist": "ticked by you", "trophies": "automatic"},
            "done": sum(1 for r in rs if r["done"]), "total": len(rs), "groups": groups, "trophies": _auto(tc, player)}


def upcoming(tc, player):
    return []


def find(text):
    t = L.norm(text)
    raw = str(text or "").strip().lower()
    if not t and not raw:
        return []
    exact = [i for i in items() if raw in (i["id"], i["alias"]) or (t and t == L.norm(i["name"]))]
    return exact or [i for i in items() if t and t in L.norm(i["name"])]


def _split(args):
    """(item words, number or None). A trailing number may carry $ and commas."""
    args = list(args)
    n = None
    if args and re.fullmatch(r"\$?\d[\d,]*", args[-1]):
        n = int(re.sub(r"[$,]", "", args.pop()))
    return args, n


def _pick(words):
    m = find(" ".join(words))
    if len(m) == 1:
        return m[0], None
    if not m:
        return None, "Requirement not found. Use its name, id or short word (for example vehicles)."
    names = ", ".join(i["name"] for i in m[:MAX_MATCHES_SHOWN])
    return None, "%d requirements match: %s. Use the id or a longer name." % (len(m), names)


def _change(tc, player, args, mode):
    if not items():
        return _nodata()
    words, n = _split(args)
    it, why = _pick(words)
    if not it:
        return why
    s = load_state(tc, player)
    cur = _int(s["counts"].get(it["id"]))
    target = _int(s["targets"].get(it["id"])) or it["target"]
    if mode == "done":
        new = target if n is None else n
    elif mode == "add":
        if n is None:
            return "Use: add <requirement> <number>"
        new = cur + n
    elif mode == "undone":
        new = 0
    else:   # target
        if not n:
            return "Use: target <requirement> <number> (the number the game asks for)"
        s["targets"][it["id"]] = n
        save_state(tc, player, s)
        return "Target for %s is now %s." % (it["name"], _fmt(it, n))
    s["counts"][it["id"]] = new
    save_state(tc, player, s)
    r = [x for x in rows(tc, player) if x["id"] == it["id"]][0]
    return "Saved. " + _row_text(r) + "."


def cmd_done(tc, player, args):
    """done <requirement> [number]: set the count (no number = reached the target)."""
    return _change(tc, player, args, "done")


def cmd_add(tc, player, args):
    return _change(tc, player, args, "add")


def cmd_undone(tc, player, args):
    return _change(tc, player, args, "undone")


def cmd_target(tc, player, args):
    return _change(tc, player, args, "target")


def cmd_status(tc, player, args):
    return summary(tc, player)


def cmd_left(tc, player, args):
    if not items():
        return _nodata()
    left = [r for r in rows(tc, player) if not r["done"]]
    if not left:
        return "Every requirement on the list is done."
    shown = "; ".join(_row_text(r) for r in left[:MAX_LISTED])
    return "%d left: %s%s" % (len(left), shown, ", ..." if len(left) > MAX_LISTED else ".")


commands = {"done": cmd_done, "add": cmd_add, "undone": cmd_undone, "target": cmd_target, "status": cmd_status, "left": cmd_left}
