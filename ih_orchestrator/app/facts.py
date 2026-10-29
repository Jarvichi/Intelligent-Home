"""Shared facts store (v0.9.0): add-ons share small internal facts (for example today's meal eat time) through the orchestrator instead of
Home Assistant entities. A fact is /facts/<module>/<name> = a JSON value (<= 4 KiB, at most 500 in total) with set_at, expires_at and the writer.
Option fact_clients: [{module, key, write}]. A bearer key identifies the calling module. Any known client may read; only module X (with write true)
writes or deletes /facts/X/*. Stored in /data/facts.json (atomic write). Values are never logged: log lines name module/name only. stdlib only."""
import datetime, hmac, json, os, re, threading, time

NAME_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
VALUE_MAX = 4096        # bytes of the JSON-encoded value
MAX_FACTS = 500
TTL_MAX_S = 366 * 86400


def iso(t):
    return None if t is None else datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_clients(o):
    """-> list of {module, key, write}. Bad entries (bad module name, empty key, duplicate module) are dropped."""
    out, seen = [], set()
    for c in (o.get("fact_clients") or []) if isinstance(o, dict) else []:
        if not isinstance(c, dict):
            continue
        m, k = c.get("module"), c.get("key")
        if not (isinstance(m, str) and NAME_RE.match(m) and isinstance(k, str) and k.strip()) or m in seen:
            continue
        seen.add(m)
        out.append({"module": m, "key": k.strip(), "write": c.get("write") is True})
    return out


def _expiry(body, now):
    """-> (expires_at epoch or None, error text or None)."""
    ttl, exp = body.get("ttl_s"), body.get("expires_at")
    if ttl is not None and exp is not None:
        return None, "give ttl_s or expires_at, not both"
    if ttl is not None:
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= TTL_MAX_S:
            return None, "ttl_s must be a whole number of seconds, 1 to %d" % TTL_MAX_S
        return now + ttl, None
    if exp is not None:
        try:
            d = datetime.datetime.fromisoformat(str(exp).strip().replace("Z", "+00:00"))
        except ValueError:
            return None, "expires_at must be an ISO date-time"
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)   # no zone given: UTC
        t = d.timestamp()
        if t <= now:
            return None, "expires_at is in the past"
        return t, None
    return None, None


class Facts:
    def __init__(self, data_dir, clients=(), log=print, clock=time.time):
        self.path = os.path.join(data_dir, "facts.json")
        self.clients, self.log, self.clock = list(clients), log, clock
        self.lock = threading.Lock()
        self.items = {}   # "module/name" -> {value, set_at, expires_at, writer}
        self.load()

    @property
    def on(self):
        return bool(self.clients)

    def load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            if not isinstance(d, dict):
                raise ValueError("not an object")
            self.items = {k: v for k, v in d.items() if isinstance(v, dict) and "value" in v and isinstance(v.get("set_at"), (int, float))}
        except FileNotFoundError:
            self.items = {}
        except (OSError, ValueError):
            self.items = {}
            self.log("facts: facts.json unreadable, starting empty")

    def _prune(self, now):
        dead = [k for k, v in self.items.items() if v.get("expires_at") is not None and v["expires_at"] <= now]
        for k in dead:
            del self.items[k]
        return bool(dead)

    def _save(self, now):
        self._prune(now)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.items, f)
            os.replace(tmp, self.path)
        except OSError:
            self.log("facts: could not save facts.json")

    def auth(self, header):
        """The calling client dict for an Authorization header, or None. Compares against every key (constant time each)."""
        h = header or ""
        if not h.startswith("Bearer "):
            return None
        tok, hit = h[7:].strip().encode(), None
        for c in self.clients:
            if hmac.compare_digest(tok, c["key"].encode()):
                hit = c
        return hit

    @staticmethod
    def _view(v):
        return {"value": v["value"], "set_at": iso(v["set_at"]), "expires_at": iso(v.get("expires_at"))}

    def put(self, caller, module, name, body):
        if not (NAME_RE.match(module) and NAME_RE.match(name)):
            return 400, {"error": "bad module or name"}
        if caller["module"] != module or not caller["write"]:
            return 403, {"error": "not allowed to write here"}
        if not isinstance(body, dict) or "value" not in body:
            return 400, {"error": "need {value, ttl_s? | expires_at?}"}
        try:
            n = len(json.dumps(body["value"], separators=(",", ":"), ensure_ascii=False).encode())
        except (TypeError, ValueError):
            return 400, {"error": "value is not JSON"}
        if n > VALUE_MAX:
            return 413, {"error": "value over %d bytes" % VALUE_MAX}
        now = self.clock()
        exp, err = _expiry(body, now)
        if err:
            return 400, {"error": err}
        key = module + "/" + name
        with self.lock:
            self._prune(now)
            if key not in self.items and len(self.items) >= MAX_FACTS:
                return 429, {"error": "store full (%d facts)" % MAX_FACTS}
            self.items[key] = {"value": body["value"], "set_at": now, "expires_at": exp, "writer": caller["module"]}
            self._save(now)
        self.log("facts: put %s" % key)
        return 200, {"ok": True, "set_at": iso(now), "expires_at": iso(exp)}

    def get(self, module, name):
        if not (NAME_RE.match(module) and NAME_RE.match(name)):
            return 404, {"error": "not found"}
        now = self.clock()
        with self.lock:
            changed = self._prune(now)
            v = self.items.get(module + "/" + name)
            if changed:
                self._save(now)
        return (200, self._view(v)) if v else (404, {"error": "not found"})

    def list(self, module=None):
        now = self.clock()
        with self.lock:
            if self._prune(now):
                self._save(now)
            rows = [dict(self._view(v), module=k.split("/", 1)[0], name=k.split("/", 1)[1], writer=v.get("writer")) for k, v in sorted(self.items.items())
                    if module is None or k.startswith(module + "/")]
        return 200, {"facts": rows}

    def delete(self, caller, module, name):
        if not (NAME_RE.match(module) and NAME_RE.match(name)):
            return 400, {"error": "bad module or name"}
        if caller["module"] != module or not caller["write"]:
            return 403, {"error": "not allowed to delete here"}
        now = self.clock()
        with self.lock:
            gone = self.items.pop(module + "/" + name, None)
            self._save(now)
        if gone:
            self.log("facts: delete %s/%s" % (module, name))
        return (200, {"ok": True}) if gone else (404, {"error": "not found"})

    def status(self):
        """For the status page: names and times only, never values. None when the feature is off."""
        if not self.on:
            return None
        now = self.clock()
        with self.lock:
            self._prune(now)
            rows = [{"fact": k, "set_at": iso(v["set_at"]), "expires_at": iso(v.get("expires_at"))} for k, v in sorted(self.items.items())]
        return {"count": len(rows), "clients": len(self.clients), "facts": rows}


def build(o, data_dir, log=print):
    return Facts(data_dir, parse_clients(o), log)


def facts_html(st, esc):
    if not st:
        return ""
    rows = "".join("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (esc(r["fact"]), esc(r["set_at"] or "-"), esc(r["expires_at"] or "never")) for r in st["facts"])
    return "<h3>Facts: %d</h3>%s" % (st["count"], "<table><tr><th>Fact<th>Set<th>Expires</tr>%s</table>" % rows if rows else "<p>None stored.</p>")
