"""Provider steam_presence: Steam sessions and level-ups from the Home Assistant Steam sensors (presence only: Steam gives no achievements here).
Session = the now-playing title appears, then disappears; an event is written when it ends and lasted at least MIN_MIN minutes.
Same feed contract as psn_trophies."""
import datetime, json, os, tempfile

NAME = "steam_presence"
MIN_MIN = 20


def _read(ctx, entity):
    try:
        v = ctx.ha("states/" + entity).get("state")
        return None if v in (None, "", "unavailable", "None") else v
    except Exception:
        return "?error"


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


def run(ctx, player, now=None):
    prefix = player.get("steam_prefix")
    if not prefix:
        return
    game_entity = player.get("steam_now_playing_entity") or f"sensor.{prefix}_now_playing"
    level_entity = player.get("steam_level_entity") or f"sensor.{prefix}_level"
    pkey = player["key"]
    state_file = os.path.join(ctx.data_dir, f"state_{NAME}_{pkey}.json")
    feed_file = os.path.join(ctx.data_dir, "feeds", f"{NAME}_{pkey}.json")
    st = _load(state_file, {})
    feed = _load(feed_file, {"events": []})
    now = now or datetime.datetime.now().astimezone()
    iso = now.isoformat(timespec="seconds")
    game = _read(ctx, game_entity)
    if game == "?error":
        return    # could not read: change nothing
    if game == "unknown":
        game = None
    level_raw = _read(ctx, level_entity)
    level = int(level_raw) if level_raw and level_raw.isdigit() else None
    events = []
    cur = st.get("game")
    if game != cur:
        if cur and st.get("since"):    # the previous session just ended
            mins = int((now - datetime.datetime.fromisoformat(st["since"])).total_seconds() // 60)
            if mins >= MIN_MIN:
                events.append({"time": iso, "kind": "session", "tier": None, "count": mins, "game": cur, "level_up": False,
                               "level": level, "big": False, "text": f"Played {cur} for about {mins} min"})
        st["game"], st["since"] = game, (iso if game else None)
    if level is not None:
        if "level" in st and level > st["level"]:
            events.append({"time": iso, "kind": "level", "tier": None, "count": level - st["level"], "game": game, "level_up": True,
                           "level": level, "big": True, "text": f"Steam level up to {level}"})
        st["level"] = max(st.get("level", 0), level)
    feed.update({"source": NAME, "player": pkey, "updated": iso, "current": {"level": st.get("level"), "now_playing": game},
                 "events": (feed.get("events", []) + events)[-50:]})
    _write(feed_file, feed)
    _write(state_file, st)
