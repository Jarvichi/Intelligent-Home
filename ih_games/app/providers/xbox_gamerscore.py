"""Provider xbox_gamerscore: reads the Home Assistant Xbox sensors and writes a facts feed (gamerscore = achievements).
Totals only: HA gives the gamerscore number and the game being played, not which achievement. Same feed contract as psn_trophies."""
import datetime, json, os, tempfile

NAME = "xbox_gamerscore"
BIG_DELTA = 200   # one jump of this many points (or more) counts as a big moment


def _read(ctx, entity):
    try:
        v = ctx.ha("states/" + entity).get("state")
        return None if v in (None, "", "unavailable", "unknown", "None") else v
    except Exception:
        return None


def _atomic_write(path, data):
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


def run(ctx, player):
    pkey = player["key"]
    prefix = player.get("xbox_prefix")
    if not prefix:
        return   # this player has no Xbox account
    score_entity = player.get("xbox_gamerscore_entity") or f"sensor.{prefix}_gamerscore"
    game_entity = player.get("xbox_now_playing_entity") or f"sensor.{prefix}_now_playing"
    state_file = os.path.join(ctx.data_dir, f"state_{NAME}_{pkey}.json")
    feed_file = os.path.join(ctx.data_dir, "feeds", f"{NAME}_{pkey}.json")
    prev = _load(state_file, {})
    feed = _load(feed_file, {"events": []})

    raw = _read(ctx, score_entity)
    if raw is None or not str(raw).isdigit():
        return
    score = int(raw)
    game = _read(ctx, game_entity)
    now_iso = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    events = []
    if "score" in prev and score > prev["score"]:
        delta = score - prev["score"]
        events.append({"time": now_iso, "kind": "achievement", "tier": "gamerscore", "count": delta, "game": game,
                       "level_up": False, "level": score, "big": delta >= BIG_DELTA,
                       "text": f"Earned {delta} gamerscore" + (f" in {game}" if game else "") + f" (now {score})"})
    # high-water mark: a dip from a glitchy reading must not make the climb back look like new points
    _atomic_write(state_file, {"score": max(prev.get("score", 0), score)})
    feed.update({"source": NAME, "player": pkey, "updated": now_iso,
                 "current": {"gamerscore": score, "now_playing": game}, "events": (feed.get("events", []) + events)[-50:]})
    _atomic_write(feed_file, feed)
