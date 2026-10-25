"""House commands channel: status, lights, temperatures and (optionally) bins, free power and who-is-home answers.
Trigger: new message in the house channel (setting channel_house, or the JSON file channel_house_file), polled 4 s.
`whoami` works for anyone; everything else only for allowed users. Bot messages ignored; texts starting "!" get no 'don't know' reply.
HA: GET /states (lock, light, climate and the entities named in the settings house_entities / status_device), GET history of the
presence switches in the setting presence (read only; they are never changed), fires the event command_event {text, author},
light.turn_off for 'lights off' only. Nothing here touches doors or the alarm. Entities that are not set are simply not offered."""
import json, os, re, sys, time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from features import shopping as S
import settings as ST

CATCHUP_RE = re.compile(r"catch-?up (on|off|status)")
_OUT = ("go out", "went out", "going out", "gone out", "leave", "left", "head out", "headed out", "depart", "go off", "went off")
_IN = ("get home", "got home", "come home", "came home", "arrive", "arrived", "get back", "got back", "come back",
       "came back", "return", "get in", "got in", "back home", "home")
MIN_AWAY = timedelta(minutes=10)


def parse_dt(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


class View:
    """Read-only snapshot of HA states: dict entity_id -> {state, attributes, last_changed}."""
    def __init__(self, states):
        self.d = {s['entity_id']: s for s in states}
    def st(self, e, default='?'):
        s = self.d.get(e); return s['state'] if s else default
    def attr(self, e, k):
        s = self.d.get(e); return (s.get('attributes') or {}).get(k) if s else None
    def domain(self, dom):
        return [s for e, s in self.d.items() if e.startswith(dom + '.')]
    def fname(self, s):
        return (s.get('attributes') or {}).get('friendly_name') or s['entity_id']


def help_text(cfg):
    """The help text lists only what this setup offers."""
    h = cfg['house_entities']
    cmds = ["**status** (doors" + (", alarm" if h.get('alarm') else "") + (", heating" if h.get('boiler') or h.get('thermostat') else "")
            + (", " + cfg['status_device']['label'] if cfg['status_device'].get('label') else "") + ")",
            "**lights** (which lights are on)", "**lights off** (all lights off)"]
    if h.get('bin_next'):
        cmds.append("**bins**")
    if h.get('free_power_events'):
        cmds.append("**free** (free electricity)")
    cmds.append("**temps** (room temperatures)")
    cmds += ["**shop** (the shopping list, grouped by aisle)", "**got milk, bread** (tick things off)"]
    if cfg['catchup_cmd']:
        cmds.append("**catchup on / off / status** (%s)" % cfg['catchup_label'])
    cmds.append("**help**")
    out = "Commands: " + ", ".join(cmds) + "."
    names = [n.capitalize() for n in cfg['presence']]
    if names:
        out += " You can also ask things like \"when did %s go out\" or \"is %s home\"." % (names[0], names[-1])
    return out + " Doors and the alarm cannot be changed from here."


def aliases(cfg, cmd):
    """Extra single words that count as the command `cmd` (setting aliases, regex alternatives; only plain words are exact-matched)."""
    return tuple(w for w in re.split(r"\|", cfg['aliases'].get(cmd, '')) if re.fullmatch(r"[a-z0-9 '-]+", w))


def answer(v, text, now=None, cfg=None):
    """Returns (reply or None, action or None)."""
    cfg = cfg or ST.merge({})
    h, dev = cfg['house_entities'], cfg['status_device']
    now = now or datetime.now(timezone.utc)
    t = text.strip().lower().rstrip("?!. ")
    if t in ("help", "hi", "hello", "commands"):
        return help_text(cfg), None
    if t == "status" or t in aliases(cfg, 'status'):
        unl = [v.fname(s) for s in v.domain('lock') if s['state'] != 'locked']
        lines = []
        if h.get('alarm'):
            lines.append(f"Alarm: {v.st(h['alarm'])}")
        lines.append("Doors: all locked" if not unl else "Doors unlocked: " + ", ".join(unl))
        if h.get('boiler') or h.get('thermostat'):
            t_now = v.attr(h['thermostat'], 'current_temperature') if h.get('thermostat') and h['thermostat'] in v.d else '?'
            lines.append("Heating: " + (f"boiler {'on' if v.st(h['boiler']) == 'on' else 'off'}" if h.get('boiler') else "") + (", " if h.get('boiler') and h.get('thermostat') else "") + (f"hall {t_now}°" if h.get('thermostat') else ""))
        if dev.get('battery'):
            try:
                kmin = int((now - parse_dt(v.st(dev['seen']))).total_seconds() // 60) if dev.get('seen') else None
            except Exception:
                kmin = None
            lines.append(f"{dev.get('label', 'Device')}: {v.st(dev['battery'])}%" + (f", last check-in {kmin} min ago" if kmin is not None else ""))
        return "\n".join(lines), None
    if t == "lights":
        on = sorted(v.fname(s) for s in v.domain('light') if s['state'] == 'on')
        return (("Lights on: " + ", ".join(on)) if on else "No lights are on."), None
    if t in ("lights off", "all lights off"):
        on = [s['entity_id'] for s in v.domain('light') if s['state'] == 'on']
        if not on:
            return "No lights were on.", None
        return f"Turning off {len(on)} light(s).", ("light", "turn_off", {"entity_id": on})
    if h.get('bin_next') and t in ("bins", "bin") + aliases(cfg, 'bins'):
        return f"Next bins: {v.st(h['bin_next'])} in {v.st(h['bin_days']) if h.get('bin_days') else '?'} day(s).", None
    if h.get('free_power_events') and t in ("free", "free electricity") + aliases(cfg, 'free'):
        evs = v.attr(h['free_power_events'], 'events') or []
        banked = v.st(h['free_power_banked']) if h.get('free_power_banked') else '?'
        fut = [e for e in evs if parse_dt(e['end']) > now]
        if not fut:
            return f"No free hours booked. Hours banked: {banked}.", None
        txt = ", ".join(parse_dt(e['start']).astimezone(LOCAL_TZ).strftime("%a %H:%M") + "-" + parse_dt(e['end']).astimezone(LOCAL_TZ).strftime("%H:%M") for e in fut)
        return f"Free electricity booked: {txt}. Hours banked: {banked}.", None
    if t in ("temps", "temp", "temperatures"):
        rows = []
        for s in v.domain('climate'):
            ct = (s.get('attributes') or {}).get('current_temperature')
            if ct is not None and s['state'] != 'unavailable':
                rows.append(f"{v.fname(s)}: {ct}°")
        return "\n".join(sorted(rows)) or "No readings.", None
    return None, None


LOCAL_TZ = timezone.utc   # replaced from HA's time_zone at start (set_tz)


def set_tz(name):
    global LOCAL_TZ
    try:
        from zoneinfo import ZoneInfo
        LOCAL_TZ = ZoneInfo(name)
    except Exception:
        pass


def fuzzy(text, cfg=None):
    t = text.strip().lower().rstrip("?!. ")
    ali = (cfg or {}).get('aliases') or {}
    has = lambda pat, cmd=None: re.search(pat + (r"|\b(" + ali[cmd] + r")\b" if cmd and ali.get(cmd) else ""), t) is not None
    if has(r"\b(off|out|switch|turn|kill|shut)\b"):
        return None
    if has(r"\blights?\b"): return "lights"
    if has(r"\bbins?\b|\brubbish\b|\brecycling\b|\bcollection\b", 'bins'): return "bins"
    if has(r"\b(free|cheap|cheapest)\b|\belectricity\b", 'free'): return "free"
    if has(r"\btemp\w*|\bwarm\b|\bcold\b|\bhot\b|\bdegrees\b"): return "temps"
    if has(r"\b(door|doors|lock|locked|unlocked|alarm|heating|house)\b", 'status'): return "status"
    return None


def when(ts, now):
    d = ts.astimezone(LOCAL_TZ); today = now.astimezone(LOCAL_TZ).date()
    if d.date() == today: day = "today"
    elif d.date() == today - timedelta(days=1): day = "yesterday"
    else: day = d.strftime("%a %-d %b")
    return f"{d.strftime('%H:%M')} {day}"


def presence_periods(states, now):
    """states: [{state, last_changed}] -> [[start, is_home]] with away blips under 10 min merged into home."""
    raw = []
    for s in states:
        if s['state'] not in ('on', 'off'):
            continue
        home = s['state'] == 'on'
        if not raw or raw[-1][1] != home:
            raw.append([parse_dt(s['last_changed']), home])
    out = []
    for i, (ts, home) in enumerate(raw):
        nxt = raw[i + 1][0] if i + 1 < len(raw) else now
        if not home and nxt - ts < MIN_AWAY and 0 < i:
            continue
        if out and out[-1][1] == home:
            continue
        out.append([ts, home])
    return out


def presence_answer(text, cur, history, now, people=()):
    """cur: current state dict of the person's presence switch (or None); history: list of state dicts (7 days); people: the names asked about.
    None if not a presence question."""
    t = text.strip().lower().rstrip("?!. ")
    m = re.search(r"\b(" + "|".join(re.escape(p) for p in people) + r")\b", t) if people else None
    if not m or cur is None:
        return None
    name = m.group(1).capitalize()
    want_out = any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in _OUT if w != "home")
    want_in = (not want_out) and any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in _IN)
    asks = bool(re.search(r"\b(is|where|home now|in now|out now|here)\b", t))
    if not (want_out or want_in or asks):
        return None
    periods = presence_periods(history, now)
    now_home = cur['state'] == 'on'
    ends = [ts for ts, home in periods if not home]
    starts = [ts for i, (ts, home) in enumerate(periods) if home and i > 0]
    note = "\n(From phone presence, so times can be a few minutes out.)"
    if want_out:
        if not ends:
            return f"No record of {name} going out in the last 7 days." + note
        extra = "" if not now_home else f" and has been back since {when(starts[-1], now)}" if starts else ""
        return f"{name} last went out at {when(ends[-1], now)}{extra}." + note
    if want_in:
        if not starts:
            return f"No record of {name} arriving home in the last 7 days." + note
        extra = "" if now_home else f" and left again at {when(ends[-1], now)}" if ends and ends[-1] > starts[-1] else ""
        return f"{name} last got home at {when(starts[-1], now)}{extra}." + note
    since = periods[-1][0] if periods else parse_dt(cur['last_changed'])
    return f"{name} is {'home' if now_home else 'out'}, since {when(since, now)}." + note


