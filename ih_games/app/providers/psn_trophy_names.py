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
TITLES_LOOKUP_MAX = 500    # trophy titles read when looking for one game
TITLE_MATCHES_MAX = 3      # a game can have several trophy lists (regions, platforms)
TROPHY_GROUPS_MAX = 10     # base game plus add-on trophy groups read per list
DEFAULT_GROUP = "default"
KEY_SEPARATOR = ":"        # earned key = "<np communication id>:<trophy id>"; trophy ids repeat across games
EARNED_KEYS_MAX = 2000     # newest earned keys kept in the state file
LEGACY_IDS_MAX = 2000      # old bare trophy ids kept (not used for dedup) after the 0.13.1 migration


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


def trophy_key(np_id, trophy_id):
    """Unique key for one trophy: the same trophy id (0..N) exists in every game."""
    return f"{np_id or ''}{KEY_SEPARATOR}{trophy_id}"


def _split_keys(values):
    """Split stored earned ids into (keyed, legacy). Entries without the separator are old bare ids."""
    keyed, legacy = [], []
    for v in values or []:
        (keyed if KEY_SEPARATOR in str(v) else legacy).append(str(v))
    return keyed, legacy


def _read_history_keys(path):
    """Keys already in the history file. Old lines (no np_id) cannot be attributed to a game: they are
    left in the file and ignored here, so a bare id never hides a trophy of another game."""
    keys = set()
    try:
        with open(path) as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("np_id"):
                    keys.add(trophy_key(row["np_id"], row.get("id")))
    except OSError:
        pass
    return keys


def _value(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _iso_time(value):
    """ISO 8601 text (with the 'T' separator every browser parses), or None."""
    if not value:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value).replace(" ", "T", 1)


def _trophy_user(client, online_id):
    """The object to read trophies from. Prefer the account the NPSSO token belongs to: reading your own
    trophies through a public Online ID lookup returned PSNAWPNotFound on the live house (10 Nov 2026)."""
    me = client.me()
    try:
        own = str(me.online_id).casefold() == str(online_id).casefold()
    except Exception:
        own = False
    return me if own else client.user(online_id=online_id)


def title_trophies(npsso, online_id, game):
    """Every trophy PSN defines for the game title, earned or not, as plain dicts:
    {id, group, name, detail, tier, earned, earned_at, hidden}."""
    from psnawp_api import PSNAWP
    from psnawp_api.models.trophies.trophy_constants import PlatformType

    client = PSNAWP(npsso_cookie=npsso)
    user = _trophy_user(client, online_id)
    titles = list(user.trophy_titles(limit=TITLES_LOOKUP_MAX))
    target = str(game or "").casefold().strip()
    matches = [t for t in titles if str(_value(t, "title_name", "")).casefold().strip() == target]
    if not matches and target:
        matches = [t for t in titles if target in str(_value(t, "title_name", "")).casefold() or str(_value(t, "title_name", "")).casefold() in target]
    if not matches:
        return []

    records = []
    for title in matches[:TITLE_MATCHES_MAX]:
        comm_id = _value(title, "np_communication_id")
        if not comm_id:
            continue
        platforms = _value(title, "title_platform", ()) or ()
        # psnawp needs the PlatformType enum, not its text: with "PS5" as a string it asked the PS4 service
        # and PSN answered "not found" for every PS5 game (found by a live test, 10 Nov 2026).
        platform = next(iter(platforms), PlatformType.PS4)
        groups = user.trophy_groups_summary(comm_id, platform)
        # psnawp returns a TrophyGroupsSummary object (the groups are in .trophy_groups), not a list
        groups = _value(groups, "trophy_groups", groups)
        group_ids = [str(_value(g, "trophy_group_id", DEFAULT_GROUP)) for g in groups]
        if not group_ids:
            group_ids = [DEFAULT_GROUP]
        for group_id in group_ids[:TROPHY_GROUPS_MAX]:
            try:
                trophies = user.trophies(comm_id, platform, include_progress=True, trophy_group_id=group_id, limit=None)
                for trophy in trophies:
                    tier = str(_value(trophy, "trophy_type", "")).lower().rsplit(".", 1)[-1]
                    if tier not in TIERS:
                        continue
                    earned_at = _value(trophy, "earned_date_time")
                    records.append({"id": str(_value(trophy, "trophy_id", "")), "np_id": str(comm_id), "group": group_id,
                                    "name": str(_value(trophy, "trophy_name", "") or "").strip(),
                                    "detail": str(_value(trophy, "trophy_detail", "") or "").strip(),
                                    "tier": tier, "earned": bool(_value(trophy, "earned", False)),
                                    "earned_at": _iso_time(earned_at),
                                    "hidden": bool(_value(trophy, "trophy_hidden", False))})
            except Exception:
                continue
    return records


def _recent_trophies(npsso, online_id, game):
    """Return recent earned trophy records matching the current game title."""
    return [{"name": r["name"], "tier": r["tier"], "earned_at": r["earned_at"], "id": r["id"],
             "np_id": r.get("np_id", "")}
            for r in title_trophies(npsso, online_id, game) if r["earned"] and r["name"] and r["earned_at"]]


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

    known_list, legacy_old = _split_keys(state.get("earned_ids", []))
    legacy_ids = (legacy_old + [i for i in state.get("legacy_ids", []) if i not in legacy_old])[-LEGACY_IDS_MAX:]
    known = set(known_list)
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
        if is_new and trophy_key(record["np_id"], record["id"]) not in known:
            fresh.append(record)
    try:
        feed = json.load(open(feed_path)) if os.path.exists(feed_path) else {"events": []}
    except (OSError, ValueError):
        feed = {"events": []}
    events = list(feed.get("events", []))
    history_path = os.path.join(ctx.data_dir, f"trophy_history_{pkey}.jsonl")
    try:
        seen_ids = _read_history_keys(history_path)
    except OSError:
        seen_ids = set()
    for trophy in fresh:
        if trophy_key(trophy["np_id"], trophy["id"]) not in seen_ids:
            try:
                os.makedirs(os.path.dirname(history_path), exist_ok=True)
                with open(history_path, "a") as hist:
                    hist.write(json.dumps({"time": now, "id": trophy["id"], "np_id": trophy["np_id"], "game": game, "tier": trophy["tier"],
                                           "name": trophy["name"], "earned_at": trophy.get("earned_at")}) + "\n")
            except OSError:
                pass
        events.append({"time": now, "kind": "trophy_named", "tier": trophy["tier"], "count": 1,
                       "game": game, "np_id": trophy["np_id"], "trophy_id": trophy["id"], "achievement": trophy["name"], "big": trophy["tier"] == "platinum",
                       "text": f"Earned {trophy['tier']} trophy: {trophy['name']} in {game}"})
    feed.update({"source": NAME, "player": pkey, "updated": now, "current": {"game": game}, "events": events[-50:]})
    _atomic_write(feed_path, feed)
    state.update({"counts": {t: int(current.get(t, 0) or 0) for t in TIERS},
                  "earned_ids": (known_list + sorted(k for k in ({trophy_key(x["np_id"], x["id"]) for x in newly_earned} - known)))[-EARNED_KEYS_MAX:],
                  "legacy_ids": legacy_ids,
                  "checked_at": now, "updated": now})
    _atomic_write(state_path, state)
