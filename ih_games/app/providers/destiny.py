"""Provider destiny: Destiny 2 sessions from the public Bungie API (API key only, no OAuth). Same feed contract as psn_trophies.
A session = new activities; it ends after 30 minutes with no new activity, then one 'session' event is written.
Polls Bungie at most every destiny_poll_s (default 900) however often it is called. Ids in the player dict are public Bungie ids."""
import datetime, json, os, tempfile, urllib.request
from zoneinfo import ZoneInfo

NAME = "destiny"
BASE = "https://www.bungie.net/Platform"
RESET_UTC_HOUR = 17   # Bungie weekly reset, Tuesday 17:00 UTC. UNCONFIRMED: not verifiable from repo/fixtures.


def last_weekly_reset(now):
    """Most recent Tuesday RESET_UTC_HOUR:00 UTC at or before now (aware datetime)."""
    u = now.astimezone(datetime.timezone.utc)
    t = u.replace(hour=RESET_UTC_HOUR, minute=0, second=0, microsecond=0)
    while t.weekday() != 1 or t > u:
        t -= datetime.timedelta(days=1)
    return t


def reset_local_hhmm(now, tz):
    return last_weekly_reset(now).astimezone(tz).strftime("%H:%M")


CLASSES = {0: "Titan", 1: "Hunter", 2: "Warlock"}
MODES = {2: "Story", 3: "Strike", 4: "Raid", 5: "Crucible (PvP)", 6: "Patrol", 7: "Story", 10: "Control", 12: "Clash", 16: "Nightfall",
         18: "Strike", 19: "Iron Banner", 31: "Supremacy", 32: "Private match", 37: "Survival", 38: "Countdown", 39: "Trials", 46: "Nightfall",
         47: "Nightfall", 48: "Rumble", 49: "Rumble", 63: "Gambit", 69: "Competitive", 70: "Quickplay", 71: "Clash", 73: "Mayhem",
         75: "Salvage", 82: "Dungeon", 84: "Trials", 89: "Zone Control", 91: "Lost Sector", 92: "Showdown", 100: "Dungeon",
         101: "Exotic mission", 104: "Gauntlet"}
PVP = {5, 10, 12, 19, 31, 32, 37, 38, 39, 48, 49, 69, 70, 71, 73, 75, 84, 89, 92}
QUIET_MIN = 30


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), text=True)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _load(path, default):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


def _api(ctx, key, path):
    req = urllib.request.Request(BASE + path, headers={"X-API-Key": key, "User-Agent": "ih_games"})
    with ctx.open(req, timeout=20) as r:
        return json.load(r)["Response"]


def _name(ctx, key, st, h):
    names = st.setdefault("names", {})
    if str(h) not in names:
        try:
            names[str(h)] = _api(ctx, key, f"/Destiny2/Manifest/DestinyActivityDefinition/{h}/")["displayProperties"]["name"] or "an activity"
        except Exception:
            return "an activity"
    return names[str(h)]


def summarise(sess):
    kills, deaths = sum(a["kills"] for a in sess), sum(a["deaths"] for a in sess)
    mins = max(1, sum(a["secs"] for a in sess) // 60)
    kinds = sorted({MODES.get(a["mode"], "activity") for a in sess})
    pvp = sum(1 for a in sess if a["mode"] in PVP)
    return (f"{len(sess)} activit{'y' if len(sess) == 1 else 'ies'} in about {mins} min as {sess[-1]['class']} ({', '.join(kinds[:3])}); "
            f"{kills} kills, {deaths} deaths" + (f"; {pvp} PvP" if pvp else "; all PvE"))


def ago(iso, now):
    days = (now - datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))).days
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"


def render_note(player, chars, sessions, now, tz):
    """Markdown block like the host tracker's brain note block (characters + last 5 sessions). Written to /data/notes; never to the brain."""
    rows = "\n".join(f"- {c['class']}: Power {c['power']}, about {c['hours']} h played, last played {ago(c['last'], now)}" for c in chars)
    sess = "\n".join(f"- {s['when']}: {s['text']}" for s in sessions[-5:][::-1]) or "- none recorded yet"
    main = chars[0]["class"] if chars else "?"
    return (f"## Destiny 2 (live, updated {now.astimezone(tz):%d %b %H:%M})\n"
            f"Account {player.get('display_name') or player['key']}. Main character is the {main} (most recently played).\n{rows}\n\nRecent sessions:\n{sess}\n")