class Feature:
    interval = 4

    def __init__(self, ctx, root=None):
        self.c = ctx
        self.cfg = ST.of(ctx)
        self.root = self.cfg['root'] if root is None else root
        self.chan, self.last = None, None
        self.tz_done = False
        self.people = tuple(n.lower() for n in self.cfg['presence'])
        self.shop = S.Feature(ctx, self.root)   # 'shop' / 'got' typed in the house channel use the same logic as the shopping channel

    def channel_now(self):
        p = ST.path_of(self.root, self.cfg['channel_house_file'])
        try:
            return str(json.load(open(p)).get('house') or self.cfg['channel_house']) if p else self.cfg['channel_house']
        except (OSError, ValueError):
            return self.cfg['channel_house']

    def catchup(self, sub, who='Discord'):
        fill = lambda a: a.format(sub=sub, root=self.root, who=who)
        args = [fill(a) for a in self.cfg['catchup_cmd']] + ([fill(a) for a in self.cfg['catchup_on_args']] if sub == 'on' else [])
        try:
            out = self.run_cmd(args)
        except Exception as e:
            return f"Could not run catch-up ({str(e)[:60]})."
        return out.strip() or "Done."

    def run_cmd(self, args):
        import subprocess
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout

    def history(self, ent, now):
        return self.c.ha.history(ent, now - timedelta(days=7))

    def reply_for(self, text, now):
        """Returns (reply, action). Shop answers are text-only here: the full list grouped by aisle (as the old _shop_answer)."""
        tl = text.strip().lower().rstrip("?!. ")
        cm = CATCHUP_RE.fullmatch(tl) if self.cfg['catchup_cmd'] else None
        if cm:   # optional command switch (setting catchup_cmd)
            return self.catchup(cm.group(1)), None
        if self.shop.shop_re.fullmatch(tl):
            self.shop.add_meal_plan()
            items = [lbl for lbl, _ in S.merge_batteries(self.shop.items())]
            if not items:
                return "The shopping list is empty.", None
            groups = {}
            for i in items: groups.setdefault(S.aisle(i, self.shop.aisles), []).append(i)
            out = [f"**Shopping list** ({len(items)})"]
            order, rank = self.shop.learned()
            for g in order:
                if g in groups:
                    out.append(f"__{g}__: " + ", ".join(S.sort_aisle(groups[g], rank)))
            return "\n".join(out), None
        gm = S.GOT_RE.fullmatch(text.strip())
        if gm:
            items = self.shop.items()
            done, unsure, missing = S.match_got(items, gm.group(2))
            for i in done:
                self.c.ha.call('todo', 'update_item', {'entity_id': self.shop.list, 'item': i, 'status': 'completed'})
            return S.got_reply(items, done, unsure, missing), None
        v = View(self.c.ha.states())
        reply, action = answer(v, text, now, self.cfg)
        if reply is None:
            g = fuzzy(text, self.cfg)
            if g:
                reply, action = answer(v, g, now, self.cfg)
        if reply is None and self.people:
            try:
                who = re.search(r"\b(" + "|".join(re.escape(p) for p in self.people) + r")\b", tl)
                if who:
                    ent = {k.lower(): e for k, e in self.cfg['presence'].items()}[who.group(1)]
                    reply = presence_answer(text, v.d.get(ent), self.history(ent, now), now, self.people)
            except Exception as e:
                print('presence answer failed:', e)
                reply = "Sorry, I couldn't look that up."
        return reply, action

    def handle_message(self, m):
        d = self.c.discord
        a = m.get('author', {})
        if a.get('bot') or a.get('id') == d.me().get('id'):
            return
        text = (m.get('content') or '').strip()
        if not text:
            return
        chan = self.chan
        if text.lower() == 'whoami':
            ok = a.get('id') in self.c.allowed()
            d.post(chan, f"Your Discord user id is {a.get('id')}" + (" (allowed)." if ok else " (not allowed yet: ask the owner to add it)."))
            return
        if a.get('id') not in self.c.allowed():
            return
        self.c.ha.fire(self.cfg['command_event'], {'text': text, 'author': a.get('username')})
        reply, action = self.reply_for(text, datetime.now(timezone.utc))
        if action:
            self.c.ha.call(*action)
        if reply:
            d.post(chan, reply)
        elif not text.startswith('!'):
            d.post(chan, "Sorry, I don't know that one. Type **help**.")

    def tick(self):
        if not self.tz_done:
            try:
                set_tz(self.c.ha.config().get('time_zone'))
            except Exception:
                pass
            self.tz_done = True
        new = self.channel_now()
        if new != self.chan:
            self.chan, self.last = new, None
        if not self.chan:
            time.sleep(60); return   # no house channel set: nothing to do
        st, msgs = self.c.discord.req('GET', f"/channels/{self.chan}/messages?limit=20" + (f"&after={self.last}" if self.last else ''))
        if st != 200:
            time.sleep(60); return
        if self.last is None:   # first run: start after the newest message, never answer old ones
            self.last = msgs[0]['id'] if msgs else '0'
            return
        for m in sorted(msgs, key=lambda x: int(x['id'])):
            self.last = m['id']
            self.handle_message(m)
