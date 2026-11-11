"""Provider psn_sessions: PlayStation play time from play sessions (PSN reports no hours for most games).

Reads sensor.<trophy_prefix>_now_playing on every provider run. A session starts when a title appears and ends when
the sensor has shown no title for SESSION_GAP_MIN minutes, or when another title replaces it. Unreadable or
'unavailable' readings are ignored, so a short drop-out does not cut a session. Minutes = session start to the last
reading with the title. Sessions shorter than SESSION_MIN_MIN are dropped.
Totals count from the day tracking began (`tracked_since`); they are not lifetime totals.
Output: /data/psn_playtime_<player>.json. It is deliberately not in feeds/, so no chat message is made from it.
Library.refresh() copies the totals into playtime_min, but never over a play time reported by a platform."""
import datetime, json, os, tempfile

NAME = "psn_sessions"
SESSION_MIN_MIN = 5       # shorter sessions are dropped (a title can flash up for a minute or two)
SESSION_GAP_MIN = 10      # minutes without a title before a session is closed
RECENT_SESSIONS_MAX = 30  # closed sessions kept for inspection
SECONDS_PER_MINUTE = 60
UNREADABLE = "?"          # reading to ignore (sensor unavailable or read error)
UNREADABLE_STATES = (None, "", "unavailable", "None")
NO_TITLE_STATE = "unknown"


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), text=True)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _load(path, default):
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else default
    except (OSError, ValueError):
        return default


def _mins(a, b):
    return int((datetime.datetime.fromisoformat(b) - datetime.datetime.fromisoformat(a)).total_seconds() // SECONDS_PER_MINUTE)


def _close(st, data):
    """Finish the open session (if long enough) and add it to the totals."""
    g, since, last = st.get("game"), st.get("since"), st.get("last")
    if g and since and last:
        m = _mins(since, last)
        if m >= SESSION_MIN_MIN:
            t = data["games"].setdefault(g, {"minutes": 0, "sessions": 0, "last_played": None})
            t["minutes"] += m
            t["sessions"] += 1
            t["last_played"] = last
            data["recent"] = (data.get("recent", []) + [{"game": g, "start": since, "end": last, "minutes": m}])[-RECENT_SESSIONS_MAX:]
    st["game"] = st["since"] = st["last"] = st["gap_since"] = None


def reading(state):
    """Sensor state -> title, None (no title) or UNREADABLE."""
    if state in UNREADABLE_STATES:
        return UNREADABLE
    return None if state == NO_TITLE_STATE else state


def step(st, data, game, iso):
    """Apply one reading. game: a title, None (no title) or UNREADABLE (ignored)."""
    if game == UNREADABLE:
        return
    cur = st.get("game")
    if game:
        if cur and cur != game:
            _close(st, data)
            cur = None
        if not cur:
            st["game"], st["since"] = game, iso
        st["last"], st["gap_since"] = iso, None
    elif cur:
        st["gap_since"] = st.get("gap_since") or iso
        if _mins(st["gap_since"], iso) >= SESSION_GAP_MIN:
            _close(st, data)


def read_game(ctx, entity):
    try:
        return reading(ctx.ha("states/" + entity).get("state"))
    except Exception:
        return UNREADABLE


def run(ctx, player, now=None):
    prefix = player.get("trophy_prefix")
    if not prefix:
        return
    pkey = player["key"]
    path = os.path.join(ctx.data_dir, f"psn_playtime_{pkey}.json")
    data = _load(path, {})
    now = now or datetime.datetime.now().astimezone()
    iso = now.isoformat(timespec="seconds")
    data.setdefault("games", {})
    data.setdefault("tracked_since", now.date().isoformat())
    st = data.setdefault("state", {})
    step(st, data, read_game(ctx, f"sensor.{prefix}_now_playing"), iso)
    data["updated"] = iso
    _write(path, data)
