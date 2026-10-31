"""Tool: Destiny 2. Weekly and daily reset computed locally (Bungie resets at 17:00 UTC; shown in the add-on timezone).
Characters, power and milestones from the Bungie API with the bungie_api_key option (public ids in the player entry).
Cached for destiny_poll_s. Read-only; vault and gear need OAuth and are NOT here."""
import datetime, json, os, urllib.request
from zoneinfo import ZoneInfo
import library as L

NAME = "destiny"
TITLES = ("destiny 2", "destiny2", "destiny")
BASE = "https://www.bungie.net/Platform"
CLASSES = {0: "Titan", 1: "Hunter", 2: "Warlock"}
RESET_UTC_HOUR = 17


def matches(entry):
    return L.norm(entry.get("title")) in TITLES


def _tz(tc):
    try:
        return ZoneInfo(tc.opts.get("timezone") or "Europe/London")
    except Exception:
        return datetime.timezone.utc


def next_daily(now, hour=RESET_UTC_HOUR):
    now = now.astimezone(datetime.timezone.utc)
    t = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return t if t > now else t + datetime.timedelta(days=1)


def next_weekly(now, hour=RESET_UTC_HOUR):
    """Next Tuesday at hour:00 UTC strictly after now."""
    t = next_daily(now, hour)
    while t.weekday() != 1:
        t += datetime.timedelta(days=1)
    return t


