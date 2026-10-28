"""Settings hub + provisioner (v0.5.0). Fills EMPTY options of the other ih_* add-ons of the same repository
from one store (/data/hub.json) and the `shared` option. stdlib only. Values are NEVER logged or returned:
logs and status hold option NAMES, add-on suffixes and error kinds only."""
import json, os, secrets, threading, time, urllib.request, urllib.error

SUP = "http://supervisor"
STR_TYPES = ("str", "string", "password", "url", "email")
# identity-like options: always per add-on, never global (import and provision)
PER_ADDON_ONLY = frozenset(("module_name", "signing_key", "model", "agent_id", "callback_url", "channel_key",
                            "command_channel_key", "house_channel_key", "mqtt_user", "mqtt_password",
                            "api_token", "outbox_token", "parent_url"))   # own inbound tokens and per-login values never spread (28 Oct review)
WAIT_POLLS, WAIT_STEP = 30, 2   # restarted add-on must be 'started' within 60 s


class SupError(Exception):
    """A Supervisor call failed. Holds only a short kind (no response text: it could echo values)."""


def supervisor(method, path, body=None, token=None, timeout=20):
    """Real Supervisor call; returns the 'data' dict. Tests replace this with a fake."""
    tok = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(SUP + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SupError("http_%d" % e.code)
    except Exception as e:
        raise SupError(type(e).__name__)
    if isinstance(out, dict) and out.get("result") not in (None, "ok"):
        raise SupError("result_error")
    return out.get("data") if isinstance(out, dict) and "data" in out else out


def _base_type(t):
    """'password' from 'password?' or 'str(1,5)'."""
    return str(t or "").split("(")[0].rstrip("?").strip().lower()


def schema_types(schema):
    """{option: type} from the Supervisor schema (list of field dicts, or dict). Anything odd is skipped."""
    out = {}
    if isinstance(schema, dict):
        for k, v in schema.items():
            out[str(k)] = _base_type(v) if isinstance(v, str) else "complex"
    elif isinstance(schema, list):
        for f in schema:
            if isinstance(f, dict) and isinstance(f.get("name"), str):
                out[f["name"]] = _base_type(f.get("type")) if isinstance(f.get("type"), str) else "complex"
    return out


def is_text(t):
    return t in STR_TYPES


def empty(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def prefix_from(self_slug):
    """'abc_ih_orchestrator' -> 'abc'. None if the slug does not look like ours."""
    s = str(self_slug or "")
    return s[:-len("_ih_orchestrator")] if s.endswith("_ih_orchestrator") and len(s) > len("_ih_orchestrator") else None


def parse_shared(shared):
    """Option `shared` [{name, value}] -> ({opt: v}, {suffix: {opt: v}}). Blank values are ignored."""
    g, a = {}, {}
    for e in shared if isinstance(shared, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get("name"), str) or empty(e.get("value")):
            continue
        n = e["name"].strip()
        if "." in n:
            suf, opt = n.split(".", 1)
            if suf and opt:
                a.setdefault(suf, {})[opt] = e["value"]
        elif n:
            g[n] = e["value"]
    return g, a


class Provisioner:
    def __init__(self, data_dir, sup=supervisor, log=print, options=lambda: {}, sleep=time.sleep):
        self.hub_path = os.path.join(data_dir, "hub.json")
        self.sup, self.log, self.options, self.sleep = sup, log, options, sleep
        self.repo = None
        self.lock = threading.Lock()
        self.status = {"last_run": None, "ran": False, "prefix": None, "addons": {}, "errors": []}

    # ---- hub store
    def load_hub(self):
        try:
            with open(self.hub_path) as f:
                d = json.load(f)
            if isinstance(d, dict):
                return {"global": d.get("global") if isinstance(d.get("global"), dict) else {},
                        "addon": d.get("addon") if isinstance(d.get("addon"), dict) else {}}
        except Exception:
            pass
        return {"global": {}, "addon": {}}

    def save_hub(self, hub):
        os.makedirs(os.path.dirname(self.hub_path), exist_ok=True)
        tmp = self.hub_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(hub, f)
        os.replace(tmp, self.hub_path)

    def hub_exists(self):
        return os.path.exists(self.hub_path)

    def effective(self, hub):
        """hub.json overlaid by the `shared` option (shared wins). Not saved."""
        sg, sa = parse_shared(self.options().get("shared"))
        g = dict(hub["global"]); g.update(sg)
        a = {k: dict(v) for k, v in hub["addon"].items() if isinstance(v, dict)}
        for suf, d in sa.items():
            a.setdefault(suf, {}).update(d)
        return g, a

    # ---- scope
    def targets(self):
        """(prefix, [slug]) of the other ih_* add-ons in OUR repository. Raises SupError."""
        me = self.sup("GET", "/addons/self/info")
        prefix = prefix_from(me.get("slug") if isinstance(me, dict) else "")
        if not prefix:
            raise SupError("no_self_slug")
        self.repo = me.get("repository") if isinstance(me, dict) else None
        if empty(self.repo):
            raise SupError("no_self_repository")
        lst = self.sup("GET", "/addons")
        items = lst.get("addons") if isinstance(lst, dict) else lst
        slugs = []
        for x in items if isinstance(items, list) else []:
            s = x.get("slug") if isinstance(x, dict) else None
            if (isinstance(s, str) and s.startswith(prefix + "_ih_") and s != prefix + "_ih_orchestrator"
                    and x.get("repository") == self.repo):
                slugs.append(s)
        return prefix, sorted(slugs)

    @staticmethod
    def suffix(prefix, slug):
        return slug[len(prefix) + 1:]

    def info(self, slug):
        d = self.sup("GET", "/addons/%s/info" % slug)
        return d if isinstance(d, dict) else {}

    # ---- import
    def do_import(self):
        """One-off: collect str/password option values of installed ih add-ons into hub.json. Returns names only."""
        prefix, slugs = self.targets()
        found = {}  # opt -> {suffix: value}
        for slug in slugs:
            try:
                opts = self.info(slug).get("options")
            except SupError as e:
                self.log("provision import: %s unreadable (%s)" % (self.suffix(prefix, slug), e))
                continue
            for k, v in (opts if isinstance(opts, dict) else {}).items():
                if isinstance(v, str) and v.strip():
                    found.setdefault(k, {})[self.suffix(prefix, slug)] = v
        hub = self.load_hub()
        for opt, byadd in found.items():
            if len(set(byadd.values())) == 1 and opt not in PER_ADDON_ONLY:
                hub["global"].setdefault(opt, next(iter(byadd.values())))
            else:
                for suf, v in byadd.items():
                    hub["addon"].setdefault(suf, {}).setdefault(opt, v)
        self.save_hub(hub)
        self.log("provision import: %d options from %d add-ons" % (len(found), len(slugs)))
        return {"imported": sorted(found), "addons": len(slugs)}

    def ensure_register_key(self):
        hub = self.load_hub()
        if empty(hub["global"].get("register_key")):
            hub["global"]["register_key"] = secrets.token_urlsafe(32)
            self.save_hub(hub)

    # ---- provision
    def run(self, now=None):
        """One pass over the other ih add-ons. Never raises. Returns the names-only status."""
        with self.lock:
            return self._run(now)

    def _run(self, now):
        o = self.options()
        overwrite = o.get("provision_overwrite") is True
        st = {"last_run": time.time() if now is None else now, "ran": True, "prefix": None, "addons": {}, "errors": []}
        try:
            if not self.hub_exists():
                self.do_import()
            self.ensure_register_key()
            g, a = self.effective(self.load_hub())
            prefix, slugs = self.targets()
        except Exception as e:
            st["errors"].append("setup: " + (str(e) if isinstance(e, SupError) else type(e).__name__))
            self.log("provision: setup failed (%s)" % st["errors"][-1])
            self.status = st
            return self.get_status()
        self.status = st
        for slug in slugs:
            suf = self.suffix(prefix, slug)
            try:
                st["addons"][suf] = self._one(slug, suf, g, a.get(suf, {}), overwrite)
                if st["addons"][suf].get("halt"):
                    st["errors"].append("stopped: %s did not restart" % suf)
                    self.log("provision: stopped, %s did not reach started" % suf)
                    break
            except Exception as e:
                kind = str(e) if isinstance(e, SupError) else type(e).__name__
                st["addons"][suf] = {"filled": [], "missing": [], "restarted": False, "error": kind}
                self.log("provision: %s failed (%s)" % (suf, kind))
        self.status = st
        return self.get_status()

    def _one(self, slug, suf, g, a, overwrite):
        d = self.info(slug)
        if d.get("repository") != self.repo:
            return {"filled": [], "missing": [], "restarted": False, "error": "other_repository"}
        cur = dict(d.get("options") if isinstance(d.get("options"), dict) else {})
        types = schema_types(d.get("schema"))
        filled, missing = [], []
        for opt, t in types.items():
            if not is_text(t):
                continue
            want = a.get(opt, None if opt in PER_ADDON_ONLY else g.get(opt))
            have = cur.get(opt)
            if not empty(want) and (empty(have) or (overwrite and have != want)):
                cur[opt] = want
                filled.append(opt)
            elif empty(have):
                missing.append(opt)
        res = {"filled": sorted(filled), "missing": sorted(missing), "restarted": False, "error": None}
        if filled and not all(opt in types and isinstance(cur[opt], str) for opt in filled):
            self.log("provision: %s skipped (value or schema check failed)" % suf)
            res.update(filled=[], error="validation_failed")
            return res
        if filled:
            try:
                self.sup("POST", "/addons/%s/options" % slug, {"options": cur})
            except SupError as e:
                res.update(filled=[], error="options_rejected:" + str(e))
                res["missing"] = sorted(set(missing) | set(filled))
                self.log("provision: %s options not saved (%s)" % (suf, e))
                return res
            self.log("provision: %s filled %s" % (suf, ",".join(res["filled"])))
            if d.get("state") == "started":
                try:
                    self.sup("POST", "/addons/%s/restart" % slug)
                    res["restarted"] = True
                    if not self.wait_started(slug):
                        res["error"] = "not_started_after_restart"
                        res["halt"] = True
                except SupError as e:
                    res["error"] = "restart_failed:" + str(e)
                    self.log("provision: %s restart failed (%s)" % (suf, e))
        return res

    def wait_started(self, slug):
        for _ in range(WAIT_POLLS):
            try:
                if self.info(slug).get("state") == "started":
                    return True
            except SupError:
                pass
            self.sleep(WAIT_STEP)
        return False

    def get_status(self):
        s = self.status
        return {"enabled": True, "last_run": s["last_run"], "ran": s["ran"], "errors": list(s["errors"]),
                "addons": {k: dict(v) for k, v in s["addons"].items()}}


def settings_html(status, esc):
    """Status page section: per add-on, the count of missing options (names only elsewhere)."""
    if not status:
        return ""
    rows = "".join("<tr><td>%s</td><td>%d</td><td>%s</td></tr>" % (esc(k), len(v.get("missing") or []), esc(v.get("error") or ""))
                   for k, v in sorted((status.get("addons") or {}).items()))
    err = "".join("<p>%s</p>" % esc(e) for e in status.get("errors") or [])
    return "<h3>Settings</h3>%s<table><tr><th>Add-on<th>Missing options<th>Error</tr>%s</table>" % (err, rows)


def loop(prov, get_interval_min, stop=lambda: False, sleep=time.sleep):
    """Timer thread body: run now, then every interval minutes."""
    while not stop():
        prov.run()
        try:
            m = int(get_interval_min())
        except Exception:
            m = 10
        sleep(max(1, m) * 60)
