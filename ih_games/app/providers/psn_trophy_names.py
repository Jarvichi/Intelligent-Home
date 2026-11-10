"""Enrich rising PSN trophy totals with the names of newly earned trophies.

Uses the user's own PSN NPSSO session token. No token is logged or written to
disk. If no token is configured, the provider quietly does nothing.
"""
import datetime
import json
import os
import tempfile

NAME = "psn_trophy_names"
TIERS = {"bronze", "silver", "gold", "platinum"}


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


def _value(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _trophy_user(client, online_id):
    """The object to read trophies from. Prefer the account the NPSSO token belongs to: reading your own
    trophies through a public Online ID lookup returned PSNAWPNotFound on the live house (10 Nov 2026)."""
    me = client.me()
    try:
        own = str(me.online_id).casefold() == str(online_id).casefold()
    except Exception:
        own = False
    return me if own else client.user(online_id=online_id)


def _recent_trophies(npsso, online_id, game):
    """Return recent earned trophy records matching the current game title."""
    from psnawp_api import PSNAWP
    from psnawp_api.models.trophies.trophy_constants import PlatformType

    client = PSNAWP(npsso_cookie=npsso)
    user = _trophy_user(client, online_id)
    titles = list(user.trophy_titles(limit=500))
    target = str(game or "").casefold().strip()
    matches = [t for t in titles if str(_value(t, "title_name", "")).casefold().strip() == target]
    if not matches and target:
        matches = [t for t in titles if target in str(_value(t, "title_name", "")).casefold() or str(_value(t, "title_name", "")).casefold() in target]
    if not matches:
        return []

    records = []
    for title in matches[:3]:
        comm_id = _value(title, "np_communication_id")
        if not comm_id:
            continue
        platforms = _value(title, "title_platform", ()) or ()
        # psnawp needs the PlatformType enum, not its text: with "PS5" as a string it asked the PS4 service
        # and PSN answered "not found" for every PS5 game (found by a live test, 10 Nov 2026).
        platform = next(iter(platforms), PlatformType.PS4)
        groups = user.trophy_groups_summary(comm_id, platform)
        group_ids = [str(_value(g, "trophy_group_id", "default")) for g in groups]
        if not group_ids:
            group_ids = ["default"]
        for group_id in group_ids[:10]:
            try:
                trophies = user.trophies(comm_id, platform, include_progress=True, trophy_group_id=group_id, limit=None)
                for trophy in trophies:
                    earned_at = _value(trophy, "earned_date_time")
                    name = str(_value(trophy, "trophy_name", "") or "").strip()
                    if not _value(trophy, "earned", False) or not name or not earned_at:
                        continue
                    tier = str(_value(trophy, "trophy_type", "")).lower().rsplit(".", 1)[-1]
                    if tier in TIERS:
                        records.append({"name": name, "tier": tier, "earned_at": str(earned_at), "id": str(_value(trophy, "trophy_id", ""))})
            except Exception:
                continue
    return records


def run(ctx, player):
    prefix = player.get("trophy_prefix")
    token = str(ctx.opts.get("psn_npsso") or "").strip()
    online_id = str(player.get("psn_online_id") or "").strip()
    if not prefix or not token or not online_id:
        return

    pkey = player["key"]
    state_path = os.path.join(ctx.data_dir, f"state_{NAME}_{pkey}.json")
    feed_path = os.path.join(ctx.data_dir, "feeds", f"{NAME}_{pkey}.json")
    try:
        state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    except (OSError, ValueError):
        state = {}
    try:
        totals = json.load(open(os.path.join(ctx.data_dir, f"state_psn_trophies_{pkey}.json")))
    except (OSError, ValueError):
        return

    current = totals.get("counts", {})
    previous = state.get("counts", {})
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    # Establish a quiet baseline so configuring the token does not announce the
    # player's entire back catalogue as newly earned trophies.
    if not state:
        _atomic_write(state_path, {"counts": {t: int(current.get(t, 0) or 0) for t in TIERS},
                                   "earned_ids": [], "checked_at": now})
        return
    if not any(int(current.get(t, 0) or 0) > int(previous.get(t, 0) or 0) for t in TIERS):
        return
    game = totals.get("now_playing")
    if not game:
        return

    # Fetch first; only move the high-water mark after a successful API call.
    try:
        newly_earned = _recent_trophies(token, online_id, game)
    except Exception as exc:
        # Keep the exception type only; library errors may include request data.
        ctx.log(f"psn_trophy_names: lookup failed for {pkey} ({type(exc).__name__})")
        return

    known = set(state.get("earned_ids", []))
    checked_at = state.get("checked_at")
    try:
        checked_dt = datetime.datetime.fromisoformat(checked_at) if checked_at else None
    except (TypeError, ValueError):
        checked_dt = None
    fresh = []
    for record in newly_earned:
        try:
            earned_dt = datetime.datetime.fromisoformat(record["earned_at"].replace("Z", "+00:00"))
            boundary = checked_dt
            if boundary and boundary.tzinfo is None:
                boundary = boundary.replace(tzinfo=earned_dt.tzinfo)
            is_new = boundary is not None and earned_dt > boundary
        except (TypeError, ValueError):
            is_new = False
        if is_new and record["id"] not in known:
            fresh.append(record)
    try:
        feed = json.load(open(feed_path)) if os.path.exists(feed_path) else {"events": []}
    except (OSError, ValueError):
        feed = {"events": []}
    events = list(feed.get("events", []))
    history_path = os.path.join(ctx.data_dir, f"trophy_history_{pkey}.jsonl")
    try:
        seen_ids = {json.loads(l).get("id") for l in open(history_path) if l.strip()} if os.path.exists(history_path) else set()
    except (OSError, ValueError):
        seen_ids = set()
    for trophy in fresh:
        if trophy["id"] not in seen_ids:
            try:
                os.makedirs(os.path.dirname(history_path), exist_ok=True)
                with open(history_path, "a") as hist:
                    hist.write(json.dumps({"time": now, "id": trophy["id"], "game": game, "tier": trophy["tier"],
                                           "name": trophy["name"], "earned_at": trophy.get("earned_at")}) + "\n")
            except OSError:
                pass
        events.append({"time": now, "kind": "trophy_named", "tier": trophy["tier"], "count": 1,
                       "game": game, "achievement": trophy["name"], "big": trophy["tier"] == "platinum",
                       "text": f"Earned {trophy['tier']} trophy: {trophy['name']} in {game}"})
    feed.update({"source": NAME, "player": pkey, "updated": now, "current": {"game": game}, "events": events[-50:]})
    _atomic_write(feed_path, feed)
    state.update({"counts": {t: int(current.get(t, 0) or 0) for t in TIERS},
                  "earned_ids": list((known | {x["id"] for x in newly_earned}) )[-2000:],
                  "checked_at": now, "updated": now})
    _atomic_write(state_path, state)
