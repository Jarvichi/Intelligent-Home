"""List every PSN game with at least one earned trophy (one psnawp call, no per-game calls).

Writes feeds/psn_trophy_titles_<player>.json with a `titles` list. The library adds the games and the Games UI
shows the trophy counts. Uses the user's own NPSSO token; nothing secret is logged or written. Without a token or
an Online ID the provider quietly does nothing.
"""
import datetime
import json
import os

from providers.psn_trophy_names import _atomic_write, _trophy_user, _value

NAME = "psn_trophy_titles"
PSN_TITLES_REFRESH_S = 6 * 3600   # a full list is one API call; the trophy totals change slowly
PSN_TITLES_RETRY_S = 3600         # wait this long after a failed call before trying again
TIER_ORDER = ("bronze", "silver", "gold", "platinum")


def _counts(obj):
    return {t: int(_value(obj, t, 0) or 0) for t in TIER_ORDER}


def _platform_text(platforms):
    names = sorted(str(getattr(p, "value", p)).rsplit(".", 1)[-1] for p in (platforms or ()))
    return "/".join(names)


def _iso(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if value else None


def fetch_titles(npsso, online_id):
    """Titles with at least one earned trophy, as plain dicts."""
    from psnawp_api import PSNAWP

    client = PSNAWP(npsso_cookie=npsso)
    user = _trophy_user(client, online_id)
    out = []
    for t in user.trophy_titles(limit=None):
        title = str(_value(t, "title_name", "") or "").strip()
        earned = _counts(_value(t, "earned_trophies", {}))
        if not title or not sum(earned.values()):
            continue
        out.append({"title": title, "platform": _platform_text(_value(t, "title_platform", ())),
                    "progress": int(_value(t, "progress", 0) or 0), "earned": earned,
                    "defined": _counts(_value(t, "defined_trophies", {})),
                    "last_updated": _iso(_value(t, "last_updated_date_time"))})
    return out


def _due(state, now):
    try:
        last = datetime.datetime.fromisoformat(state.get("attempted_at"))
    except (TypeError, ValueError):
        return True
    wait = PSN_TITLES_REFRESH_S if state.get("ok") else PSN_TITLES_RETRY_S
    return (now - last).total_seconds() >= wait


def run(ctx, player):
    token = str(ctx.opts.get("psn_npsso") or "").strip()
    online_id = str(player.get("psn_online_id") or "").strip()
    if not token or not online_id:
        return
    pkey = player["key"]
    state_path = os.path.join(ctx.data_dir, f"state_{NAME}_{pkey}.json")
    feed_path = os.path.join(ctx.data_dir, "feeds", f"{NAME}_{pkey}.json")
    try:
        with open(state_path) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    now = datetime.datetime.now().astimezone()
    if not _due(state, now):
        return
    stamp = now.isoformat(timespec="seconds")
    try:
        titles = fetch_titles(token, online_id)
    except Exception as exc:
        ctx.log(f"psn_trophy_titles: lookup failed for {pkey} ({type(exc).__name__})")
        _atomic_write(state_path, {"attempted_at": stamp, "ok": False})
        return
    _atomic_write(feed_path, {"source": NAME, "player": pkey, "updated": stamp, "events": [], "titles": titles})
    _atomic_write(state_path, {"attempted_at": stamp, "ok": True, "count": len(titles)})
    ctx.log(f"psn_trophy_titles: {len(titles)} games with trophies for {pkey}")
