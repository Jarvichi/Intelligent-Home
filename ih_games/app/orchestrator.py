
"""Orchestrator: reads feeds, decides messages, respects quiet hours, and emits HA events."""
import datetime, json, os, random
from zoneinfo import ZoneInfo

TEMPLATES = {
    "congrats": [
        "Nice work on {game}! {events_summary} 🏆",
        "Awesome progress in {game}! {events_summary}.",
        "Smashed it in {game}! {events_summary} 🎉"
    ],
    "ask": [
        "How are you finding {game}? Saw you bagged {events_summary}!",
        "Making moves in {game}! {events_summary}. Enjoying it?"
    ]
}

PLAYFUL = [
    "Spotted you on {game}. {events_summary}. Go on then, how did it go?",
    "Right, {game}: {events_summary}. Not bad. Enjoying it?",
    "{game} again, eh? {events_summary}. Tell me about it.",
]


def _dt(t):
    try:
        return datetime.datetime.fromisoformat(t)
    except Exception:
        return None


def _parse_time(t_str):
    h, m = map(int, t_str.split(":"))
    return datetime.time(h, m)

def _is_quiet(now_dt, q_start_str, q_end_str):
    qs = _parse_time(q_start_str)
    qe = _parse_time(q_end_str)
    t = now_dt.time()
    if qs <= qe:
        return qs <= t <= qe
    return t >= qs or t <= qe

def run(ctx, now=None):
    opts = ctx.opts
    try:
        now_dt = now or datetime.datetime.now(ZoneInfo(opts.get("timezone") or "Europe/London"))
    except Exception:
        now_dt = now or datetime.datetime.now().astimezone()
    now_iso = now_dt.isoformat(timespec="seconds")
    
    q_start = opts.get("quiet_start", "22:00")
    q_end = opts.get("quiet_end", "08:00")
    in_quiet = _is_quiet(now_dt, q_start, q_end)

    data_dir = ctx.data_dir
    feeds_dir = os.path.join(data_dir, "feeds")
    msg_log_path = os.path.join(data_dir, "messages.jsonl")

    sent_messages = []
    if os.path.exists(msg_log_path):
        with open(msg_log_path, "r") as f:
            for line in f:
                if line.strip():
                    try:
                        sent_messages.append(json.loads(line))
                    except Exception:
                        pass

    for p in opts.get("players", []):
        pkey = p["key"]
        speaker = p.get("speaker", "bot")
        
        player_msgs = [m for m in sent_messages if m["player"] == pkey and m.get("sent") and not m.get("dry")]
        today_str = now_dt.strftime("%Y-%m-%d")
        msgs_today = [m for m in player_msgs if m["time"].startswith(today_str)]
        
        last_time = None
        if player_msgs:
            try:
                last_time = datetime.datetime.fromisoformat(player_msgs[-1]["time"])
            except Exception:
                pass

        # Load all provider feeds for this player
        events_to_process = []
        is_playing = False
        
        if os.path.exists(feeds_dir):
            for fn in os.listdir(feeds_dir):
                if fn.endswith(f"_{pkey}.json"):
                    try:
                        with open(os.path.join(feeds_dir, fn), "r") as f:
                            fdata = json.load(f)
                            if fdata.get("current", {}).get("now_playing"):
                                is_playing = True
                            events_to_process.extend(fdata.get("events", []))
                    except Exception:
                        pass

        # Filter out events already sent
        handled = [_dt(m["event_time"]) for m in sent_messages if m["player"] == pkey and m.get("event_time")]
        handled_upto = max([h for h in handled if h] or [None])   # dry runs count as handled too (no log spam)
        unhandled = [e for e in events_to_process if _dt(e.get("time")) and (handled_upto is None or _dt(e["time"]) > handled_upto)]

        # Drop events older than 12 hours
        cutoff = now_dt - datetime.timedelta(hours=12)
        valid_events = []
        for e in unhandled:
            try:
                etime = datetime.datetime.fromisoformat(e["time"])
                if etime >= cutoff:
                    valid_events.append(e)
            except Exception:
                pass

        if not valid_events:
            continue

        has_big = any(e.get("big") for e in valid_events)
        in_quiet = _is_quiet(now_dt, p.get("quiet_start") or q_start, p.get("quiet_end") or q_end)   # per-player override

        # Check constraints
        if in_quiet:
            continue
            
        if is_playing and not has_big:
            continue

        if len(msgs_today) >= int(p.get("max_per_day", 1)) + (1 if has_big else 0):
            continue

        if last_time and (now_dt - last_time) < datetime.timedelta(hours=3):
            continue

        # Form message
        game = valid_events[-1].get("game") or "your game"
        summaries = [e.get("text", "") for e in valid_events]
        summary_str = ", ".join(summaries)

        rnd = random.Random(f"{pkey}_{valid_events[0]['time']}")
        kind = rnd.choice(["congrats", "ask"])
        tmpl = rnd.choice(PLAYFUL if p.get("voice") == "playful" else TEMPLATES[kind])
        msg_text = tmpl.format(game=game, events_summary=summary_str)

        msg_payload = {
            "player": pkey,
            "speaker": speaker,
            "kind": kind,
            "text": msg_text
        }

        sent_ok = False
        if ctx.dry:
            ctx.log(f"[DRY] would fire ih_games_message: {msg_payload}")
            sent_ok = True
        else:
            try:
                ctx.ha("events/ih_games_message", msg_payload)
                sent_ok = True
            except Exception as ex:
                ctx.log(f"Failed to fire event: {ex}")

        log_entry = {
            "time": now_iso,
            "player": pkey,
            "kind": kind,
            "text": msg_text,
            "sent": sent_ok and not ctx.dry,
            "dry": bool(ctx.dry),
            "event_time": valid_events[-1]["time"]
        }

        with open(msg_log_path, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
        if sent_ok and not ctx.dry:   # outbox: a sender on the host collects these (GET /outbox, POST /ack) and delivers them
            with open(os.path.join(data_dir, "outbox.jsonl"), "a") as f:
                f.write(json.dumps(dict(msg_payload, id=f"{pkey}-{valid_events[-1]['time']}", time=now_iso)) + "\n")


