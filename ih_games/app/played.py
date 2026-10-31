"""Played summary per game from the provider feeds (no new data source): events, last activity, achievements, sessions."""
import json, os
import library as L

FEEDS = ("psn_trophies", "xbox_gamerscore", "steam_presence", "destiny")


def played(data_dir, player, entry):
    n, last, kinds, count = 0, "", {}, 0
    for src in FEEDS:
        try:
            f = json.load(open(os.path.join(data_dir, "feeds", f"{src}_{player}.json")))
        except (OSError, ValueError):
            continue
        for ev in f.get("events", []):
            if L.norm(ev.get("game")) != L.norm(entry["title"]):
                continue
            n += 1
            kinds[ev.get("kind") or "event"] = kinds.get(ev.get("kind") or "event", 0) + 1
            count += int(ev.get("count") or 0) if ev.get("kind") == "achievement" else 0
            last = max(last, ev.get("time") or "")
    return {"events": n, "kinds": kinds, "last_event": last or None, "last_seen": entry.get("last_seen"),
            "playtime_min": entry.get("playtime_min"), "platform": entry.get("platform")}


def text(entry, p):
    bits = [entry["title"] + (" on " + entry["platform"] if entry.get("platform") else "")]
    if p["playtime_min"]:
        bits.append("about %d h played" % (p["playtime_min"] // 60))
    if p["events"]:
        bits.append("%d recent update(s): %s" % (p["events"], ", ".join("%d %s" % (v, k) for k, v in sorted(p["kinds"].items()))))
    else:
        bits.append("no recent updates recorded")
    if p["last_seen"]:
        bits.append("last seen " + p["last_seen"][:10])
    return ". ".join(bits) + "."
