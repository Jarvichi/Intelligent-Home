
"""Provider psn_trophies: reads HA sensors for PSN trophies and writes atomic facts feeds."""
import datetime, json, os, tempfile

NAME = "psn_trophies"
TIERS = ("platinum", "gold", "silver", "bronze")

def _read_sensor(ctx, entity):
    try:
        s = ctx.ha("states/" + entity)
        val = s.get("state")
        if val in (None, "unavailable", "unknown"):
            return None
        return val
    except Exception:
        return None

def _atomic_write(filepath, data):
    dir_name = os.path.dirname(filepath)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, text=True)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, filepath)

def run(ctx, player):
    pkey = player["key"]
    prefix = player.get("trophy_prefix", pkey)
    
    state_file = os.path.join(ctx.data_dir, f"state_{NAME}_{pkey}.json")
    feed_file = os.path.join(ctx.data_dir, "feeds", f"{NAME}_{pkey}.json")
    
    prev_state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                prev_state = json.load(f)
        except Exception:
            prev_state = {}

    feed_data = {"source": NAME, "player": pkey, "updated": "", "current": {}, "events": []}
    if os.path.exists(feed_file):
        try:
            with open(feed_file, "r") as f:
                feed_data = json.load(f)
        except Exception:
            pass

    cur_level_s = _read_sensor(ctx, f"sensor.{prefix}_trophy_level")
    cur_next_s = _read_sensor(ctx, f"sensor.{prefix}_next_level")
    cur_now_s = _read_sensor(ctx, f"sensor.{prefix}_now_playing")
    
    cur_counts = {}
    for tier in TIERS:
        v = _read_sensor(ctx, f"sensor.{prefix}_{tier}_trophies")
        cur_counts[tier] = int(v) if v is not None and v.isdigit() else None

    if cur_level_s is None or any(cur_counts[t] is None for t in TIERS):
        return

    cur_level = int(cur_level_s) if cur_level_s.isdigit() else 0
    cur_game = cur_now_s if cur_now_s and cur_now_s not in ("unavailable", "unknown", "None") else None

    cur_state = {
        "level": cur_level,
        "next_level": cur_next_s,
        "now_playing": cur_game,
        "counts": cur_counts
    }

    now_iso = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    new_events = []

    if prev_state:
        prev_counts = prev_state.get("counts", {})
        prev_level = prev_state.get("level", cur_level)
        level_up = cur_level > prev_level

        for tier in TIERS:
            old_c = prev_counts.get(tier)
            new_c = cur_counts.get(tier)
            if old_c is not None and new_c is not None and new_c > old_c:
                inc = new_c - old_c
                is_big = (tier == "platinum") or level_up
                txt = f"Earned {inc} {tier} trophy" if inc == 1 else f"Earned {inc} {tier} trophies"
                if cur_game:
                    txt += f" in {cur_game}"
                if level_up:
                    txt += f" and reached level {cur_level}"
                
                new_events.append({
                    "time": now_iso,
                    "kind": "trophy",
                    "tier": tier,
                    "count": inc,
                    "game": cur_game,
                    "level_up": level_up,
                    "level": cur_level,
                    "big": is_big,
                    "text": txt
                })

    # high-water mark: a dip (sensor glitch) must not make the climb back look like new trophies
    hw = dict(cur_state)
    hw["counts"] = {t: max(v for v in (prev_state.get("counts", {}).get(t), cur_counts[t]) if v is not None) for t in TIERS}
    hw["level"] = max(prev_state.get("level", 0), cur_level)
    _atomic_write(state_file, hw)

    events = feed_data.get("events", []) + new_events
    events = events[-50:]

    feed_data["source"] = NAME
    feed_data["player"] = pkey
    feed_data["updated"] = now_iso
    feed_data["current"] = cur_state
    feed_data["events"] = events

    _atomic_write(feed_file, feed_data)


