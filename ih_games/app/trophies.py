"""Trophy facts for one game, from the feeds psn_trophy_titles (totals) and psn_trophy_names (latest named trophies)."""
import json, os
import library as L

TITLES_FEED, NAMES_FEED = "psn_trophy_titles", "psn_trophy_names"
LATEST_NAMED_MAX = 5
TIERS = ("bronze", "silver", "gold", "platinum")


def _feed(data_dir, name, player):
    try:
        with open(os.path.join(data_dir, "feeds", f"{name}_{player}.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def for_game(data_dir, player, title):
    """{progress, earned, defined, platform, latest:[{name,tier,time}]} or None when the game has no trophy data."""
    n = L.norm(title)
    found = next((t for t in _feed(data_dir, TITLES_FEED, player).get("titles", []) if L.norm(L.clean_title(t.get("title"))) == n), None)
    named = [e for e in _feed(data_dir, NAMES_FEED, player).get("events", [])
             if e.get("kind") == "trophy_named" and L.norm(e.get("game")) == n and e.get("achievement")]
    if not found and not named:
        return None
    named.sort(key=lambda e: e.get("time") or "", reverse=True)
    out = {"progress": None, "earned": None, "defined": None, "platform": None,
           "latest": [{"name": e["achievement"], "tier": e.get("tier"), "time": e.get("time")} for e in named[:LATEST_NAMED_MAX]]}
    if found:
        out.update(progress=found.get("progress"), earned=found.get("earned"), defined=found.get("defined"),
                   platform=found.get("platform"))
    return out


def line(t):
    """'Trophies: 12 bronze, 3 silver, 1 gold, 0 platinum, 34% complete' (None without totals)."""
    if not t or not t.get("earned"):
        return None
    bits = ", ".join("%d %s" % (int(t["earned"].get(k) or 0), k) for k in TIERS)
    if t.get("progress") is not None:
        bits += ", %d%% complete" % int(t["progress"])
    return "Trophies: " + bits
