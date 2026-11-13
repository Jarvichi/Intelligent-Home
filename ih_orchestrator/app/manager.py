"""Add-on manager page (v0.10.0: trace explorer; consumer outcome progress). Ingress only: the server on IH_MANAGER_PORT answers the Supervisor ingress gateway (172.30.32.2) and nobody else.
Lists the add-ons of catalogue.json merged with Supervisor data, runs install/start/stop/restart/update in background jobs, shows needs / needed by.
Acts ONLY on catalogue slugs of our own repository (same scope rule as the provisioner). stdlib only. Never logs or returns option values."""
import hashlib, json, os, re, threading, time, urllib.request, urllib.error
from pathlib import Path
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
_WHY = r"(?:.*?\bwhy=(?P<why>\S+))?"   # optional reason tag after ok= (cooking logs `ok=False why=no_such_recipe http=404`)
BENIGN_WHY = {"no_such_recipe", "not_found"}   # a lookup that found nothing is a normal answer, not a failed call
USAGE_FORMATS = [
    ("ai_layer request line", r"^ih_ai_layer$", re.compile(_T + r"request id=(?P<id>\S*)" + _OK)),       # `12:27:07 request id=.. caller=.. cap=.. ok=True`
    ("provider ask line", r"^ih_ai_layer_", re.compile(_T + r"ask id=(?P<id>\S*)" + _OK)),                # `.. ask id=.. agent=.. ok=True`
    ("invoke line", r"^ih_(jobs|games|cooking|orchestrator|orchestration_inbox|discord)", re.compile(_T + r"invoke id=(?P<id>\S*)" + _OK + _WHY)),   # `.. invoke id=run1 src=startup what=trust .. ok=True`
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
        found.append((h * 3600 + mi * 60 + se, m.group("ok") == "True" or gd.get("why") in BENIGN_WHY, "%s#%d" % (base, seen[base])))
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
                 "ui": {k: v[:240] for k, v in (c.get("ui") if isinstance(c.get("ui"), dict) else {}).items() if k in ("title", "purpose") and isinstance(v, str)},
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
            info = self.sup("GET", "/addons/%s/info" % full) or {}
            r["boot"] = info.get("boot")
            # Keep the ingress token/URL inside Supervisor. The UI only needs to know
            # whether Home Assistant's authenticated /app/<slug> route is available.
            r["ingress"] = bool(info.get("ingress_url") or info.get("ingress_entry"))
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
                     "consumer_progress": snap.get("consumer_progress") or [],
                     "ui_contributions": snap.get("ui_contributions") or []}


PAGE = (Path(__file__).parent / "ui" / "index.html").read_text(encoding="utf-8")


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
                if p in ("/ih-ui.css", "/ih-ui.js", "/ih-patterns.css", "/manager-logic.js"):
                    name = p[1:]
                    body = (Path(__file__).parent / "ui" / name).read_bytes()
                    return self._send(200, body, "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8")
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
