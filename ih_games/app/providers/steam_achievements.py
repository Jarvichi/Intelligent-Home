"""Read newly unlocked Steam achievement names for the game currently in Steam Online.

The Steam Web API key is read from add-on options and is never logged or stored.
The first successful lookup for each game is a quiet baseline.
"""
import datetime
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request

NAME = "steam_achievements"
POLL_SECONDS = 300
SCHEMA_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_GAMES = 50
MAX_KNOWN_PER_GAME = 3000
STEAM_ID_RE = re.compile(r"^\d{17}$")
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


def _api_json(ctx, interface, method, version, params):
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        "https://api.steampowered.com/" + interface + "/" + method + "/v" + version + "/?" + query,
        headers={"Accept": "application/json", "User-Agent": "ih_games/steam-achievements"},
    )
    with ctx.open(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _steam_id(ctx, api_key, player, state, now_epoch):
    direct = str(player.get("steam_id") or "").strip()
    if STEAM_ID_RE.fullmatch(direct):
        return direct
    profile_url = str(player.get("steam_profile_url") or "").strip()
    if not profile_url:
        return ""
    parsed = urllib.parse.urlsplit(profile_url)
    if parsed.scheme not in ("https", "http") or parsed.hostname not in ("steamcommunity.com", "www.steamcommunity.com"):
        return ""
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return ""
    kind, value = parts[0].lower(), parts[1]
    if kind == "profiles" and STEAM_ID_RE.fullmatch(value):
        return value
    if kind != "id" or not value:
        return ""
    if state.get("resolved_profile_url") == profile_url:
        cached = str(state.get("resolved_steam_id") or "")
        if STEAM_ID_RE.fullmatch(cached):
            return cached
    response = _api_json(ctx, "ISteamUser", "ResolveVanityURL", "1", {
        "key": api_key, "vanityurl": value,
    })
    result = response.get("response") or {}
    steam_id = str(result.get("steamid") or "")
    if result.get("success") not in (1, "1") or not STEAM_ID_RE.fullmatch(steam_id):
        raise ValueError("Steam could not resolve the profile URL")
    state["resolved_profile_url"] = profile_url
    state["resolved_steam_id"] = steam_id
    state["resolved_at"] = now_epoch
    return steam_id


def _read_game(ctx, player):
    prefix = player.get("steam_prefix")
    entity = player.get("steam_now_playing_entity") or (f"sensor.{prefix}_now_playing" if prefix else "")
    if not entity:
        return None, None, None
    try:
        state = ctx.ha("states/" + entity)
    except Exception:
        return None, None, None
    if not isinstance(state, dict):
        return None, None, None
    title = str(state.get("state") or "").strip()
    if title.casefold() in ("", "unknown", "unavailable", "none"):
        return None, None, None
    attrs = state.get("attributes") or {}
    raw_appid = attrs.get("app_id") or attrs.get("steam_app_id")
    try:
        appid = int(raw_appid)
    except (TypeError, ValueError):
        return title, None, None
    if appid <= 0:
        return title, None, None
    return title, str(appid), appid


def _schema(ctx, api_key, appid):
    response = _api_json(ctx, "ISteamUserStats", "GetSchemaForGame", "2", {"key": api_key, "appid": appid, "l": "english"})
    game = response.get("game") or {}
    available = game.get("availableGameStats") or {}
    rows = available.get("achievements") or []
    result = {}
    for item in rows:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        result[str(item["name"])] = {
            "display_name": str(item.get("displayName") or "").strip(),
            "hidden": bool(item.get("hidden", 0)),
        }
    return result, str(game.get("gameName") or "").strip()


def _player_achievements(ctx, api_key, steam_id, appid):
    response = _api_json(ctx, "ISteamUserStats", "GetPlayerAchievements", "1", {
        "key": api_key, "steamid": steam_id, "appid": appid, "l": "english",
    })
    stats = response.get("playerstats") or {}
    success = stats.get("success")
    if success not in (True, 1, "1"):
        raise ValueError("Steam did not return public player achievement data")
    result = {}
    for item in stats.get("achievements") or []:
        if not isinstance(item, dict) or not item.get("apiname"):
            continue
        try:
            achieved = int(item.get("achieved") or 0) == 1
            unlocktime = int(item.get("unlocktime") or 0)
        except (TypeError, ValueError):
            continue
        if achieved:
            result[str(item["apiname"])] = unlocktime
    return result


def _event_time(epoch, now):
    if epoch > 0:
        try:
            return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            pass
    return now


def run(ctx, player, now=None):
    api_key = str(ctx.opts.get("steam_api_key") or "").strip()
    if not api_key:
        return
    title, appid_key, appid = _read_game(ctx, player)
    if not title or not appid:
        return

    now_dt = now or datetime.datetime.now().astimezone()
    now_iso = now_dt.isoformat(timespec="seconds")
    now_epoch = now_dt.timestamp()
    state_path = os.path.join(ctx.data_dir, f"state_{NAME}_{player['key']}.json")
    feed_path = os.path.join(ctx.data_dir, "feeds", f"{NAME}_{player['key']}.json")
    history_path = os.path.join(ctx.data_dir, f"steam_achievement_history_{player['key']}.jsonl")
    state = _load(state_path, {"games": {}})
    try:
        steam_id = _steam_id(ctx, api_key, player, state, now_epoch)
    except Exception as exc:
        ctx.log(f"steam_achievements: profile lookup failed for {player['key']} ({type(exc).__name__})")
        return
    if not steam_id:
        return
    games = state.get("games") if isinstance(state.get("games"), dict) else {}
    app_state = games.get(appid_key) if isinstance(games.get(appid_key), dict) else {}
    checked_at = float(app_state.get("checked_at") or 0)
    if now_epoch - checked_at < POLL_SECONDS:
        return

    try:
        schema = app_state.get("schema") if isinstance(app_state.get("schema"), dict) else {}
        schema_at = float(app_state.get("schema_at") or 0)
        if not schema or now_epoch - schema_at >= SCHEMA_TTL_SECONDS:
            schema, api_title = _schema(ctx, api_key, appid)
            app_state["schema"] = schema
            app_state["schema_at"] = now_epoch
        else:
            api_title = ""
        unlocked = _player_achievements(ctx, api_key, steam_id, appid)
    except Exception as exc:
        # HTTP/library exceptions can contain the request URL, including the key.
        ctx.log(f"steam_achievements: lookup failed for {player['key']} ({type(exc).__name__})")
        return

    known = set(app_state.get("known", []))
    first_lookup = "known" not in app_state
    new_names = []
    if not first_lookup:
        for api_name, unlocktime in unlocked.items():
            if api_name in known:
                continue
            display_name = str((schema.get(api_name) or {}).get("display_name") or "").strip()
            if display_name:
                new_names.append((api_name, display_name, unlocktime))

    try:
        feed = _load(feed_path, {"events": []})
        events = list(feed.get("events", []))
    except Exception:
        events = []
        feed = {"events": []}
    for api_name, display_name, unlocktime in new_names:
        event_id = f"{appid_key}:{api_name}"
        events.append({
            "time": now_iso,
            "kind": "achievement",
            "platform": "steam",
            "count": 1,
            "tier": None,
            "game": title or api_title or f"Steam app {appid_key}",
            "achievement": display_name,
            "achievement_id": event_id,
            "earned_at": _event_time(unlocktime, now_iso),
            "big": False,
            "text": f"Unlocked {display_name} in {title or api_title or 'your Steam game'}",
        })
        try:
            with open(history_path, "a") as history:
                history.write(json.dumps({
                    "id": event_id,
                    "app_id": appid_key,
                    "game": title or api_title,
                    "name": display_name,
                    "earned_at": _event_time(unlocktime, now_iso),
                    "recorded_at": now_iso,
                }) + "\n")
        except OSError:
            pass

    updated_app = dict(app_state)
    updated_app.update({
        "known": list((known | set(unlocked.keys())))[-MAX_KNOWN_PER_GAME:],
        "checked_at": now_epoch,
        "game": title or api_title,
    })
    games[appid_key] = updated_app
    # Bound persistent schema/state growth to recently checked games.
    games = dict(sorted(games.items(), key=lambda item: float((item[1] or {}).get("checked_at") or 0))[-MAX_GAMES:])
    state.update({"games": games, "updated": now_iso})
    _atomic_write(state_path, state)
    feed.update({
        "source": NAME,
        "player": player["key"],
        "updated": now_iso,
        "current": {"now_playing": title, "app_id": appid_key},
        "events": events[-50:],
    })
    _atomic_write(feed_path, feed)
