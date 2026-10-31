"""Per-player game library in /data/library.json, built from the provider feeds, plus manual add/hide.
Entry: id, title, platform, first_seen, last_seen, playtime_min (or None), source, tools[], hidden, manual, todo.
Generic code: player keys, display names and titles all come from options or the user."""
import datetime, json, os, re, tempfile, threading

PLATFORM_BY_FEED = {"psn_trophies": "psn", "xbox_gamerscore": "xbox", "steam_presence": "steam", "destiny": None}
DESTINY_PLATFORM = {1: "xbox", 2: "psn", 3: "steam", 5: "stadia"}
FEED_SOURCE = {"psn_trophies": "psn", "xbox_gamerscore": "xbox", "steam_presence": "steam", "destiny": "bungie"}
_LOCK = threading.Lock()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-") or "game"


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def make_id(title, platform=None):
    return slug(title) + ("-" + slug(platform) if platform else "")


def _atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), text=True)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _iso(now):
    return now.astimezone().isoformat(timespec="seconds")


class Library:
    def __init__(self, data_dir, tools=(), players=None, now=None):
        self.dir, self.tools, self.players = data_dir, list(tools), players or []
        self.path = os.path.join(data_dir, "library.json")
        self._now = now or (lambda: datetime.datetime.now(datetime.timezone.utc))

    # storage
    def _load(self):
        try:
            with open(self.path) as fh:
                d = json.load(fh)
            return d if isinstance(d.get("entries"), dict) else {"entries": {}}
        except (OSError, ValueError, AttributeError):
            return {"entries": {}}

    def resolve_player(self, who):
        w = norm(who)
        for p in self.players:
            if w and w in (norm(p.get("key")), norm(p.get("display_name"))):
                return p["key"]
        return None

    def _tools_for(self, entry):
        return [t.NAME for t in self.tools if t.matches(entry)]

    # reading
    def list(self, player, include_hidden=False):
        with _LOCK:
            ents = self._load()["entries"].get(player, {})
        out = [e for e in ents.values() if include_hidden or not e.get("hidden")]
        return sorted(out, key=lambda e: e.get("last_seen") or "", reverse=True)

    def get(self, player, gid):
        with _LOCK:
            return self._load()["entries"].get(player, {}).get(gid)

    def find(self, player, ref):
        """Entry by id, or by title (case-insensitive), or None."""
        e = self.get(player, ref)
        if e:
            return e
        n = norm(ref)
        for e in self.list(player, True):
            if norm(e["title"]) == n or slug(e["title"]) == slug(ref):
                return e
        return None

    # writing
    def _touch(self, d, player, title, platform, source, seen, playtime=None, manual=False):
        ents = d["entries"].setdefault(player, {})
        # one entry per title when a platform is unknown
        existing = None
        for e in ents.values():
            if norm(e["title"]) == norm(title) and (e.get("platform") == platform or not platform or not e.get("platform")):
                existing = e
                break
        if existing is None:
            existing = {"id": make_id(title, platform), "title": title, "platform": platform, "first_seen": seen,
                        "last_seen": seen, "playtime_min": None, "source": source, "tools": [], "hidden": False,
                        "manual": bool(manual), "todo": False}
            ents[existing["id"]] = existing
        else:
            if platform and not existing.get("platform"):
                existing["platform"] = platform
            if seen and (not existing.get("last_seen") or seen > existing["last_seen"]):
                existing["last_seen"] = seen
            if seen and (not existing.get("first_seen") or seen < existing["first_seen"]):
                existing["first_seen"] = seen
            if existing.get("source") == "manual" and source != "manual":
                existing["source"] = source
        if playtime is not None:
            existing["playtime_min"] = int(playtime)
        existing["tools"] = self._tools_for(existing)
        return existing

    def add(self, player, title, platform=None, tools=None):
        title = str(title or "").strip()
        if not title:
            raise ValueError("title required")
        with _LOCK:
            d = self._load()
            e = self._touch(d, player, title, (platform or "").strip().lower() or None, "manual", _iso(self._now()), manual=True)
            e["manual"], e["hidden"] = True, False
            if tools:
                e["tools"] = sorted(set(e["tools"]) | {str(t) for t in tools})
            _atomic(self.path, d)
            return dict(e)

    def hide(self, player, ref, hidden=True):
        with _LOCK:
            d = self._load()
            ents = d["entries"].get(player, {})
            e = ents.get(ref)
            if e is None:
                n = norm(ref)
                e = next((x for x in ents.values() if norm(x["title"]) == n), None)
            if e is None:
                return None
            e["hidden"] = bool(hidden)
            _atomic(self.path, d)
            return dict(e)

    def mark_todo(self, player, gid):
        with _LOCK:
            d = self._load()
            e = d["entries"].get(player, {}).get(gid)
            if e:
                e["todo"] = True
                _atomic(self.path, d)

    def refresh(self):
        """Rebuild from the feeds in /data/feeds. Adds and updates, never removes or unhides."""
        now = self._now()
        fdir = os.path.join(self.dir, "feeds")
        try:
            names = sorted(os.listdir(fdir))
        except OSError:
            names = []
        with _LOCK:
            d = self._load()
            for p in self.players:
                pk = p["key"]
                for fn in names:
                    for src, label in FEED_SOURCE.items():
                        if fn == f"{src}_{pk}.json":
                            self._from_feed(d, pk, src, os.path.join(fdir, fn), now)
            _atomic(self.path, d)

    def _from_feed(self, d, pk, src, path, now):
        try:
            with open(path) as fh:
                f = json.load(fh)
        except (OSError, ValueError):
            return
        cur = f.get("current") or {}
        upd = f.get("updated") or _iso(now)
        platform = PLATFORM_BY_FEED.get(src)
        if src == "destiny":
            platform = None
            for p in self.players:
                if p["key"] == pk:
                    platform = DESTINY_PLATFORM.get(p.get("destiny_type"))
            mins = sum(int(c.get("hours") or 0) for c in cur.get("characters", [])) * 60 or None
            seen = max([c.get("last") for c in cur.get("characters", []) if c.get("last")] or [upd])
            self._touch(d, pk, "Destiny 2", platform, FEED_SOURCE[src], seen, mins)
            return
        titles = {}
        for ev in f.get("events", []):
            g = ev.get("game")
            if g:
                t = ev.get("time") or upd
                titles[g] = max(titles.get(g, ""), t)
        if cur.get("now_playing"):
            titles[cur["now_playing"]] = upd
        for g, t in titles.items():
            self._touch(d, pk, g, platform, FEED_SOURCE[src], t)

    # to-do rule (owner request, 31 Oct 2026)
    def games_without_todo(self):
        """Entries (any player) that have no tool and no to-do yet. One row per title with all its players."""
        with _LOCK:
            d = self._load()
        rows = {}
        for pk, ents in d["entries"].items():
            for e in ents.values():
                if e.get("hidden") or e.get("tools") or e.get("todo"):
                    continue
                r = rows.setdefault(norm(e["title"]), {"title": e["title"], "id": e["id"], "players": [], "ids": {}})
                r["players"].append(pk)
                r["ids"][pk] = e["id"]
        return sorted(rows.values(), key=lambda r: r["title"].lower())
