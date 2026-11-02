"""Add-on manager page (v0.10.0: trace explorer; consumer outcome progress). Ingress only: the server on IH_MANAGER_PORT answers the Supervisor ingress gateway (172.30.32.2) and nobody else.
Lists the add-ons of catalogue.json merged with Supervisor data, runs install/start/stop/restart/update in background jobs, shows needs / needed by.
Acts ONLY on catalogue slugs of our own repository (same scope rule as the provisioner). stdlib only. Never logs or returns option values."""
import hashlib, json, os, re, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
import provisioner

INGRESS_IP = "172.30.32.2"   # the Supervisor ingress gateway; host_network means the port is open to the LAN, so every other client is refused
ACTIONS = ("install", "start", "stop", "restart", "update")
EXTERNAL = {"mqtt": "core_mosquitto"}   # requires that are not ours: only checked, never acted on
LOG_LINES = 50
JOB_KEEP = 50
USAGE_EVERY = 300   # seconds between usage counts (done by the cache warmer, never on a page load)
SLOW = 900   # seconds: install and update can take minutes (local build)


def client_allowed(ip):
    return ip == INGRESS_IP


def sup_slow(method, path, body=None):
    return provisioner.supervisor(method, path, body, timeout=SLOW)


def sup_text(path, timeout=20):
    """GET a plain-text Supervisor endpoint (logs); returns text. Raises SupError."""
    tok = os.environ.get("SUPERVISOR_TOKEN", "") or provisioner._s6_token()
    req = urllib.request.Request(provisioner.SUP + path, headers={"Authorization": "Bearer " + tok, "Accept": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise provisioner.SupError("http_%d" % e.code)
    except Exception as e:
        raise provisioner.SupError(type(e).__name__)


def http_json(url, timeout=4):
    """GET a small JSON document from a sibling add-on; None when unreachable or not JSON."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read(1_000_000))
    except Exception:
        return None


def load_catalogue(path):
    """[{name, slug, description, kind, requires, version}] from catalogue.json; [] if missing or bad."""
    try:
        d = json.load(open(path))
        return [x for x in d if isinstance(x, dict) and isinstance(x.get("slug"), str)] if isinstance(d, list) else []
    except Exception:
        return []


def needs_map(cat):
    """{slug: [required slug]} and the reverse {slug: [slugs that need it]}. Unknown names are kept (e.g. mqtt)."""
    needs = {c["slug"]: [r for r in c.get("requires") or [] if isinstance(r, str)] for c in cat}
    by = {}
    for s, rs in needs.items():
        for r in rs:
            by.setdefault(r, []).append(s)
    return needs, {k: sorted(v) for k, v in by.items()}


def dep_status(req, installed):
    """'ok' | 'stopped' | 'not installed' for one required name. installed: {short: state}."""
    st = installed.get(req)
    return "not installed" if st is None else ("ok" if st == "started" else "stopped")


def install_order(short, needs, installed, seen=None):
    """Required own add-ons that are not installed, dependencies first, then short itself. External names (mqtt) skipped."""
    seen = seen if seen is not None else set()
    out = []
    for r in needs.get(short, []):
        if r in EXTERNAL or r in seen or r in installed or r not in needs:
            continue
        seen.add(r)
        out += install_order(r, needs, installed, seen)
        out.append(r)
    return out


# ---- usage counting (0.8.0). Counts and times ONLY: log text is parsed in memory and never stored, returned or logged.
# Every IH add-on logs ONE metadata line per real invocation, starting with HH:MM:SS (no date). Add a row to USAGE_FORMATS for a new
# line format: (label, regex matching the SHORT slug, regex for the line). The line regex needs the groups t (HH:MM:SS) and ok (True|False);
# an optional group id is used to dedupe. The first row whose slug regex matches is used; USAGE_GENERIC is the fallback for every other add-on.
_T = r"^(?P<t>\d\d:\d\d:\d\d) "
_OK = r".*?\bok=(?P<ok>True|False)\b"
USAGE_FORMATS = [
    ("ai_layer request line", r"^ih_ai_layer$", re.compile(_T + r"request id=(?P<id>\S*)" + _OK)),       # `12:27:07 request id=.. caller=.. cap=.. ok=True`
    ("provider ask line", r"^ih_ai_layer_", re.compile(_T + r"ask id=(?P<id>\S*)" + _OK)),                # `.. ask id=.. agent=.. ok=True`
    ("invoke line", r"^ih_(jobs|games|cooking|orchestrator|orchestration_inbox|discord)", re.compile(_T + r"invoke id=(?P<id>\S*)" + _OK)),   # `.. invoke id=run1 src=startup what=trust .. ok=True`
]
USAGE_GENERIC = re.compile(_T + r"(?:.*?\bid=(?P<id>\S*))?" + _OK)   # fallback: any timestamped line holding ok=True / ok=False
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
USAGE_DAYS = 14        # days of counts kept in /data/usage.json
USAGE_KEY_DAYS = 3     # days of dedupe keys kept
USAGE_KEY_MAX = 6000   # dedupe keys per add-on and day


def usage_regex(short):
    for _label, slug_rx, rx in USAGE_FORMATS:
        if re.search(slug_rx, short):
            return rx
    return USAGE_GENERIC


def parse_usage(short, text, now):
    """-> [(day 'YYYY-MM-DD', seconds_of_day, ok, key)] for the invocation lines of one log. Text is not kept.
    Lines carry only a time of day: walking back from the newest line, a time later than the next line's means we crossed midnight.
    Quiet spells of more than a day cannot be seen, so very old lines in a long log may be dated a day or more late (known limit)."""
    rx = usage_regex(short)
    found, seen = [], {}
    for line in text.splitlines():
        m = rx.match(_ANSI.sub("", line).strip())
        if not m:
            continue
        h, mi, se = (int(x) for x in m.group("t").split(":"))
        if h > 23 or mi > 59 or se > 59:
            continue
        gd = m.groupdict()
        base = hashlib.sha1((short + "|" + m.group("t") + "|" + (gd.get("id") or line)).encode()).hexdigest()[:10]
        seen[base] = seen.get(base, 0) + 1
        found.append((h * 3600 + mi * 60 + se, m.group("ok") == "True", "%s#%d" % (base, seen[base])))
    out = []
    if not found:
        return out
    lt = time.localtime(now)
    day = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    nowsec = lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec
    off = 1 if found[-1][0] > nowsec + 120 else 0    # newest line is later than now: it was yesterday
    prev = None
    for sec, ok, key in reversed(found):
        if prev is not None and sec > prev:
            off += 1
        prev = sec
        d = time.localtime(day - off * 86400 + 43200)
        out.append(("%04d-%02d-%02d" % (d.tm_year, d.tm_mon, d.tm_mday), sec, ok, key))
    out.reverse()
    return out


def day_str(ts):
    d = time.localtime(ts)
    return "%04d-%02d-%02d" % (d.tm_year, d.tm_mon, d.tm_mday)


class Jobs:
    """Background runner. States: queued, running, done, error. Keeps the last JOB_KEEP jobs."""
    def __init__(self):
        self.lock, self.jobs, self.n, self.events = threading.Lock(), {}, 0, {}

    def submit(self, label, fn):
        with self.lock:
            self.n += 1
            jid = "j%d" % self.n
            self.jobs[jid] = {"id": jid, "label": label, "state": "queued", "steps": [], "error": None, "started": time.time(), "finished": None}
            self.events[jid] = threading.Event()
            for old in sorted(self.jobs, key=lambda k: int(k[1:]))[:-JOB_KEEP]:
                self.jobs.pop(old, None); self.events.pop(old, None)
        threading.Thread(target=self._run, args=(jid, fn), daemon=True).start()
        return jid

    def _run(self, jid, fn):
        j = self.jobs[jid]
        j["state"] = "running"
        try:
            fn(lambda s: j["steps"].append(str(s)[:120]))
            j["state"] = "done"
        except Exception as e:
            j["state"], j["error"] = "error", str(e) if isinstance(e, provisioner.SupError) else type(e).__name__
        j["finished"] = time.time()
        self.events[jid].set()

    def get(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
            return dict(j, steps=list(j["steps"])) if j else None

    def wait(self, jid, timeout=5):
        ev = self.events.get(jid)
        return bool(ev and ev.wait(timeout))


class Manager:
    def __init__(self, catalogue, sup=sup_slow, text=sup_text, prov=lambda: None, seen=lambda: {}, log=print, ttl=60,
                 health=lambda: None, http=None, usage_path=None, clock=time.time, traces=lambda: None):
        self.catalogue, self.sup, self.text, self.prov, self.seen, self.log, self.ttl = catalogue, sup, text, prov, seen, log, ttl
        self.health_src, self.http = health, http or http_json
        self.jobs, self.busy, self.lock = Jobs(), set(), threading.Lock()
        self.cache = (0, None)
        self.usage_path, self.clock, self.ulock = usage_path or os.environ.get("IH_USAGE", "/data/usage.json"), clock, threading.Lock()
        self.usage, self.usage_t, self.usage_err = self._usage_load(), 0, {}
        self.trace_src = traces

    # ---- scope
    def context(self):
        """(prefix, repo) of this add-on. Raises SupError."""
        me = self.sup("GET", "/addons/self/info")
        prefix = provisioner.prefix_from(me.get("slug") if isinstance(me, dict) else "")
        repo = me.get("repository") if isinstance(me, dict) else None
        if not prefix or provisioner.empty(repo):
            raise provisioner.SupError("no_self_slug")
        return prefix, repo

    def scope(self, short):
        """-> (full slug, repo) or raises ValueError. Only catalogue slugs, never the orchestrator itself, and the repository must be ours."""
        names = {c["slug"] for c in self.catalogue}
        if short not in names or short == "ih_orchestrator" or not short.startswith("ih_"):
            raise ValueError("not an add-on of this repository")
        prefix, repo = self.context()
        full = prefix + "_" + short
        try:
            info = self.sup("GET", "/addons/%s/info" % full)
        except provisioner.SupError:
            info = self.sup("GET", "/store/addons/%s" % full)
        if not isinstance(info, dict) or info.get("repository") != repo:
            raise ValueError("not an add-on of this repository")
        return full, repo

    # ---- table
    def installed_map(self, prefix):
        """{short: {state, version, latest, update, repository}} for installed add-ons (+ external names like mqtt)."""
        lst = self.sup("GET", "/addons")
        items = lst.get("addons") if isinstance(lst, dict) else lst
        out = {}
        for x in items if isinstance(items, list) else []:
            if not isinstance(x, dict) or not isinstance(x.get("slug"), str):
                continue
            s = x["slug"]
            short = s[len(prefix) + 1:] if s.startswith(prefix + "_ih_") else next((k for k, v in EXTERNAL.items() if v == s), None)
            if short:
                out[short] = {"state": x.get("state"), "version": x.get("version"), "latest": x.get("version_latest"),
                              "update": bool(x.get("update_available")), "slug": s}
        return out

    def rows(self, force=False):
        t, cached = self.cache
        if cached is not None and not force and time.time() - t < self.ttl:
            return cached
        data = self._rows()
        self.cache = (time.time(), data)
        return data

    def warm(self, every=20, stop=lambda: False, sleep=time.sleep):
        """Background refresher (28 Oct): ~24 sequential stats calls take longer than the page waits,
        so a cold request left the table empty. Keeps the cache fresh; page requests then answer at once."""
        while not stop():
            try:
                self.rows(force=True)
            except Exception as e:
                self.log("manager warm: %s" % type(e).__name__)
            if self.clock() - self.usage_t >= USAGE_EVERY:
                try:
                    self.collect_usage()
                except Exception as e:
                    self.log("manager usage: %s" % type(e).__name__)
            sleep(every)

    def _rows(self):
        prefix, _ = self.context()
        inst = self.installed_map(prefix)
        states = {k: v["state"] for k, v in inst.items()}
        needs, by = needs_map(self.catalogue)
        seen = self.seen() or {}
        settings = ((self.prov().get_status() if self.prov() else None) or {}).get("addons") or {}
        out = []
        for c in self.catalogue:
            s = c["slug"]
            i = inst.get(s)
            r = {"slug": s, "name": c.get("name") or s, "description": str(c.get("description") or "")[:200], "kind": c.get("kind"),
                 "installed": bool(i), "version": i["version"] if i else None, "latest": (i or {}).get("latest") or c.get("version"),
                 "state": i["state"] if i else None, "update": bool(i and i["update"]), "boot": None, "cpu": None, "mem_mb": None,
                 "mem_pct": None, "full": (prefix + "_" + s) if i else None, "seen": seen.get(s), "missing_settings": len(settings[s].get("missing") or []) if s in settings else None,
                 "needs": [{"slug": n, "status": dep_status(n, states)} for n in needs.get(s, [])],
                 "needed_by": [{"slug": n, "status": dep_status(n, states)} for n in by.get(s, [])],
                 "image": bool(c.get("image")), "can_act": s != "ih_orchestrator", "used": self.used(s) if i else None}
            if i:
                self._live(prefix + "_" + s, r)
            out.append(r)
        return out

    def _live(self, full, r):
        try:
            r["boot"] = (self.sup("GET", "/addons/%s/info" % full) or {}).get("boot")
            if r["state"] == "started":
                st = self.sup("GET", "/addons/%s/stats" % full) or {}
                r["cpu"] = st.get("cpu_percent")
                r["mem_mb"] = round(float(st.get("memory_usage") or 0) / 1048576, 1)
                r["mem_pct"] = st.get("memory_percent")
        except (provisioner.SupError, ValueError, TypeError):
            pass

    # ---- actions
    def warn_missing(self, short):
        """List of required names not installed (own ones, plus external like mqtt)."""
        prefix, _ = self.context()
        inst = self.installed_map(prefix)
        needs, _by = needs_map(self.catalogue)
        return [r for r in needs.get(short, []) if r not in inst]

    def act(self, action, short, with_requires=False):
        """-> (http code, object). Starts a background job; install of an add-on with missing requires warns first."""
        if action not in ACTIONS:
            return 400, {"error": "unknown action"}
        try:
            full, _repo = self.scope(short)
        except ValueError as e:
            return 403, {"error": str(e)}
        except provisioner.SupError as e:
            return 502, {"error": str(e)}
        order = []
        if action == "install":
            try:
                missing = self.warn_missing(short)
            except provisioner.SupError as e:
                return 502, {"error": str(e)}
            needs, _ = needs_map(self.catalogue)
            own = [m for m in missing if m not in EXTERNAL]
            if missing and not with_requires:
                return 409, {"warning": "Needs first: " + ", ".join(missing), "missing": missing, "installable": own,
                             "external": [m for m in missing if m in EXTERNAL]}
            order = install_order(short, needs, {k: 1 for k in needs if k not in own})
        with self.lock:
            if short in self.busy:
                return 409, {"error": "busy: another action is running for this add-on"}
            self.busy.add(short)
        jid = self.jobs.submit("%s %s" % (action, short), lambda step: self._job(action, short, full, order, step))
        return 202, {"job": jid}

    def _job(self, action, short, full, order, step):
        try:
            prefix, _ = self.context()
            for dep in order:
                step("install " + dep)
                self.sup("POST", "/store/addons/%s_%s/install" % (prefix, dep))
            step("%s %s" % (action, short))
            path = "/store/addons/%s/install" % full if action == "install" else "/addons/%s/%s" % (full, action)
            self.sup("POST", path)
            if action == "install" or order:
                self._provision(step)
        finally:
            with self.lock:
                self.busy.discard(short)
            self.cache = (0, None)

    def _provision(self, step):
        p = self.prov()
        if not p:
            step("provisioner is off: fill the settings by hand"); return
        step("provisioner run")
        p.run()

    def logs(self, short):
        """-> (code, obj) with the last LOG_LINES lines."""
        try:
            full, _ = self.scope(short)
            text = self.text("/addons/%s/logs" % full)
        except ValueError as e:
            return 403, {"error": str(e)}
        except provisioner.SupError as e:
            return 502, {"error": str(e)}
        return 200, {"slug": short, "lines": text.splitlines()[-LOG_LINES:]}

    # ---- usage (counts only; see parse_usage)
    def _usage_load(self):
        try:
            d = json.load(open(self.usage_path))
            if isinstance(d, dict) and isinstance(d.get("days"), dict):
                return d
        except Exception:
            pass
        return {"v": 1, "days": {}}

    def _usage_save(self):
        try:
            tmp = self.usage_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.usage, f, separators=(",", ":"))
            os.replace(tmp, self.usage_path)
        except Exception as e:
            self.log("manager usage save: %s" % type(e).__name__)

    def collect_usage(self):
        """Read each installed catalogue add-on's log tail once, add NEW invocation lines to the day buckets, save. Returns lines added."""
        prefix, _ = self.context()
        inst = self.installed_map(prefix)
        now, added, err = self.clock(), 0, {}
        with self.ulock:
            days = self.usage["days"]
            for c in self.catalogue:
                s = c["slug"]
                if s not in inst or not s.startswith("ih_"):
                    continue
                try:
                    text = self.text("/addons/%s/logs" % inst[s]["slug"])
                except provisioner.SupError as e:
                    err[s] = str(e); continue
                known = set()
                for dd in days.values():
                    known.update((dd.get(s) or {}).get("k") or [])
                for day, sec, ok, key in parse_usage(s, text, now):
                    if key in known:
                        continue
                    known.add(key)
                    b = days.setdefault(day, {}).setdefault(s, {"ok": 0, "fail": 0, "last": -1, "h": [0] * 24, "hf": [0] * 24, "k": []})
                    b["ok" if ok else "fail"] += 1
                    b["h" if ok else "hf"][sec // 3600] += 1
                    b["last"] = max(b["last"], sec)
                    if len(b["k"]) < USAGE_KEY_MAX:
                        b["k"].append(key)
                    added += 1
            keep = sorted(days)[-USAGE_DAYS:]
            for d in [d for d in days if d not in keep]:
                del days[d]
            for d in sorted(days)[:-USAGE_KEY_DAYS]:
                for b in days[d].values():
                    b.pop("k", None)
            self.usage_t, self.usage_err = now, err
            self._usage_save()
        return added

    def used(self, short):
        """{today, failed_today, h24, failed_24h, last (HH:MM or None), last_day} or None when nothing was ever counted/read yet."""
        now = self.clock()
        with self.ulock:
            if not self.usage_t and not self.usage["days"]:
                return None
            days = self.usage["days"]
            today, yest = day_str(now), day_str(now - 86400)
            t = (days.get(today) or {}).get(short) or {}
            y = (days.get(yest) or {}).get(short) or {}
            hr = time.localtime(now).tm_hour
            h24 = sum(sum(b.get(k, [0] * 24)[hr + 1:]) for b, k in ((y, "h"), (y, "hf"))) + sum(sum(t.get(k, [0] * 24)[:hr + 1]) for k in ("h", "hf"))
            f24 = sum(y.get("hf", [0] * 24)[hr + 1:]) + sum(t.get("hf", [0] * 24)[:hr + 1])
            last, last_day = None, None
            for d in sorted(days, reverse=True):
                b = days[d].get(short)
                if b and b.get("last", -1) >= 0:
                    last, last_day = "%02d:%02d" % (b["last"] // 3600, b["last"] % 3600 // 60), d
                    break
            return {"today": t.get("ok", 0) + t.get("fail", 0), "failed_today": t.get("fail", 0), "h24": h24, "failed_24h": f24, "last": last, "last_day": last_day, "today_day": today}

    def usage_report(self):
        """-> (200, obj): per installed add-on, counts for the last 7 days. Counts and times only."""
        now = self.clock()
        dates = [day_str(now - 86400 * i) for i in range(6, -1, -1)]
        try:
            prefix, _ = self.context()
            inst = self.installed_map(prefix)
        except provisioner.SupError as e:
            return 502, {"error": str(e)}
        rows = []
        with self.ulock:
            for c in self.catalogue:
                s = c["slug"]
                if s not in inst or not s.startswith("ih_"):
                    continue
                per, fl = [], 0
                for d in dates:
                    b = (self.usage["days"].get(d) or {}).get(s) or {}
                    per.append(b.get("ok", 0) + b.get("fail", 0)); fl += b.get("fail", 0)
                rows.append({"slug": s, "state": inst[s]["state"], "per_day": per, "total": sum(per), "failed": fl, "unreadable": s in self.usage_err})
        for r in rows:
            u = self.used(r["slug"]); r["last"], r["last_day"] = (u or {}).get("last"), (u or {}).get("last_day")
        rows.sort(key=lambda r: (-r["total"], r["slug"]))
        return 200, {"days": dates, "addons": rows, "counted_at": self.usage_t or None,
                     "note": "Counted from the recent log of each add-on (a limited tail), saved every 5 minutes, so older days survive log rotation. Counts start when this version first ran."}

    def trace_list(self):
        store = self.trace_src()
        return (200, store.list()) if store else (200, {"traces": [], "retention_days": 7})

    def trace_detail(self, trace_id):
        store = self.trace_src()
        row = store.get(trace_id) if store else None
        return (200, row) if row else (404, {"error": "trace not found"})

    def job(self, jid):
        j = self.jobs.get(jid)
        return (200, j) if j else (404, {"error": "unknown job"})

    # ---- plugins (ih_jobs). The `settings` text of a plugin holds private data: only its key names ever leave this class.
    JOBS = "ih_jobs"

    def _jobs_full(self):
        prefix, _ = self.context()
        return prefix + "_" + self.JOBS

    @staticmethod
    def _setting_keys(text):
        try:
            d = json.loads(text) if str(text or "").strip() else {}
        except ValueError:
            return []
        return sorted(str(k)[:60] for k in d) if isinstance(d, dict) else []

    def plugins(self):
        """-> (code, obj). obj: {installed, running, plugins:[{name, live, settings_keys, mode, schedule, next_run, last_run, last_error, runs}]}."""
        try:
            full = self._jobs_full()
            try:
                info = self.sup("GET", "/addons/%s/info" % full)
            except provisioner.SupError:
                return 200, {"installed": False, "running": False, "plugins": []}
        except provisioner.SupError as e:
            return 502, {"error": str(e)}
        opts = info.get("options") if isinstance(info, dict) and isinstance(info.get("options"), dict) else {}
        listed = [r for r in (opts.get("plugins") or []) if isinstance(r, dict) and str(r.get("name") or "").strip()]
        live = self.http("http://%s:8792/status" % full.replace("_", "-"))
        pm = live.get("plugins") if isinstance(live, dict) and isinstance(live.get("plugins"), dict) else {}
        out = []
        for r in listed:
            n = str(r["name"]).strip()
            st = pm.get(n) if isinstance(pm.get(n), dict) else {}
            out.append({"name": n, "live": r.get("live") is True, "settings_keys": self._setting_keys(r.get("settings")),
                        "mode": st.get("mode"), "schedule": st.get("schedule"), "next_run": st.get("next_run"),
                        "last_run": st.get("last_run"), "last_error": str(st["last_error"])[:200] if st.get("last_error") else None,
                        "runs": st.get("runs"), "errors": st.get("errors"), "last_ok": st.get("last_ok"),
                        "last_success": st.get("last_success"), "last_success_mode": st.get("last_success_mode")})
        return 200, {"installed": True, "running": isinstance(live, dict), "plugins": out}

    def plugin_set(self, name, live):
        """Flip plugins[name].live with a read-modify-write of the FULL options, then restart ih_jobs. -> (code, obj); never returns settings."""
        if not isinstance(live, bool):
            return 400, {"error": "live must be true or false"}
        try:
            full = self._jobs_full()
            info = self.sup("GET", "/addons/%s/info" % full)
            opts = dict(info.get("options")) if isinstance(info, dict) and isinstance(info.get("options"), dict) else None
            if opts is None:
                return 502, {"error": "no options"}
            plist = [dict(r) if isinstance(r, dict) else r for r in opts.get("plugins") or []]
            hit = [r for r in plist if isinstance(r, dict) and str(r.get("name") or "").strip() == name]
            if not hit:
                return 404, {"error": "unknown plugin"}
            hit[0]["live"] = live
            opts["plugins"] = plist
            self.sup("POST", "/addons/%s/options" % full, {"options": opts})
            self.sup("POST", "/addons/%s/restart" % full)
        except provisioner.SupError as e:
            return 502, {"error": str(e)}
        return 200, {"ok": True, "name": name, "live": live}

    # ---- settings and health
    def settings(self):
        """{addons:[{slug, missing:[names]}], provisioner, config_slug}. Names only, never values."""
        try:
            rows = self.rows()
            own = self.context()[0] + "_ih_orchestrator"
        except provisioner.SupError as e:
            return 502, {"error": str(e)}
        p = self.prov()
        st = ((p.get_status() if p else None) or {}).get("addons") or {}
        out = [{"slug": r["slug"], "full": r.get("full"), "missing": [str(m)[:80] for m in (st.get(r["slug"]) or {}).get("missing") or []],
                "optional_empty": [str(m)[:80] for m in (st.get(r["slug"]) or {}).get("optional_empty") or []],
                "error": (st.get(r["slug"]) or {}).get("error")} for r in rows if r["installed"] and r["slug"] in st]
        return 200, {"addons": out, "provisioner": bool(p), "config_slug": own}

    def fill_now(self):
        if not self.prov():
            return 409, {"error": "the settings hub is off (turn on 'provision' in the orchestrator configuration)"}
        return 202, {"job": self.jobs.submit("provision", lambda step: self._provision(step))}

    def health(self):
        snap = self.health_src()
        if not isinstance(snap, dict):
            return 200, {"services": [], "counts": {}, "total": 0, "stale": True, "age_s": None}
        svc = [{"name": r.get("name"), "state": r.get("state"), "kind": r.get("kind"), "version": r.get("version"),
                "checks": [str(c.get("detail") or "")[:200] for c in r.get("checks") or [] if isinstance(c, dict)]}
               for r in snap.get("services") or []]
        return 200, {"services": svc, "counts": snap.get("counts"), "total": snap.get("total"), "stale": snap.get("stale"), "age_s": snap.get("age_s"),
                     "consumer_progress": snap.get("consumer_progress") or []}


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><meta name=theme-color content='#101511'><title>Intelligent Home</title>
<style>:root{color-scheme:light dark;--bg:#f3f5ef;--panel:#fff;--panel2:#e9eee5;--ink:#172019;--muted:#657267;--line:#d9e0d6;--accent:#a8f06b;--accent-ink:#17310b;--blue:#326b4d;--ok:#26734b;--bad:#c34639;--warn:#9a650e;--warn-bg:#fff1d5;--shadow:0 12px 36px #1720190b;--empty:#d7ded5}
@media(prefers-color-scheme:dark){:root{--bg:#101511;--panel:#171f19;--panel2:#202a22;--ink:#edf4ed;--muted:#9eaea0;--line:#303d32;--accent:#b4f77d;--accent-ink:#17240f;--blue:#9ddc91;--ok:#93dfa5;--bad:#ff978a;--warn:#ffd17e;--warn-bg:#3a3020;--shadow:0 16px 42px #0005;--empty:#3a473b}}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 82% -12%,#a8f06b20,transparent 35%),var(--bg);color:var(--ink);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}#shell{max-width:1440px;margin:auto;padding:26px clamp(16px,3vw,44px) 60px}.brand{display:flex;align-items:center;gap:13px;margin:0 0 27px}.mark{width:42px;height:42px;border-radius:15px;background:var(--accent);display:grid;place-items:center;color:var(--accent-ink);font-size:23px;font-weight:900;box-shadow:0 6px 22px #a8f06b33}.brand h1{font-size:15px;line-height:1.15;margin:0;letter-spacing:.12em;text-transform:uppercase}.brand small{display:block;margin-top:5px;color:var(--muted);font-size:12px;letter-spacing:0;text-transform:none}.topline{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:17px}.eyebrow{color:var(--muted);font-size:11px;font-weight:750;letter-spacing:.15em;text-transform:uppercase}h2{font-size:clamp(27px,4vw,42px);line-height:1.06;letter-spacing:-.04em;margin:5px 0 8px}.lead{margin:0;color:var(--muted);max-width:680px}.livepill{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:8px 13px;white-space:nowrap;font-size:12px;color:var(--muted)}.livepill:before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:8px;box-shadow:0 0 0 4px color-mix(in srgb,var(--ok) 14%,transparent)}
nav{display:flex;gap:5px;overflow:auto;position:sticky;top:0;z-index:4;padding:8px;margin:25px -8px 22px;border:1px solid var(--line);border-radius:17px;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(18px)}nav a{cursor:pointer;user-select:none;padding:9px 15px;border-radius:11px;text-decoration:none;color:var(--muted);font-size:13px;font-weight:650;white-space:nowrap;transition:.18s}nav a:hover{background:var(--panel2);color:var(--ink)}nav a.on{background:var(--accent);color:var(--accent-ink);box-shadow:0 4px 14px #a8f06b26}
#msg{padding:11px 14px;border:1px solid var(--line);border-radius:12px;margin:0 0 16px;display:none;background:var(--panel);box-shadow:var(--shadow)}.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,310px),1fr));gap:13px;margin-top:16px}.card{border:1px solid var(--line);border-radius:17px;padding:17px;min-width:0;background:var(--panel);box-shadow:var(--shadow);transition:transform .18s,border-color .18s}.card:hover{transform:translateY(-2px);border-color:color-mix(in srgb,var(--blue) 55%,var(--line))}.card.s-started{border-top:2px solid var(--ok)}.card.s-stopped,.card.s-error{border-top:2px solid var(--bad)}.card h3{margin:0 0 5px;font-size:15px;letter-spacing:-.01em;overflow-wrap:anywhere}.d{font-size:12px;color:var(--muted)}.r{color:var(--bad);font-weight:700}.g{color:var(--ok);font-weight:650}.o{color:var(--warn);font-weight:700}.kv{display:flex;flex-wrap:wrap;gap:8px 14px;margin:14px 0 3px;font-size:12px}.kv>span{min-width:calc(50% - 14px)}.btns{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}button,a.b{padding:8px 12px;border:1px solid var(--line);border-radius:10px;background:var(--panel2);color:var(--ink);font:inherit;font-size:12px;font-weight:650;text-decoration:none;cursor:pointer;transition:.15s}button:hover,a.b:hover{border-color:var(--blue);transform:translateY(-1px)}button:focus-visible,a:focus-visible{outline:3px solid var(--blue);outline-offset:2px}.hero{position:relative;overflow:hidden;border:1px solid var(--line);border-radius:24px;padding:clamp(22px,4vw,38px);background:linear-gradient(120deg,var(--panel),color-mix(in srgb,var(--panel2) 70%,var(--panel)));box-shadow:var(--shadow)}.hero:after{content:"IH";position:absolute;right:4%;top:50%;transform:translateY(-50%);font-size:clamp(100px,20vw,230px);font-weight:900;letter-spacing:-.1em;color:color-mix(in srgb,var(--blue) 8%,transparent);pointer-events:none}.hero>*{position:relative;z-index:1}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:15px 0 26px}.stat{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:17px;box-shadow:var(--shadow)}.stat b{display:block;font-size:clamp(25px,4vw,34px);letter-spacing:-.05em;line-height:1.1}.stat span{display:block;margin-top:6px;color:var(--muted);font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.1em}.sectionhead{display:flex;align-items:end;justify-content:space-between;gap:12px;margin:25px 0 10px}.sectionhead h3{margin:0;font-size:17px;letter-spacing:-.02em}.quietlink{border:0;background:none;color:var(--blue);padding:4px;font-size:12px}.servicegrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,260px),1fr));gap:10px}.service{display:flex;align-items:center;gap:12px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:13px 14px;cursor:pointer;text-align:left;min-width:0}.service:hover{border-color:var(--blue)}.service .dot{width:10px;height:10px;flex:none;border-radius:50%;background:var(--ok);box-shadow:0 0 0 4px color-mix(in srgb,var(--ok) 14%,transparent)}.service.off .dot{background:var(--bad);box-shadow:0 0 0 4px color-mix(in srgb,var(--bad) 14%,transparent)}.service .svcname{font-size:13px;font-weight:700;overflow-wrap:anywhere}.service .svcmeta{margin-left:auto;color:var(--muted);font-size:11px;white-space:nowrap}.shortcuts{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}.shortcut{padding:15px;border:1px solid var(--line);border-radius:14px;background:var(--panel);cursor:pointer;text-align:left;color:var(--ink);font:inherit}.shortcut strong{display:block;font-size:13px}.shortcut span{display:block;color:var(--muted);font-size:11px;margin-top:4px}.shortcut:hover{border-color:var(--blue)}pre{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:12px;overflow:auto;max-height:260px;font-size:12px;white-space:pre-wrap;word-break:break-all}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:11px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-size:10px;letter-spacing:.1em;text-transform:uppercase}.w{overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:15px}.chip{display:inline-block;margin:4px 5px 8px 0;padding:7px 13px;border:1px solid var(--line);border-radius:999px;cursor:pointer;font-size:12px;color:var(--muted)}.chip.on{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.trace-layout{display:grid;grid-template-columns:minmax(240px,340px) minmax(0,1fr);gap:14px;align-items:start}.trace-list,.trace-detail{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px;min-width:0}.trace-search{width:100%;padding:10px 12px;margin-bottom:10px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--ink);font:inherit}.trace-row{display:flex;width:100%;flex-direction:column;align-items:flex-start;text-align:left;gap:3px;background:var(--bg);margin:0 0 7px;padding:11px}.trace-row.selected{border-color:var(--blue);box-shadow:inset 3px 0 var(--blue)}.trace-id{font:11px ui-monospace,SFMono-Regular,monospace;color:var(--muted);overflow-wrap:anywhere}.trace-timeline{position:relative;margin:18px 0 0 8px;padding-left:24px}.trace-timeline:before{content:"";position:absolute;left:6px;top:7px;bottom:7px;width:2px;background:var(--line)}.trace-span{position:relative;padding:12px 14px;margin:0 0 10px;border:1px solid var(--line);border-radius:12px;background:var(--bg)}.trace-dot{position:absolute;left:-24px;top:17px;width:12px;height:12px;border-radius:50%;background:var(--blue);border:3px solid var(--panel);box-sizing:content-box}.trace-span.trace-ok .trace-dot{background:var(--ok)}.trace-span.trace-error .trace-dot{background:var(--bad)}.trace-span-head{display:flex;align-items:center;gap:9px;justify-content:space-between}.trace-span-head strong{font-size:13px}.trace-status{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}.trace-status.ok{color:var(--ok)}.trace-status.error{color:var(--bad)}.trace-status.skipped,.trace-status.unknown{color:var(--warn)}.trace-reason{font-size:11px;color:var(--warn);margin-top:4px}.trace-detail h3{margin:7px 0;font-size:14px}.trace-detail time{display:block;margin-top:5px;font-size:10px}
@media(max-width:700px){#shell{padding:18px 14px 38px}.brand{margin-bottom:22px}.topline{align-items:flex-start}.livepill{font-size:0;padding:10px}.livepill:before{margin:0}.stats{grid-template-columns:repeat(2,1fr);gap:9px}.stat{padding:14px}.shortcuts{grid-template-columns:repeat(2,1fr)}nav{margin-top:17px}td,th{padding:9px 8px}}@media(max-width:700px){.trace-layout{grid-template-columns:minmax(0,1fr)}.trace-list{max-height:36vh;max-height:36dvh;overflow:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain}.trace-detail{scroll-margin-top:12px}.trace-timeline{margin-left:0;padding-left:20px}.trace-dot{left:-20px}.trace-span{padding:12px;min-width:0}.trace-span-head{align-items:flex-start;flex-wrap:wrap}.trace-span-head strong{min-width:0;overflow-wrap:anywhere}.trace-status{flex-shrink:0}}@media(prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;transition:none!important}}
</style><div id=shell><header class=brand><div class=mark>✳</div><h1>Intelligent Home<small>Operations console · Home Assistant</small></h1></header><div class=topline><div><div class=eyebrow>Control room</div><h2 id=pagetitle>Good to see you.</h2><p class=lead id=pagesub>One clear view of your home services and their health.</p></div><div class=livepill>Connected</div></div>
<nav id=nav><a data-t=overview>Overview</a><a data-t=addons>Add-ons</a><a data-t=install>Catalogue</a><a data-t=plugins>Jobs</a><a data-t=traces>Traces</a><a data-t=settings>Settings</a><a data-t=health>Health</a><a data-t=usage>Usage</a></nav>
<div id=msg></div><div id=main>Loading your control room…</div></div>
<script>
var B=location.pathname.endsWith('/')?location.pathname:location.pathname+'/';
var TABS=['overview','addons','install','plugins','traces','settings','health','usage'],TAB='overview',FILT='All',OPEN={},DATA=null;
function U(p){return B+p}
function E(tag,txt,cls){var e=document.createElement(tag);if(txt!==undefined&&txt!==null)e.textContent=txt;if(cls)e.className=cls;return e}
function say(t){var m=document.getElementById('msg');m.style.display=t?'block':'none';m.textContent=t||''}
function btn(label,fn){var b=E('button',label);b.type='button';b.onclick=fn;return b}
/* No confirm(): the HA phone app can crash on dialogs inside the panel frame (28 Oct 2026). Two taps instead. */
function ask(b,text,fn){if(b.dataset.armed){delete b.dataset.armed;b.textContent=b.dataset.label;fn();return}b.dataset.label=b.textContent;b.dataset.armed=1;b.textContent='Tap again to confirm';say(text);setTimeout(function(){if(b.dataset.armed){delete b.dataset.armed;b.textContent=b.dataset.label}},5000)}
function T(x){if(!x)return null;var d=new Date(typeof x=='number'?x*1000:x);return isNaN(d)?String(x):d.toLocaleString([], {day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}
function kv(card,pairs){var d=E('div',null,'kv');pairs.forEach(function(p){if(p[1]===null||p[1]===undefined)return;var s=E('span');s.appendChild(E('span',p[0]+' ','d'));s.appendChild(E('span',String(p[1]),p[2]));d.appendChild(s)});card.appendChild(d)}
function J(r){return r.json().then(function(j){return[r.status,j]})}
function post(path){return fetch(U(path),{method:'POST'}).then(J)}
function act(a,s,extra){say(a+' '+s+' ...');post('api/action/'+a+'/'+s+(extra||'')).then(function(x){var c=x[0],j=x[1];
 if(c==202)return poll(j.job);
 if(c==409&&j.warning){if(j.installable&&j.installable.length){say(j.warning);var m=document.getElementById('msg');m.appendChild(E('br'));m.appendChild(btn('Install those first, then '+s,function(){act(a,s,'?with_requires=1')}));return}say(j.warning+(j.external&&j.external.length?' ('+j.external.join(', ')+' is not ours: install it from the add-on store)':''));return}
 say(j.error||j.warning||('failed '+c))}).catch(function(e){say('error: '+e)})}
function poll(id){fetch(U('api/job/'+id)).then(function(r){return r.json()}).then(function(j){
 say(j.label+': '+j.state+(j.steps.length?' ('+j.steps[j.steps.length-1]+')':'')+(j.error?' ERROR '+j.error:''));
 if(j.state=='queued'||j.state=='running')setTimeout(function(){poll(id)},2000);else show()})}
function age(x){if(!x||x.age_s==null)return '-';var a=x.age_s;return (x.state||'')+' '+(a<90?a+'s':a<5400?Math.round(a/60)+'m':Math.round(a/3600)+'h')}
/* HA navigation without a page load (the old /hassio/addon path is a 404 since the apps rename). */
function goHA(path){try{window.top.history.pushState(null,'',path);window.top.dispatchEvent(new CustomEvent('location-changed',{detail:{replace:false}}))}catch(e){window.top.location.href=path}}
function cfgLink(full,label){return btn(label||'Open config',function(){goHA(['','config','app',full,'config'].join('/'))})}
function logs(slug,holder){var p=holder.querySelector('pre');if(p){p.remove();return}p=E('pre','loading...');holder.appendChild(p);
 fetch(U('api/logs/'+slug)).then(function(r){return r.json()}).then(function(j){p.textContent=j.error?j.error:j.lines.join('\\n')})}
function usedTxt(u){if(!u)return null;return u.today+' today'+(u.failed_today?' ('+u.failed_today+' failed)':'')+', last '+(u.last?(u.last_day==u.today_day||!u.last_day?u.last:u.last+' on '+u.last_day.slice(5)):'never seen')}
function imgTxt(a){return a.image?'yes (tag '+(a.latest||a.version||'?')+')':'no (built on the Pi)'}
function jump(t){TAB=t;DATA=null;say('');show()}
function tabOverview(m){m.appendChild(E('div','Reading live add-on and health data…','d'));Promise.all([fetch(U('api/addons')).then(function(r){return r.json()}),fetch(U('api/health')).then(function(r){return r.json()})]).then(function(x){var d=x[0],h=x[1];m.textContent='';if(d.error)throw Error(d.error);DATA=d;
 var installed=d.addons.filter(function(a){return a.installed}),started=installed.filter(function(a){return a.state=='started'}),updates=installed.filter(function(a){return a.update}),cn=h.counts||{},fails=cn.FAIL||0;
 var hero=E('section',null,'hero');hero.appendChild(E('div','YOUR HOME, AT A GLANCE','eyebrow'));hero.appendChild(E('h2',fails?fails+' service'+(fails==1?' needs':'s need')+' attention':'Everything in its place.'));hero.appendChild(E('p',fails?'A health check found an issue. Open Health to see what needs attention.':'Your services are running and the system is keeping watch.','lead'));var hb=E('div',null,'btns');hb.appendChild(btn('Open health overview →',function(){jump('health')}));hb.appendChild(btn('Manage add-ons',function(){jump('addons')}));hero.appendChild(hb);m.appendChild(hero);
 var stats=E('div',null,'stats');[[started.length,'Running'],[installed.length,'Installed'],[updates.length,'Updates available'],[fails,(h.stale?'Health checks · stale':'Health checks failing')]].forEach(function(a,i){var s=E('div',null,'stat');s.appendChild(E('b',String(a[0]),i==3&&a[0]?'r':''));s.appendChild(E('span',a[1]));stats.appendChild(s)});m.appendChild(stats);
 var sh=E('div',null,'sectionhead');sh.appendChild(E('h3','Your services'));sh.appendChild(btn('View all →',function(){jump('addons')}));m.appendChild(sh);var grid=E('div',null,'servicegrid');installed.slice(0,12).forEach(function(a){var b=btn('');b.className='service'+(a.state=='started'?'':' off');b.appendChild(E('i',null,'dot'));var wrap=E('span');wrap.appendChild(E('span',a.slug,'svcname'));wrap.appendChild(E('span',(a.state||'unknown')+(a.version?' · v'+a.version:'')+(a.update?' · update':''),'svcmeta'));b.appendChild(wrap);b.onclick=function(){jump('addons')};grid.appendChild(b)});if(!installed.length)grid.appendChild(E('p','No IH add-ons are installed yet.','d'));m.appendChild(grid);
 var hh=E('div',null,'sectionhead');hh.appendChild(E('h3','Common tasks'));m.appendChild(hh);var shortcuts=E('div',null,'shortcuts');[['Install an add-on','Browse the IH catalogue','install'],['Review scheduled jobs','See runs, mode and recent results','plugins'],['Trace a request','Follow a Discord AI request through IH','traces'],['Check shared settings','Find missing configuration','settings'],['Inspect usage','See recent invocation trends','usage']].forEach(function(a){var b=btn('');b.className='shortcut';b.appendChild(E('strong',a[0]+' ↗'));b.appendChild(E('span',a[1]));b.onclick=function(){jump(a[2])};shortcuts.appendChild(b)});m.appendChild(shortcuts)
 }).catch(function(e){m.textContent='';m.appendChild(E('p','Could not load the overview: '+e,'r'));m.appendChild(btn('Try again',function(){show()}))})}
function tabAddons(m,d){var c=E('div',null,'cards');d.addons.filter(function(a){return a.installed}).forEach(function(a){
 var k=E('div',null,'card s-'+(a.state||'none'));k.appendChild(E('h3',a.slug));k.appendChild(E('div',a.description,'d'));
 kv(k,[['Version',(a.version||'?')+(a.update?' -> update to '+a.latest:''),a.update?'o':''],['Prebuilt image',imgTxt(a),a.image?'g':''],['State',a.state||'-',a.state=='started'?'g':'r'],
  ['CPU',a.cpu==null?null:a.cpu+' %'],['Mem',a.mem_mb==null?null:a.mem_mb+' MB'],['Seen',age(a.seen)],
  ['Missing settings',a.missing_settings==null?null:a.missing_settings,a.missing_settings?'o':''],['Used',usedTxt(a.used),a.used&&a.used.failed_today?'o':'']]);
 if(a.can_act){var b=E('div',null,'btns');
  if(a.state=='started')b.appendChild(btn('Stop',function(){act('stop',a.slug)}));else b.appendChild(btn('Start',function(){act('start',a.slug)}));
  b.appendChild(btn('Restart',function(){act('restart',a.slug)}));
  if(a.update)b.appendChild(btn('Update to '+a.latest,function(){act('update',a.slug)}));
  b.appendChild(btn('Logs',function(){logs(a.slug,k)}));if(a.full)b.appendChild(cfgLink(a.full));k.appendChild(b)}
 else if(a.full){var b2=E('div',null,'btns');b2.appendChild(cfgLink(a.full));k.appendChild(b2)}
 c.appendChild(k)});m.appendChild(c)}
function tabInstall(m,d){var c=E('div',null,'cards'),n=0;d.addons.filter(function(a){return !a.installed&&a.can_act}).forEach(function(a){n++;
 var k=E('div',null,'card');k.appendChild(E('h3',a.name+' ('+a.slug+')'));k.appendChild(E('div',a.description));kv(k,[['Prebuilt image',imgTxt(a),a.image?'g':'']]);
 var w=E('div',null,'kv');a.needs.forEach(function(x){w.appendChild(E('span','needs '+x.slug+': '+(x.status=='ok'?'ok':'missing ('+x.status+')'),x.status=='ok'?'g':'r'))});if(!a.needs.length)w.appendChild(E('span','needs nothing','d'));k.appendChild(w);
 var b=E('div',null,'btns');b.appendChild(btn('Install '+(a.latest||''),function(){act('install',a.slug)}));k.appendChild(b);c.appendChild(k)});
 m.appendChild(n?c:E('p','Everything in the catalogue is installed.'))}
function tabPlugins(m){m.appendChild(E('p','Loading plugins...'));fetch(U('api/plugins')).then(J).then(function(x){m.textContent='';var j=x[1];
 if(x[0]!=200){m.appendChild(E('p',j.error||'failed','r'));return}
 if(!j.installed){m.appendChild(E('p','The ih_jobs add-on is not installed.'));return}
 if(!j.running)m.appendChild(E('p','The jobs add-on did not answer: live details are missing.','o'));
 m.appendChild(E('p','Results are since jobs started. Success includes expected skips and dry runs; it does not confirm delivery.'));
 if(!j.plugins.length)m.appendChild(E('p','No plugins are listed in the ih_jobs configuration.'));
 var c=E('div',null,'cards');j.plugins.forEach(function(p){var k=E('div',null,'card s-'+(p.live?'started':'none'));k.appendChild(E('h3',p.name));
  kv(k,[['Mode',p.live?'Live':'Dry',p.live?'g':'o'],['Running as',p.mode],['Schedule',p.schedule?(p.schedule.kind=='daily'?'daily at '+p.schedule.at:p.schedule.kind=='every'?'every '+p.schedule.minutes+' min':p.schedule.kind):'-'],['Next',T(p.next_run)],['Last run',T(p.last_run)],['Runs',p.runs],['Errors',p.errors],['Last result',p.last_ok===true?'Succeeded':p.last_ok===false?'Failed':'Unknown',p.last_ok===false?'r':''],['Last success',T(p.last_success)],['Success mode',p.last_success_mode],['Last error',p.last_error,p.last_error?'r':''],
   ['Settings',p.settings_keys.length?p.settings_keys.join(', '):null]]);
  var b=E('div',null,'btns');b.appendChild(btn(p.live?'Make dry':'Make live',function(){var me=this;
   ask(me,(p.live?'Make '+p.name+' DRY (stops real actions)':'Make '+p.name+' LIVE (real actions)')+'? ih_jobs will restart.',function(){
   say('saving ...');post('api/plugin/'+encodeURIComponent(p.name)+'/'+(p.live?'dry':'live')).then(function(y){say(y[0]==200?p.name+' is now '+(y[1].live?'live':'dry')+'; ih_jobs restarted':(y[1].error||'failed'));show()})})}));
  k.appendChild(b);c.appendChild(k)});m.appendChild(c)})}
function tabSettings(m){m.appendChild(E('p','Loading settings...'));fetch(U('api/settings')).then(J).then(function(x){m.textContent='';var j=x[1];
 if(x[0]!=200){m.appendChild(E('p',j.error||'failed','r'));return}
 var t=E('div',null,'btns');t.appendChild(btn('Fill now',function(){var me=this;ask(me,'Run the settings hub now? It fills empty options of the add-ons.',function(){say('provision ...');post('api/provision').then(function(y){if(y[0]==202)poll(y[1].job);else say(y[1].error||'failed')})})}));
 t.appendChild(cfgLink(j.config_slug,'Shared settings list (orchestrator config)'));m.appendChild(t);
 if(!j.provisioner)m.appendChild(E('p','The settings hub is off. Turn on provision in the orchestrator configuration.','o'));
 var c=E('div',null,'cards');j.addons.forEach(function(a){var k=E('div',null,'card s-'+(a.missing.length?'stopped':'started'));k.appendChild(E('h3',a.slug));
  k.appendChild(E('div',a.missing.length?'Missing: '+a.missing.join(', '):'All settings filled',a.missing.length?'o':'g'));if(a.optional_empty&&a.optional_empty.length)k.appendChild(E('div','Optional, not set: '+a.optional_empty.join(', '),'d'));if(a.error)k.appendChild(E('div',a.error,'r'));
  if(a.full){var b=E('div',null,'btns');b.appendChild(cfgLink(a.full));k.appendChild(b)}c.appendChild(k)});m.appendChild(c)})}
function tabHealth(m){m.appendChild(E('p','Loading health...'));fetch(U('api/health')).then(J).then(function(x){m.textContent='';var j=x[1];
 var cn=j.counts||{};m.appendChild(E('p',(cn.OK||0)+' OK, '+(cn.FAIL||0)+' FAIL, '+(cn.UNKNOWN||0)+' unknown'+(j.stale?' (STALE data)':''),j.stale||cn.FAIL?'o':'g'));
 ['All','FAIL','OK'].forEach(function(f){var c=E('span',f,'chip'+(FILT==f?' on':''));c.onclick=function(){FILT=f;show()};m.appendChild(c)});
 var w=E('div',null,'w'),t=E('table'),h=E('tr');['Service','State','Kind','Checks'].forEach(function(z){h.appendChild(E('th',z))});t.appendChild(h);
 j.services.filter(function(s){return FILT=='All'||s.state==FILT}).forEach(function(s){var r=E('tr');
  [s.name,s.state,s.kind||'',(s.checks||[]).join('; ')].forEach(function(v,i){r.appendChild(E('td',v,i==1?(v=='OK'?'g':v=='FAIL'?'r':'o'):''))});t.appendChild(r)});
 w.appendChild(t);m.appendChild(w);
 m.appendChild(E('h3','Consumer progress'));
 var p=j.consumer_progress||[];
 if(!p.length){m.appendChild(E('p','No consumer outcome streams reported yet.','d'));return}
 var pw=E('div',null,'w'),pt=E('table'),ph=E('tr');['Consumer','State','Pending','Oldest pending','No progress','Reason'].forEach(function(z){ph.appendChild(E('th',z))});pt.appendChild(ph);
 p.forEach(function(s){var r=E('tr'),state=s.status||'unknown',tone=state=='idle'?'g':state=='stalled'?'r':state=='disabled'?'d':'o';
  [s.consumer,state,s.pending==null?'—':String(s.pending),s.oldest_pending_age_s==null?'—':String(Math.floor(s.oldest_pending_age_s))+' s',s.no_progress_s==null?'—':String(Math.floor(s.no_progress_s))+' s',s.reason||''].forEach(function(v,i){r.appendChild(E('td',v,i==1?tone:''))});pt.appendChild(r)});
 pw.appendChild(pt);m.appendChild(pw)})}
function tabUsage(m){m.appendChild(E('p','Loading usage...'));fetch(U('api/usage')).then(J).then(function(x){m.textContent='';var j=x[1];
 if(x[0]!=200){m.appendChild(E('p',j.error||'failed','r'));return}
 m.appendChild(E('p','Invocations per day, last 7 days (oldest left, today right). Counted from the recent log of each add-on and saved, so older days are kept. Add-ons not used in 7 days are highlighted.','d'));
 var mx=1;j.addons.forEach(function(a){a.per_day.forEach(function(n){if(n>mx)mx=n})});
 var w=E('div',null,'w'),t=E('table'),h=E('tr');['Add-on','7 days','Total','Failed','Last'].forEach(function(z){h.appendChild(E('th',z))});t.appendChild(h);
 j.addons.forEach(function(a){var r=E('tr'),dead=a.total==0;if(dead)r.style.background='var(--warn-bg)';
  r.appendChild(E('td',a.slug+(a.state=='started'?'':' ('+(a.state||'stopped')+')')+(a.unreadable?' (log unreadable)':'')));
  var c=E('td'),bars=E('span');bars.style.cssText='display:inline-flex;align-items:flex-end;gap:2px;height:22px';
  a.per_day.forEach(function(n,i){var b=E('span');b.title=j.days[i]+': '+n;b.style.cssText='display:inline-block;width:8px;background:var('+(n?'--blue':'--empty')+');height:'+(n?Math.max(2,Math.round(22*n/mx)):2)+'px';bars.appendChild(b)});c.appendChild(bars);r.appendChild(c);
  r.appendChild(E('td',dead?'not used in 7 days':String(a.total),dead?'o':''));r.appendChild(E('td',String(a.failed),a.failed?'r':''));
  r.appendChild(E('td',a.last?(a.last+(a.last_day?' '+a.last_day.slice(5):'')):'-'));t.appendChild(r)});
 w.appendChild(t);m.appendChild(w)})}
function tabTraces(m){m.appendChild(E('p','Loading recent request traces…','d'));fetch(U('api/traces')).then(J).then(function(x){m.textContent='';if(x[0]!=200){m.appendChild(E('p',x[1].error||'Could not load traces','r'));return}var rows=x[1].traces||[];
 var intro=E('p','Follow a request across the components that handled it. Traces contain component names, timing and outcomes only; message text and Discord IDs are never stored. Traces are kept locally for up to 7 days.','d');m.appendChild(intro);
 var layout=E('div',null,'trace-layout'),left=E('div',null,'trace-list'),right=E('div',null,'trace-detail');layout.appendChild(left);layout.appendChild(right);m.appendChild(layout);
 var search=E('input');search.type='search';search.placeholder='Filter by trace ID';search.setAttribute('aria-label','Filter traces by ID');search.className='trace-search';left.appendChild(search);var items=E('div');left.appendChild(items);var selectedId='';
 function choose(id){selectedId=id;Array.prototype.forEach.call(items.querySelectorAll('button'),function(b){b.className='trace-row'+(b.dataset.id==id?' selected':'')});right.textContent='Loading trace…';fetch(U('api/trace/'+id)).then(J).then(function(y){right.textContent='';if(y[0]!=200){right.appendChild(E('p',y[1].error||'Could not load trace','r'));return}var tr=y[1];right.appendChild(E('div','REQUEST TRACE','eyebrow'));right.appendChild(E('h3',tr.trace_id,'trace-id'));right.appendChild(E('p',tr.spans.length+' steps · content-free metadata','d'));
  var line=E('div',null,'trace-timeline'),by={};tr.spans.forEach(function(s){by[s.span_id]=s});tr.spans.forEach(function(s){var depth=0,p=s.parent_span_id,seen={};while(p&&by[p]&&!seen[p]&&depth<5){seen[p]=1;depth++;p=by[p].parent_span_id}var node=E('article',null,'trace-span '+(s.status=='error'?'trace-error':s.status=='ok'?'trace-ok':''));node.style.marginLeft=Math.min(depth,4)*(window.matchMedia('(max-width:700px)').matches?8:18)+'px';node.appendChild(E('i',null,'trace-dot'));var head=E('div',null,'trace-span-head');head.appendChild(E('strong',s.operation));head.appendChild(E('span',s.status,'trace-status '+s.status));node.appendChild(head);node.appendChild(E('div',s.component+' · '+s.duration_ms+' ms','d'));if(s.reason)node.appendChild(E('div','Reason: '+s.reason,'trace-reason'));node.appendChild(E('time',T(s.started_at),'d'));line.appendChild(node)});right.appendChild(line)
 })}
function render(){items.textContent='';var q=search.value.toLowerCase().trim(),hit=rows.filter(function(r){return !q||r.trace_id.indexOf(q)>=0});if(!hit.length){selectedId='';items.appendChild(E('p',rows.length?'No trace IDs match.':'No traces yet. Traces appear after a supported request reaches IH.','d'));right.textContent='';return}hit.forEach(function(r){var b=btn('');b.type='button';b.className='trace-row';b.dataset.id=r.trace_id;b.appendChild(E('strong',r.root+' · '+r.spans+' steps'));b.appendChild(E('span',r.trace_id,'trace-id'));b.appendChild(E('span',T(r.started_at)+' · '+r.status,'d'));b.onclick=function(){choose(r.trace_id)};items.appendChild(b)});if(!hit.some(function(r){return r.trace_id==selectedId}))selectedId='';if(!selectedId)choose(hit[0].trace_id)}
 search.oninput=render;render()
 }).catch(function(e){m.textContent='';m.appendChild(E('p','Could not load traces: '+e,'r'))})}
function show(){var m=document.getElementById('main');var titles={overview:['Good to see you.','One clear view of your home services and their health.'],traces:['Request traces','See how a request moved through IH, step by step.'],addons:['Your add-ons','Start, update and inspect the services that make up Intelligent Home.'],install:['Add to your home','Browse available IH modules and install what you need.'],plugins:['House jobs','Review scheduled work, recent runs and operating mode.'],settings:['Shared settings','See which add-ons are configured and what still needs attention.'],health:['System health','Check service status and consumer progress.'],usage:['Usage trends','See how often your add-ons have been called over the last week.']};var title=titles[TAB]||titles.overview;document.getElementById('pagetitle').textContent=title[0];document.getElementById('pagesub').textContent=title[1];
 Array.prototype.forEach.call(document.querySelectorAll('nav a'),function(a){a.className=a.dataset.t==TAB?'on':''});
 if(TAB=='overview'){m.textContent='';tabOverview(m);return}
 if(TAB=='plugins'){m.textContent='';tabPlugins(m);return}
 if(TAB=='settings'){m.textContent='';tabSettings(m);return}
 if(TAB=='health'){m.textContent='';tabHealth(m);return}
 if(TAB=='traces'){m.textContent='';tabTraces(m);return}
 if(TAB=='usage'){m.textContent='';tabUsage(m);return}
 fetch(U('api/addons')).then(function(r){return r.json()}).then(function(d){
  if(d.error){say(d.error);return}
  var keep=m.querySelectorAll('pre').length;if(keep&&TAB=='addons'&&DATA)return;
  m.textContent='';DATA=d;(TAB=='install'?tabInstall:tabAddons)(m,d)}).catch(function(e){say('error: '+e)})}
/* Tabs switch in JS: no hash links (the HA phone app treats link taps in the panel frame as navigation). #name on first load still works. */
if(TABS.indexOf(location.hash.slice(1))>=0)TAB=location.hash.slice(1);
Array.prototype.forEach.call(document.querySelectorAll('nav a'),function(a){a.setAttribute('role','button');a.onclick=function(){jump(a.dataset.t)}});show();
setInterval(function(){if(!document.hidden&&(TAB=='addons'||TAB=='install'))show()},30000);
</script>"""


def make_handler(mgr):
    class MH(BaseHTTPRequestHandler):
        def _send(self, code, body, ct):
            self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _gate(self):
            if not client_allowed(self.client_address[0]):
                self._json(403, {"error": "ingress only"}); return False
            return True

        def do_GET(self):
            if not self._gate():
                return
            p = urlparse(self.path).path.rstrip("/") or "/"
            try:
                if p == "/":
                    return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                if p == "/api/plugins":
                    return self._json(*mgr.plugins())
                if p == "/api/settings":
                    return self._json(*mgr.settings())
                if p == "/api/usage":
                    return self._json(*mgr.usage_report())
                if p == "/api/traces":
                    return self._json(*mgr.trace_list())
                if p.startswith("/api/trace/"):
                    return self._json(*mgr.trace_detail(p[len("/api/trace/"):]))
                if p == "/api/health":
                    return self._json(*mgr.health())
                if p == "/api/addons":
                    return self._json(200, {"addons": mgr.rows()})
                if p.startswith("/api/job/"):
                    return self._json(*mgr.job(p[9:]))
                if p.startswith("/api/logs/"):
                    return self._json(*mgr.logs(p[10:]))
            except provisioner.SupError as e:
                return self._json(502, {"error": str(e)})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._gate():
                return
            u = urlparse(self.path)
            parts = u.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "action"]:
                wr = parse_qs(u.query).get("with_requires", [""])[0] == "1"
                return self._json(*mgr.act(parts[2], parts[3], wr))
            if len(parts) == 4 and parts[:2] == ["api", "plugin"] and parts[3] in ("live", "dry"):
                return self._json(*mgr.plugin_set(unquote(parts[2]), parts[3] == "live"))
            if parts == ["api", "provision"]:
                return self._json(*mgr.fill_now())
            self._json(404, {"error": "not found"})

        def log_message(self, *a):
            pass
    return MH


def serve(mgr, port):
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(mgr)).serve_forever()
