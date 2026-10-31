"""Publish the library and the per-game tool data as Home Assistant sensors (REST state publish via ctx.set_state; dry mode only logs).
  sensor.games_library_<player>      state = number of visible games; attributes games = [{title, platform, last_seen, playtime, tools}]
  sensor.games_<tool>_<player>       state = short summary (max 255 chars); attributes from the tool's optional sensor(tc, player)
Names come from options (player keys) and tool NAMEs. A failing tool never stops the others."""

MAX_STATE = 255


def _playtime(e):
    m = e.get("playtime_min")
    return None if m is None else round(m / 60, 1)


def publish(mod, ctx):
    """Returns the list of entity ids written (or that would be written in dry mode)."""
    done = []
    tc = mod.tool_ctx()
    for p in mod.opts.get("players", []):
        pk = p.get("key")
        if not pk:
            continue
        games = mod.library.list(pk)
        rows = [{"title": e["title"], "platform": e.get("platform"), "last_seen": e.get("last_seen"), "playtime": _playtime(e), "tools": e.get("tools", [])} for e in games]
        eid = "sensor.games_library_" + pk
        ctx.set_state(eid, str(len(rows)), {"friendly_name": "Games library (%s)" % (p.get("display_name") or pk), "icon": "mdi:controller", "unit_of_measurement": "games", "games": rows, "player": pk})
        done.append(eid)
        for tn in sorted({t for e in games for t in e.get("tools", [])}):
            tool = mod.tool_for(tn)
            if not tool:
                continue
            eid = "sensor.games_%s_%s" % (tn, pk)
            try:
                if hasattr(tool, "sensor"):
                    state, attrs = tool.sensor(tc, pk)
                else:
                    state, attrs = tool.summary(tc, pk), {}
            except Exception as ex:
                mod.log("sensor %s failed: %s" % (eid, type(ex).__name__))
                continue
            attrs = dict(attrs, friendly_name="%s (%s)" % (tn.replace("_", " ").title(), p.get("display_name") or pk), icon="mdi:gamepad-variant", player=pk)
            ctx.set_state(eid, str(state)[:MAX_STATE], attrs)
            done.append(eid)
    return done
