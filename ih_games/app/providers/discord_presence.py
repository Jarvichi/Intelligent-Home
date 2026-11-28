"""Provider discord_presence (27 Nov 2026): PC games from Discord's "Playing X" (listener: app/discord_presence.py writes presence_<player>.json).
Same feed contract and session rule as steam_presence: a session ends when the title goes away and counts if it lasted at least MIN_MIN minutes.
Players opt in with `discord_user_id`; no id, no feed. current.now_playing is the live game (the orchestrator holds back announcements while it is set)."""
import datetime, json, os

from providers import steam_presence as S

NAME = "discord_presence"
MIN_MIN = 20


def _current(ctx, pkey):
    """The listener's file: (game or None, ok). A missing file means the listener has not seen the person yet: change nothing."""
    try:
        d = json.load(open(os.path.join(ctx.data_dir, "presence_%s.json" % pkey)))
    except (OSError, ValueError):
        return None, False
    g = d.get("game")
    return (g if isinstance(g, str) and g else None), True


def run(ctx, player, now=None):
    if not player.get("discord_user_id"):
        return
    pkey = player["key"]
    game, ok = _current(ctx, pkey)
    if not ok:
        return
    state_file = os.path.join(ctx.data_dir, "state_%s_%s.json" % (NAME, pkey))
    feed_file = os.path.join(ctx.data_dir, "feeds", "%s_%s.json" % (NAME, pkey))
    st = S._load(state_file, {})
    feed = S._load(feed_file, {"events": []})
    now = now or datetime.datetime.now().astimezone()
    iso = now.isoformat(timespec="seconds")
    events, cur = [], st.get("game")
    if game != cur:
        if cur and st.get("since"):
            mins = int((now - datetime.datetime.fromisoformat(st["since"])).total_seconds() // 60)
            if mins >= MIN_MIN:
                events.append({"time": iso, "kind": "session", "tier": None, "count": mins, "game": cur, "level_up": False,
                               "level": None, "big": False, "text": "Played %s for about %d min" % (cur, mins)})
        st["game"], st["since"] = game, (iso if game else None)
    feed.update({"source": NAME, "player": pkey, "updated": iso, "current": {"now_playing": game, "since": st.get("since")},
                 "events": (feed.get("events", []) + events)[-50:]})
    S._write(feed_file, feed)
    S._write(state_file, st)
