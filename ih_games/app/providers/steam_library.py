"""Sync the Steam account's owned games into the Games library.

The API key and profile are read from add-on options. The provider stores only
game metadata and cached sync state; it never logs credentials or request URLs.
"""
import datetime
import json
import os
import tempfile

from providers.steam_achievements import _api_json, _steam_id

NAME = "steam_library"
SYNC_SECONDS = 24 * 60 * 60


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), text=True)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load(path, default):
    try:
        with open(path) as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return default


def _last_played(epoch):
    try:
        value = int(epoch or 0)
        if value > 0:
            return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    except (OverflowError, OSError, TypeError, ValueError):
        pass
    return None


def _owned_games(ctx, api_key, steam_id):
    response = _api_json(ctx, "IPlayerService", "GetOwnedGames", "1", {
        "key": api_key,
        "input_json": json.dumps({
            "steamid": steam_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
        }, separators=(",", ":")),
    })
    result = response.get("response") or {}
    games = result.get("games") or []
    if "game_count" not in result or not isinstance(games, list):
        raise ValueError("Steam did not return public owned-game data")
    return games


def run(ctx, player, now=None):
    api_key = str(ctx.opts.get("steam_api_key") or "").strip()
    if not api_key:
        return
    now_dt = now or datetime.datetime.now().astimezone()
    now_iso = now_dt.isoformat(timespec="seconds")
    now_epoch = now_dt.timestamp()
    state_path = os.path.join(ctx.data_dir, f"state_{NAME}_{player['key']}.json")
    feed_path = os.path.join(ctx.data_dir, "feeds", f"{NAME}_{player['key']}.json")
    state = _load(state_path, {})
    try:
        steam_id = _steam_id(ctx, api_key, player, state, now_epoch)
    except Exception as exc:
        ctx.log(f"steam_library: profile lookup failed for {player['key']} ({type(exc).__name__})")
        return
    if not steam_id:
        return
    if now_epoch - float(state.get("checked_at") or 0) < SYNC_SECONDS:
        return

    try:
        games = _owned_games(ctx, api_key, steam_id)
    except Exception as exc:
        # HTTP exceptions may contain the request URL and the API key.
        ctx.log(f"steam_library: owned-game lookup failed for {player['key']} ({type(exc).__name__})")
        return

    owned_games = []
    for game in games:
        if not isinstance(game, dict):
            continue
        title = str(game.get("name") or "").strip()
        app_id = str(game.get("appid") or "").strip()
        if not title or not app_id.isdigit():
            continue
        try:
            playtime_min = max(0, int(game.get("playtime_forever") or 0))
        except (TypeError, ValueError):
            playtime_min = 0
        owned_games.append({
            "game": title,
            "app_id": app_id,
            "time": _last_played(game.get("rtime_last_played")),
            "playtime_min": playtime_min,
        })

    state.update({"checked_at": now_epoch, "game_count": len(owned_games), "updated": now_iso})
    _atomic_write(state_path, state)
    _atomic_write(feed_path, {
        "source": NAME,
        "player": player["key"],
        "updated": now_iso,
        "current": {},
        # Keep owned-game inventory out of `events`; the chat orchestrator
        # reads event arrays from every provider feed.
        "events": [],
        "owned_games": owned_games,
    })
    ctx.log(f"steam_library: synced {len(owned_games)} owned games for {player['key']}")