def _fmt_in(delta):
    m = int(delta.total_seconds() // 60)
    d, m = divmod(m, 1440)
    h, m = divmod(m, 60)
    return (f"{d}d " if d else "") + f"{h}h {m:02d}m"


def resets(tc):
    now = tc.now()
    tz = _tz(tc)
    hour = int(tc.opts.get("destiny_reset_utc_hour", RESET_UTC_HOUR))
    out = []
    for kind, t in (("daily", next_daily(now, hour)), ("weekly", next_weekly(now, hour))):
        out.append({"kind": kind, "at": t.astimezone(tz).isoformat(timespec="minutes"), "in": _fmt_in(t - now),
                    "when": t.astimezone(tz).strftime("%a %d %b %H:%M")})
    return out


def _player(tc, player):
    for p in tc.opts.get("players", []):
        if p.get("key") == player:
            return p
    return {}


def _api(tc, key, path):
    req = urllib.request.Request(BASE + path, headers={"X-API-Key": key, "User-Agent": "ih_games"})
    with tc.open(req, timeout=20) as r:
        d = json.load(r)
    if d.get("ErrorCode", 1) != 1:
        raise RuntimeError("bungie " + str(d.get("ErrorStatus")))
    return d["Response"]


def _cache_path(tc, player):
    return os.path.join(tc.data_dir, "cache", f"destiny_{player}.json")


def _milestone_name(tc, key, cache, h):
    names = cache.setdefault("names", {})
    if str(h) not in names:
        try:
            names[str(h)] = _api(tc, key, f"/Destiny2/Manifest/DestinyMilestoneDefinition/{h}/")["displayProperties"]["name"] or "a milestone"
        except Exception:
            return "a milestone"
    return names[str(h)]


def _progress(ms):
    objs = []
    for a in ms.get("activities", []):
        objs += [c.get("objective", {}) for c in a.get("challenges", [])]
    for q in ms.get("availableQuests", []):
        objs += q.get("status", {}).get("stepObjectives", [])
    if not objs:
        return "active"
    return "%d/%d objectives" % (sum(1 for o in objs if o.get("complete")), len(objs))


def fetch(tc, player, force=False):
    """Cached profile (characters, milestones). Returns dict or {'error': text}."""
    key = (tc.opts.get("bungie_api_key") or "").strip()
    p = _player(tc, player)
    if not (key and p.get("destiny_type") and p.get("destiny_id")):
        return {"error": "Destiny is not set up for this player (needs the Bungie key option and the player's destiny_type and destiny_id)."}
    path = _cache_path(tc, player)
    try:
        cache = json.load(open(path))
    except (OSError, ValueError):
        cache = {}
    ttl = int(tc.opts.get("destiny_poll_s") or 900)
    now = tc.now()
    if not force and cache.get("fetched") and (now - datetime.datetime.fromisoformat(cache["fetched"])).total_seconds() < ttl and "chars" in cache:
        return cache
    try:
        prof = _api(tc, key, f"/Destiny2/{p['destiny_type']}/Profile/{p['destiny_id']}/?components=200,202")
    except Exception as e:
        if "chars" in cache:
            cache["stale"] = True
            return cache
        return {"error": "Bungie is not answering (%s)." % type(e).__name__}
    chars = []
    for cid, c in ((prof.get("characters") or {}).get("data") or {}).items():
        chars.append({"id": cid, "class": CLASSES.get(c.get("classType"), "Guardian"), "power": c.get("light"),
                      "hours": int(c.get("minutesPlayedTotal") or 0) // 60, "last": c.get("dateLastPlayed", "")})
    chars.sort(key=lambda c: c["last"], reverse=True)
    ms_rows = []
    prog = ((prof.get("characterProgressions") or {}).get("data") or {})
    cls_by = {c["id"]: c["class"] for c in chars}
    for cid, cp in prog.items():
        for h, ms in (cp.get("milestones") or {}).items():
            ms_rows.append({"class": cls_by.get(cid, "?"), "name": _milestone_name(tc, key, cache, h), "progress": _progress(ms)})
    cache.update({"fetched": now.isoformat(), "chars": chars, "milestones": ms_rows, "stale": False})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(cache, open(path, "w"))
    return cache


def summary(tc, player):
    r = resets(tc)
    d = fetch(tc, player)
    head = "Destiny 2. Weekly reset %s (in %s); daily %s." % (r[1]["when"], r[1]["in"], r[0]["in"] + " away")
    if d.get("error"):
        return head + " " + d["error"]
    c = d["chars"]
    if not c:
        return head
    top = max(x["power"] or 0 for x in c)
    return head + " Main: %s, power %s, top power %s, %s h on main." % (c[0]["class"], c[0]["power"], top, c[0]["hours"])


def sensor(tc, player):
    """(state, attributes) for the Home Assistant sensor sensor.games_destiny_<player>."""
    r = resets(tc)
    d = fetch(tc, player)
    hour = int(tc.opts.get("destiny_reset_utc_hour", RESET_UTC_HOUR))
    now = tc.now()
    attrs = {"weekly_reset": next_weekly(now, hour).isoformat(timespec="minutes"), "weekly_reset_local": r[1]["when"], "weekly_reset_in": r[1]["in"],
             "next_reset": next_daily(now, hour).isoformat(timespec="minutes"), "daily_reset_local": r[0]["when"], "daily_reset_in": r[0]["in"],
             "milestones": [], "characters": [], "top_power": None, "error": d.get("error") or ""}
    if not d.get("error"):
        seen = set()
        for m in d.get("milestones", []):
            k = (m["name"], m["progress"])
            if k not in seen and len(attrs["milestones"]) < 30:
                seen.add(k)
                attrs["milestones"].append({"name": m["name"], "progress": m["progress"]})
        attrs["characters"] = [{"class": c["class"], "power": c["power"], "hours": c["hours"], "last": (c.get("last") or "")[:10]} for c in d.get("chars", [])]
        attrs["top_power"] = max([c["power"] or 0 for c in d.get("chars", [])] or [0]) or None
    state = "Weekly reset in %s" % r[1]["in"]
    if attrs["top_power"]:
        state += ", power %s" % attrs["top_power"]
    return state, attrs


def upcoming(tc, player):
    r = resets(tc)
    return [{"when": r[0]["at"], "text": "Daily reset (in %s)" % r[0]["in"]}, {"when": r[1]["at"], "text": "Weekly reset: new weeklies and rotators (in %s)" % r[1]["in"]}]


def cmd_week(tc, player, args):
    r = resets(tc)
    d = fetch(tc, player)
    txt = "Weekly reset %s (in %s)." % (r[1]["when"], r[1]["in"])
    if d.get("error"):
        return txt + " " + d["error"]
    ms = d.get("milestones", [])
    if not ms:
        return txt + " No milestones listed right now."
    seen, lines = set(), []
    for m in ms:
        k = (m["name"], m["progress"])
        if k not in seen:
            seen.add(k)
            lines.append("%s: %s" % (m["name"], m["progress"]))
    return txt + " Milestones: " + "; ".join(lines[:12]) + ("." if len(lines) <= 12 else " (+%d more)." % (len(lines) - 12))


def cmd_reset(tc, player, args):
    r = resets(tc)
    return "Daily reset %s (in %s). Weekly reset %s (in %s). Times are Bungie's 17:00 UTC shown in the home timezone." % (r[0]["when"], r[0]["in"], r[1]["when"], r[1]["in"])


def cmd_characters(tc, player, args):
    d = fetch(tc, player)
    if d.get("error"):
        return d["error"]
    return "; ".join("%s power %s, %s h, last played %s" % (c["class"], c["power"], c["hours"], c["last"][:10]) for c in d["chars"]) or "No characters."


def cmd_power(tc, player, args):
    d = fetch(tc, player)
    if d.get("error"):
        return d["error"]
    return ", ".join("%s %s" % (c["class"], c["power"]) for c in d["chars"]) or "No characters."


commands = {"week": cmd_week, "reset": cmd_reset, "characters": cmd_characters, "power": cmd_power}
