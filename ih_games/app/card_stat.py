"""The one stat box on a game card, next to "Last played". First match wins:
1. quests   - a game tool reports progress through its optional progress(tc, player) -> {"done", "total"[, "label"]}
2. trophies - the trophy-titles feed has earned / defined totals for the game
3. playtime - a play time above zero is known (reported by the platform, or tracked from PSN play sessions)
Otherwise None: the card hides the box, so it never shows a misleading "0 min"."""
import library as L
import trophies

KIND_QUESTS, KIND_TROPHIES, KIND_PLAYTIME = "quests", "trophies", "playtime"
DEFAULT_QUEST_LABEL = "Quests"
TROPHY_PLATFORMS = (L.PSN_PLATFORM, None)   # the trophy totals are PSN data: not shown on another platform's entry


def _count(value):
    """A whole number >= 0, or None."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def tool_progress(tool, tc, player):
    """{"kind": "quests", done, total, label} from a tool's optional progress(), or None. A failing tool gives None."""
    fn = getattr(tool, "progress", None)
    if not fn:
        return None
    try:
        p = fn(tc, player)
    except Exception:
        return None
    if not isinstance(p, dict):
        return None
    done, total = _count(p.get("done")), _count(p.get("total"))
    if done is None or not total:
        return None
    return {"kind": KIND_QUESTS, "done": min(done, total), "total": total, "label": str(p.get("label") or DEFAULT_QUEST_LABEL)}


def for_entry(entry, trophy_totals, quest=None):
    """The stat for one library entry. trophy_totals: trophies.totals_index(); quest: tool_progress() result or None."""
    if quest:
        return quest
    if entry.get("platform") in TROPHY_PLATFORMS:
        t = trophy_totals.get(L.norm(entry.get("title")))
        if t and t.get("total"):
            return {"kind": KIND_TROPHIES, "earned": t["earned"], "total": t["total"]}
    minutes = _count(entry.get("playtime_min"))
    if minutes:
        tracked = entry.get("playtime_src") == L.PLAYTIME_SRC_TRACKED
        return {"kind": KIND_PLAYTIME, "minutes": minutes, "tracked": tracked,
                "since": entry.get("playtime_since") if tracked else None}
    return None


def annotate(games, data_dir, player, tool_for=None, tc=None):
    """Copies of the library entries with `card_stat` added."""
    totals = trophies.totals_index(data_dir, player)
    out = []
    for e in games:
        tool = tool_for(e) if tool_for and e.get("tools") else None
        out.append(dict(e, card_stat=for_entry(e, totals, tool_progress(tool, tc, player) if tool else None)))
    return out
