"""Trophy facts for one game, from the feeds psn_trophy_titles (totals) and psn_trophy_names (latest named trophies)."""
import datetime, json, os
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


# ---- card summary: earned / total per game ----
def totals_index(data_dir, player):
    """{normalised title: {"earned": n, "total": m}} for every game in the trophy-titles feed that defines trophies."""
    out = {}
    for t in _feed(data_dir, TITLES_FEED, player).get("titles", []):
        try:
            earned = sum(int((t.get("earned") or {}).get(k) or 0) for k in TIERS)
            total = sum(int((t.get("defined") or {}).get(k) or 0) for k in TIERS)
        except (TypeError, ValueError, AttributeError):
            continue
        if total > 0:
            out[L.norm(L.clean_title(t.get("title")))] = {"earned": earned, "total": total}
    return out


# ---- full trophy list for one game (tap a tier tile) ----
LIST_DIR = "trophy_lists"            # /data/trophy_lists/<player>/<game slug>.json
LIST_CACHE_S = 15 * 60               # a saved list younger than this is served without a PSN call
HISTORY_FILE = "trophy_history_%s.jsonl"   # written by providers/psn_trophy_names.py
SOURCE_PSN, SOURCE_HISTORY = "psn", "history"
MISSING_NOT_CONFIGURED, MISSING_LOOKUP_FAILED, MISSING_NOT_FOUND = "not_configured", "lookup_failed", "not_found"
MISSING_NOTES = {
    MISSING_NOT_CONFIGURED: "PSN trophy lookups are not set up for this player (PSN token and Online ID).",
    MISSING_LOOKUP_FAILED: "The PSN trophy lookup failed.",
    MISSING_NOT_FOUND: "PSN returned no trophy list for this title.",
}
INCOMPLETE_NOTE = ("Incomplete list: only trophies whose names were recorded when they were earned are shown. "
                   "Unearned trophies and trophies earned before name tracking began are missing. ")
STALE_NOTE = "Showing the last saved list; the PSN trophy lookup did not succeed this time."


def _list_path(data_dir, player, title):
    return os.path.join(data_dir, LIST_DIR, L.slug(player), L.slug(L.clean_title(title)) + ".json")


def _sorted_rows(rows):
    """Earned first (newest first), then not earned in PSN order."""
    earned = sorted((r for r in rows if r.get("earned")), key=lambda r: r.get("earned_at") or "", reverse=True)
    return earned + [r for r in rows if not r.get("earned")]


def _iso_text(value):
    """Time text with the ISO 'T' separator (older history lines used a space, which some browsers cannot parse)."""
    return str(value).replace(" ", "T", 1) if value else None


def _clean_rows(rows):
    out = []
    for r in rows or []:
        tier = str(r.get("tier") or "").lower()
        if tier not in TIERS:
            continue
        out.append({"id": str(r.get("id") or ""), "group": str(r.get("group") or ""), "name": str(r.get("name") or "").strip(),
                    "detail": str(r.get("detail") or "").strip(), "tier": tier, "earned": bool(r.get("earned")),
                    "earned_at": _iso_text(r.get("earned_at")), "hidden": bool(r.get("hidden"))})
    return out


def earned_from_history(data_dir, player, title):
    """Earned trophies with names from the permanent history file and the names feed. Never holds unearned trophies."""
    n, rows, seen = L.norm(L.clean_title(title)), [], set()

    def add(game, tier, name, when):
        key = (str(tier or "").lower(), str(name or "").strip())
        if L.norm(L.clean_title(game)) != n or not key[1] or key[0] not in TIERS or key in seen:
            return
        seen.add(key)
        rows.append({"id": "", "group": "", "name": key[1], "detail": "", "tier": key[0], "earned": True,
                     "earned_at": _iso_text(when), "hidden": False})
    try:
        with open(os.path.join(data_dir, HISTORY_FILE % player)) as fh:
            for raw in fh:
                try:
                    h = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(h, dict):
                    add(h.get("game"), h.get("tier"), h.get("name"), h.get("earned_at") or h.get("time"))
    except OSError:
        pass
    for e in _feed(data_dir, NAMES_FEED, player).get("events", []):
        if e.get("kind") == "trophy_named":
            add(e.get("game"), e.get("tier"), e.get("achievement"), e.get("time"))
    return rows


def tier_list(data_dir, player, title, fetch=None, now=None):
    """All trophies of one game with earned / not earned.
    fetch(title) -> rows from PSN (None when PSN lookups are not configured). A fresh saved list is served first; a
    failed lookup falls back to the saved list (stale), then to the earned names recorded locally (complete: False).
    Returns {complete, source, fetched, stale, missing, note, trophies}."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    path = _list_path(data_dir, player, title)
    try:
        with open(path) as fh:
            saved = json.load(fh)
        saved = saved if isinstance(saved, dict) and saved.get("trophies") else None
    except (OSError, ValueError):
        saved = None

    def from_saved(stale):
        return {"complete": True, "source": SOURCE_PSN, "fetched": saved.get("fetched"), "stale": stale, "missing": None,
                "note": STALE_NOTE if stale else None, "trophies": _sorted_rows(_clean_rows(saved["trophies"]))}
    if saved:
        try:
            age = (now - datetime.datetime.fromisoformat(saved.get("fetched"))).total_seconds()
        except (TypeError, ValueError):
            age = None
        if age is not None and 0 <= age < LIST_CACHE_S:
            return from_saved(False)
    missing = MISSING_NOT_CONFIGURED
    if fetch:
        try:
            rows = _clean_rows(fetch(title))
        except Exception:
            rows, missing = [], MISSING_LOOKUP_FAILED
        else:
            missing = MISSING_NOT_FOUND
        if rows:
            stamp = now.isoformat(timespec="seconds")
            L._atomic(path, {"game": L.clean_title(title), "fetched": stamp, "trophies": rows})
            return {"complete": True, "source": SOURCE_PSN, "fetched": stamp, "stale": False, "missing": None, "note": None,
                    "trophies": _sorted_rows(rows)}
    if saved:
        return from_saved(True)
    return {"complete": False, "source": SOURCE_HISTORY, "fetched": None, "stale": False, "missing": missing,
            "note": INCOMPLETE_NOTE + MISSING_NOTES[missing],
            "trophies": _sorted_rows(earned_from_history(data_dir, player, title))}