def run(ctx, player, now=None):
    pkey, key = player["key"], (ctx.opts.get("bungie_api_key") or "").strip()
    mtype, mid = player.get("destiny_type"), player.get("destiny_id")
    if not (key and mtype and mid):
        return
    state_file = os.path.join(ctx.data_dir, f"state_{NAME}_{pkey}.json")
    feed_file = os.path.join(ctx.data_dir, "feeds", f"{NAME}_{pkey}.json")
    st = _load(state_file, {})
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if st.get("polled") and (now - datetime.datetime.fromisoformat(st["polled"])).total_seconds() < int(ctx.opts.get("destiny_poll_s", 900)):
        return
    prof = _api(ctx, key, f"/Destiny2/{mtype}/Profile/{mid}/?components=200")
    chars, acts = [], []
    for cid, c in prof["characters"]["data"].items():
        cls = CLASSES.get(c["classType"], "Guardian")
        chars.append({"class": cls, "power": c["light"], "hours": int(c["minutesPlayedTotal"]) // 60, "last": c["dateLastPlayed"]})
        got = _api(ctx, key, f"/Destiny2/{mtype}/Account/{mid}/Character/{cid}/Stats/Activities/?count=10").get("activities", [])
        for a in got:
            v = a["values"]
            acts.append({"t": a["period"], "class": cls, "mode": a["activityDetails"].get("mode", 0),
                         "name": _name(ctx, key, st, a["activityDetails"]["directorActivityHash"]),
                         "kills": int(v["kills"]["basic"]["value"]), "deaths": int(v["deaths"]["basic"]["value"]),
                         "secs": int(v["activityDurationSeconds"]["basic"]["value"])})
    acts.sort(key=lambda a: a["t"])
    chars.sort(key=lambda c: c["last"], reverse=True)
    newest = acts[-1]["t"] if acts else ""
    events = []
    if "seen" not in st:
        st["seen"] = newest    # first run: baseline only
    else:
        new = [a for a in acts if a["t"] > st["seen"]]
        if new:
            st["pending"] = st.get("pending", []) + new
            st["seen"] = newest
        pend = st.get("pending", [])
        pend_when = datetime.datetime.fromisoformat(pend[0]["t"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/London")).strftime("%d %b %H:%M") if pend else ""
        if pend and now - datetime.datetime.fromisoformat(pend[-1]["t"].replace("Z", "+00:00")) > datetime.timedelta(minutes=QUIET_MIN):
            events.append({"time": now.astimezone().isoformat(timespec="seconds"), "kind": "session", "tier": None, "count": len(pend),
                           "game": "Destiny 2", "level_up": False, "level": chars[0]["power"] if chars else None, "big": False,
                           "text": summarise(pend)})
            st["pending"] = []
            st.setdefault("sessions", []).append({"when": pend_when, "text": events[-1]["text"]})
    # weekly reset: Tuesday 17:00 UTC (Bungie; UNCONFIRMED from repo, documented constant), shown in UK time, once per reset
    try:
        tz = ZoneInfo(ctx.opts.get("timezone") or "Europe/London")
    except Exception:
        tz = datetime.timezone.utc
    last = last_weekly_reset(now)
    tag = last.strftime("%Y-%m-%d")
    if player.get("destiny_reset") and now - last < datetime.timedelta(hours=24) and st.get("reset_sent") != tag:
        events.append({"time": now.astimezone().isoformat(timespec="seconds"), "kind": "reset", "tier": None, "count": 0,
                       "game": "Destiny 2", "level_up": False, "level": None, "big": False,
                       "text": "Destiny weekly reset has just happened (Tuesday %s UK time): new weeklies and rotators are up." % reset_local_hhmm(now, tz)})
        st["reset_sent"] = tag
    playing = bool(st.get("pending"))   # activity in the last 30 min: the session is still open
    st["polled"] = now.isoformat()
    feed = _load(feed_file, {"events": []})
    feed.update({"source": NAME, "player": pkey, "updated": now.astimezone().isoformat(timespec="seconds"),
                 "current": {"characters": chars, "now_playing": "Destiny 2" if playing else None},
                 "events": (feed.get("events", []) + events)[-50:]})
    try:
        tzn = ZoneInfo(ctx.opts.get("timezone") or "Europe/London")
    except Exception:
        tzn = datetime.timezone.utc
    note = render_note(player, chars, st.get("sessions", []), now, tzn)
    feed["current"]["note_md"] = note
    os.makedirs(os.path.join(ctx.data_dir, "notes"), exist_ok=True)
    open(os.path.join(ctx.data_dir, "notes", f"{NAME}_{pkey}.md"), "w").write(note)
    _write(feed_file, feed)
    _write(state_file, st)
