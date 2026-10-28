"""Add-on manager page (v0.8.0: tabs Add-ons, Install, Plugins, Settings, Health, Usage). Ingress only: the server on IH_MANAGER_PORT answers the Supervisor ingress gateway (172.30.32.2) and nobody else.
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
                 health=lambda: None, http=None, usage_path=None, clock=time.time):
        self.catalogue, self.sup, self.text, self.prov, self.seen, self.log, self.ttl = catalogue, sup, text, prov, seen, log, ttl
        self.health_src, self.http = health, http or http_json
        self.jobs, self.busy, self.lock = Jobs(), set(), threading.Lock()
        self.cache = (0, None)
        self.usage_path, self.clock, self.ulock = usage_path or os.environ.get("IH_USAGE", "/data/usage.json"), clock, threading.Lock()
        self.usage, self.usage_t, self.usage_err = self._usage_load(), 0, {}

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
                 "can_act": s != "ih_orchestrator", "used": self.used(s) if i else None}
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
                        "runs": st.get("runs")})
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
        return 200, {"services": svc, "counts": snap.get("counts"), "total": snap.get("total"), "stale": snap.get("stale"), "age_s": snap.get("age_s")}


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Intelligent Home</title>
<style>body{font:15px system-ui;margin:0;padding:0 10px 20px}h2{margin:10px 0 4px}
nav{display:flex;gap:4px;overflow-x:auto;border-bottom:1px solid #8886;position:sticky;top:0;background:Canvas;color:CanvasText;padding:6px 0;z-index:2}
nav a{cursor:pointer;user-select:none;padding:6px 12px;border-radius:14px;text-decoration:none;color:inherit;border:1px solid #8886;white-space:nowrap}nav a.on{background:#1976d2;color:#fff;border-color:#1976d2}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px;margin-top:10px}
.card{border:1px solid #8886;border-left:6px solid #888;border-radius:8px;padding:8px 10px;min-width:0}.card.s-started{border-left-color:#2e7d32}.card.s-stopped,.card.s-error{border-left-color:#c62828}
.card h3{margin:0 0 2px;font-size:16px;word-break:break-all}.d{font-size:12px;opacity:.7}.r{color:#c62828;font-weight:bold}.g{color:#2e7d32}.o{color:#ef6c00;font-weight:bold}
.kv{display:flex;flex-wrap:wrap;gap:2px 12px;margin:4px 0;font-size:13px}.btns{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
button,a.b{padding:6px 10px;border:1px solid #8888;border-radius:6px;background:#8881;color:inherit;font:inherit;text-decoration:none;cursor:pointer}
pre{background:#8882;padding:6px;overflow:auto;max-height:260px;font-size:12px;white-space:pre-wrap;word-break:break-all}
#msg{padding:6px;border:1px solid #8886;margin:6px 0;display:none}table{border-collapse:collapse;width:100%}td,th{padding:4px 6px;text-align:left;border-bottom:1px solid #8884;vertical-align:top}.w{overflow-x:auto}
.chip{display:inline-block;margin:4px 4px 4px 0;padding:4px 12px;border:1px solid #8886;border-radius:14px;cursor:pointer}.chip.on{background:#1976d2;color:#fff}</style>
<h2>Intelligent Home</h2>
<nav id=nav><a data-t=addons>Add-ons</a><a data-t=install>Install</a><a data-t=plugins>Plugins</a><a data-t=settings>Settings</a><a data-t=health>Health</a><a data-t=usage>Usage</a></nav>
<div id=msg></div><div id=main>Loading (the first load after a restart can take about 30 seconds)...</div>
<script>
var B=location.pathname.endsWith('/')?location.pathname:location.pathname+'/';
var TABS=['addons','install','plugins','settings','health','usage'],TAB='addons',FILT='All',OPEN={},DATA=null;
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
function tabAddons(m,d){var c=E('div',null,'cards');d.addons.filter(function(a){return a.installed}).forEach(function(a){
 var k=E('div',null,'card s-'+(a.state||'none'));k.appendChild(E('h3',a.slug));k.appendChild(E('div',a.description,'d'));
 kv(k,[['Version',(a.version||'?')+(a.update?' -> update to '+a.latest:''),a.update?'o':''],['State',a.state||'-',a.state=='started'?'g':'r'],
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
 var k=E('div',null,'card');k.appendChild(E('h3',a.name+' ('+a.slug+')'));k.appendChild(E('div',a.description));
 var w=E('div',null,'kv');a.needs.forEach(function(x){w.appendChild(E('span','needs '+x.slug+': '+(x.status=='ok'?'ok':'missing ('+x.status+')'),x.status=='ok'?'g':'r'))});if(!a.needs.length)w.appendChild(E('span','needs nothing','d'));k.appendChild(w);
 var b=E('div',null,'btns');b.appendChild(btn('Install '+(a.latest||''),function(){act('install',a.slug)}));k.appendChild(b);c.appendChild(k)});
 m.appendChild(n?c:E('p','Everything in the catalogue is installed.'))}
function tabPlugins(m){m.appendChild(E('p','Loading plugins...'));fetch(U('api/plugins')).then(J).then(function(x){m.textContent='';var j=x[1];
 if(x[0]!=200){m.appendChild(E('p',j.error||'failed','r'));return}
 if(!j.installed){m.appendChild(E('p','The ih_jobs add-on is not installed.'));return}
 if(!j.running)m.appendChild(E('p','The jobs add-on did not answer: live details are missing.','o'));
 if(!j.plugins.length)m.appendChild(E('p','No plugins are listed in the ih_jobs configuration.'));
 var c=E('div',null,'cards');j.plugins.forEach(function(p){var k=E('div',null,'card s-'+(p.live?'started':'none'));k.appendChild(E('h3',p.name));
  kv(k,[['Mode',p.live?'Live':'Dry',p.live?'g':'o'],['Running as',p.mode],['Schedule',p.schedule?(p.schedule.kind=='daily'?'daily at '+p.schedule.at:p.schedule.kind=='every'?'every '+p.schedule.minutes+' min':p.schedule.kind):'-'],['Next',T(p.next_run)],['Last run',T(p.last_run)],['Runs',p.runs],['Last error',p.last_error,p.last_error?'r':''],
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
 w.appendChild(t);m.appendChild(w)})}
function tabUsage(m){m.appendChild(E('p','Loading usage...'));fetch(U('api/usage')).then(J).then(function(x){m.textContent='';var j=x[1];
 if(x[0]!=200){m.appendChild(E('p',j.error||'failed','r'));return}
 m.appendChild(E('p','Invocations per day, last 7 days (oldest left, today right). Counted from the recent log of each add-on and saved, so older days are kept. Add-ons not used in 7 days are highlighted.','d'));
 var mx=1;j.addons.forEach(function(a){a.per_day.forEach(function(n){if(n>mx)mx=n})});
 var w=E('div',null,'w'),t=E('table'),h=E('tr');['Add-on','7 days','Total','Failed','Last'].forEach(function(z){h.appendChild(E('th',z))});t.appendChild(h);
 j.addons.forEach(function(a){var r=E('tr'),dead=a.total==0;if(dead)r.style.background='#ef6c0030';
  r.appendChild(E('td',a.slug+(a.state=='started'?'':' ('+(a.state||'stopped')+')')+(a.unreadable?' (log unreadable)':'')));
  var c=E('td'),bars=E('span');bars.style.cssText='display:inline-flex;align-items:flex-end;gap:2px;height:22px';
  a.per_day.forEach(function(n,i){var b=E('span');b.title=j.days[i]+': '+n;b.style.cssText='display:inline-block;width:8px;background:'+(n?'#1976d2':'#8884')+';height:'+(n?Math.max(2,Math.round(22*n/mx)):2)+'px';bars.appendChild(b)});c.appendChild(bars);r.appendChild(c);
  r.appendChild(E('td',dead?'not used in 7 days':String(a.total),dead?'o':''));r.appendChild(E('td',String(a.failed),a.failed?'r':''));
  r.appendChild(E('td',a.last?(a.last+(a.last_day?' '+a.last_day.slice(5):'')):'-'));t.appendChild(r)});
 w.appendChild(t);m.appendChild(w)})}
function show(){var m=document.getElementById('main');
 Array.prototype.forEach.call(document.querySelectorAll('nav a'),function(a){a.className=a.dataset.t==TAB?'on':''});
 if(TAB=='plugins'){m.textContent='';tabPlugins(m);return}
 if(TAB=='settings'){m.textContent='';tabSettings(m);return}
 if(TAB=='health'){m.textContent='';tabHealth(m);return}
 if(TAB=='usage'){m.textContent='';tabUsage(m);return}
 fetch(U('api/addons')).then(function(r){return r.json()}).then(function(d){
  if(d.error){say(d.error);return}
  var keep=m.querySelectorAll('pre').length;if(keep&&TAB=='addons'&&DATA)return;
  m.textContent='';DATA=d;(TAB=='install'?tabInstall:tabAddons)(m,d)}).catch(function(e){say('error: '+e)})}
/* Tabs switch in JS: no hash links (the HA phone app treats link taps in the panel frame as navigation). #name on first load still works. */
if(TABS.indexOf(location.hash.slice(1))>=0)TAB=location.hash.slice(1);
Array.prototype.forEach.call(document.querySelectorAll('nav a'),function(a){a.setAttribute('role','button');a.onclick=function(){TAB=a.dataset.t;DATA=null;say('');show()}});show();
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
