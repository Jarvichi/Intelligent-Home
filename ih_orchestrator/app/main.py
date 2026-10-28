#!/usr/bin/env python3
"""Intelligent Home orchestrator, stages 0-2: STATUS ONLY + (v0.4.0) optional MQTT scheduler (v0.4.1: every scheduler message is HMAC-signed, option module_keys) (off by default, enable_scheduler). stdlib only (paho-mqtt only for the scheduler). v0.3.3: supervisor_addon check type is host-only. v0.3.2: each service shows a description. v0.3.0: data is PUSHED by the host (POST /ingest, bearer token); the add-on maps no folders. Secrets broker parked (enable_broker, off). No brain; the only outbound target is the MQTT host option when the scheduler is on.
Reads manifests (IH_MANIFESTS), runs the checks a container can run (http, file_age, file_exists), takes host-only checks
(tmux) from the host probe's status files (IH_STATUS), serves /status (JSON), /sensor (flat JSON for a HA REST sensor), / (HTML)."""
import html, json, os, sys, time, glob, hmac, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import healthlib, scheduler, provisioner, manager
from urllib.parse import urlparse, parse_qs

MANIFESTS = os.environ.get("IH_MANIFESTS", "")  # only for --once on the host; the add-on maps no folders
STATUS = os.environ.get("IH_STATUS", "")
SECRETS = os.environ.get("IH_SECRETS", "/data/secrets.yaml")  # used only when the broker is enabled (parked)
DATA = os.environ.get("IH_DATA", "/data")
OPTIONS = os.environ.get("IH_OPTIONS", "/data/options.json")
BROKER = None
ENABLE_BROKER = False
SCHED = None
SCHED_REASON = "not started"
TOKEN = ""
PROV = None   # provisioner.Provisioner when option provision is true
LOG_PAYLOADS = False   # option log_payloads: invocation lines also hold 200 chars of the request and reply (never for /admin)
LOG = lambda m: print(m, flush=True)
PORT = int(os.environ.get("IH_PORT", "8790"))
MANAGER_PORT = int(os.environ.get("IH_MANAGER_PORT", "8789"))   # ingress only (manager.py refuses every client but the Supervisor gateway)
CATALOGUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalogue.json")
PROBE_MAX_AGE = 180  # host probe results older than this count as unknown
SNAPSHOT_MAX_AGE = 180  # a pushed snapshot older than this is stale
MAX_BODY = 1024 * 1024
MAX_ITEMS = 500
STATE = {"manifests": [], "status": [], "received_at": None, "extra": {}}  # extra: pushes from modules, by source name
LOCK = threading.Lock()


def _snip(t, n=200):
    """First n chars as ONE line: backslash, quote and newlines escaped. Only used when option log_payloads is true."""
    t = str(t or "")[:n]
    return t.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")


def _tag(v, default="-"):
    """A short safe token for a log line (letters, digits and . _ : - only)."""
    t = "".join(c if (c.isalnum() and c.isascii()) or c in "._:/-" else "_" for c in str(v if v is not None else default))[:64]
    return t or default


def inv(src, what, n_in, n_out, t0, ok, why="-", req="", ans="", rid="-"):
    """ONE line per real invocation (/register, /admin, /pause): id, source, what, chars in/out, ms, ok, why. Text only when option log_payloads is true."""
    try:
        f = ["invoke", "id=" + _tag(rid), "src=" + _tag(src), "what=" + _tag(what), "in=%d" % n_in, "out=%d" % n_out, "ms=%d" % int((time.time() - t0) * 1000),
             "ok=%s" % bool(ok), "why=" + _tag(why)]
        if LOG_PAYLOADS and (req or ans):
            f += ['req="%s"' % _snip(req), 'ans="%s"' % _snip(ans)]
        LOG(" ".join(f))
    except Exception:
        pass


def load_manifests(d=MANIFESTS):
    out = []
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            out.append(json.load(open(p)))
        except Exception:
            out.append({"name": os.path.basename(p)[:-5], "health": [], "_bad": True})
    return out


def load_status(d=STATUS):
    out = []
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            out.append(json.load(open(p)))
        except Exception:
            pass
    return out


def host_result(name, idx, status_map, now):
    """The pushed host result for check number idx of service name, or unknown if missing/stale."""
    try:
        d = status_map[name]
        if now - d["probed_at"] > PROBE_MAX_AGE:
            return {"type": "host", "ok": None, "detail": "host probe stale"}
        return d["checks"][idx]
    except Exception:
        return {"type": "host", "ok": None, "detail": "no host probe result"}


def evaluate(m, status_map, now=None):
    now = time.time() if now is None else now
    if m.get("_bad"):
        return {"name": m["name"], "state": "FAIL", "checks": [{"type": "manifest", "ok": False, "detail": "bad manifest"}]}
    results = []
    for i, c in enumerate(m.get("health", [])):
        if c.get("type") in healthlib.HOST_ONLY:
            results.append(host_result(m["name"], i, status_map, now))
        else:
            results.append(healthlib.run_check(c, host=False, now=now))
    return {"name": m["name"], "state": healthlib.combine(results), "checks": results}


def build_snapshot(manifests, status, received_at=None, now=None):
    now = time.time() if now is None else now
    smap = {s["name"]: s for s in status if isinstance(s, dict) and isinstance(s.get("name"), str)}
    rows = []
    for m in manifests:
        r = evaluate(m, smap, now)
        r.update({"kind": m.get("kind"), "version": m.get("version"), "port": m.get("port"),
                  "description": str(m.get("description") or "")[:300],
                  "declares_secrets": m.get("declares_secrets", []), "restore": m.get("restore", "")})
        rows.append(r)
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in ("OK", "FAIL", "UNKNOWN")}
    age = None if received_at is None else now - received_at
    stale = age is None or age > SNAPSHOT_MAX_AGE
    return {"generated_at": now, "received_at": received_at, "age_s": None if age is None else int(age), "stale": stale,
            "total": len(rows), "counts": counts, "services": rows}


def snapshot(manifests=None, status=None, now=None):
    """Snapshot of the pushed data (no args), or of two folders on a host (tests, --once)."""
    if manifests is not None:
        n = time.time() if now is None else now
        lm = load_manifests(manifests)
        snap = build_snapshot(lm, load_status(status), n, n)
        snap["schedule"] = scheduler.off_status(lm, "host --once", n)
        return snap
    with LOCK:
        m, s, r = list(STATE["manifests"]), list(STATE["status"]), STATE["received_at"]
        extra = dict(STATE.get("extra") or {})
    m, s = merge_extra(m, s, extra)
    snap = build_snapshot(m, s, r, now)
    snap["schedule"] = SCHED.status(now) if SCHED else scheduler.off_status(m, SCHED_REASON, now)
    snap["settings"] = PROV.get_status() if PROV else None   # names only, never values
    return snap


def all_manifests():
    with LOCK:
        m, s, r = list(STATE["manifests"]), list(STATE["status"]), STATE["received_at"]
        extra = dict(STATE.get("extra") or {})
    return merge_extra(m, s, extra)[0]


def merge_extra(m, s, extra):
    names = {x["name"] for x in m}  # the host list wins on a name clash
    for src in sorted(extra):
        e = extra[src]
        m += [x for x in e["manifests"] if x["name"] not in names]
        s += [x for x in e["status"] if x["name"] not in names]
        names |= {x["name"] for x in e["manifests"]}
    return m, s


def save_state(data_dir=None):
    d = data_dir or DATA
    try:
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, ".snapshot.tmp")
        with open(tmp, "w") as f:
            json.dump(STATE, f)
        os.replace(tmp, os.path.join(d, "snapshot.json"))
        return True
    except OSError:
        return False


def load_state(data_dir=None):
    p = os.path.join(data_dir or DATA, "snapshot.json")
    try:
        d = json.load(open(p))
        if isinstance(d.get("manifests"), list) and isinstance(d.get("status"), list):
            with LOCK:
                STATE.update(manifests=d["manifests"], status=d["status"], received_at=d.get("received_at"),
                             extra=d.get("extra") if isinstance(d.get("extra"), dict) else {})
            return True
    except Exception:
        pass
    return False


def ingest(body, now=None):
    """Validate and store a pushed snapshot. Returns (code, obj)."""
    now = time.time() if now is None else now
    try:
        req = json.loads(body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return 400, {"error": "bad json"}
    if not isinstance(req, dict):
        return 400, {"error": "bad json"}
    m, s = req.get("manifests"), req.get("status")
    ok = (isinstance(m, list) and isinstance(s, list) and len(m) <= MAX_ITEMS and len(s) <= MAX_ITEMS
          and all(isinstance(x, dict) and isinstance(x.get("name"), str) and isinstance(x.get("health", []), list) for x in m)
          and all(isinstance(x, dict) and isinstance(x.get("name"), str) for x in s))
    if not ok:
        return 400, {"error": "need manifests[] and status[] of objects with a name"}
    src = req.get("source")
    if src is not None and not (isinstance(src, str) and 0 < len(src) <= 40):
        return 400, {"error": "bad source"}
    with LOCK:
        if src:  # a module pushes only its own entries; the host snapshot is not touched
            STATE.setdefault("extra", {})[src] = {"manifests": m, "status": s, "received_at": now}
        else:
            STATE.update(manifests=m, status=s, received_at=now)
    save_state()
    return 200, {"ok": True, "manifests": len(m), "status": len(s)}


def token_ok(header):
    if not TOKEN:
        return False
    h = header or ""
    if not h.startswith("Bearer "):
        return False
    return hmac.compare_digest(h[7:].encode(), TOKEN.encode())


def init_broker():
    """Parked feature: imports broker.py only when enable_broker is true."""
    global BROKER
    import broker
    BROKER = broker.Broker(SECRETS, DATA, log=lambda m: print(m, flush=True))
    return BROKER


def init_scheduler(o):
    """Start the scheduler when enabled and configured. Never raises; failure leaves it off with a reason."""
    global SCHED, SCHED_REASON
    try:
        s, why = scheduler.build(o, DATA, log=lambda m: print(m, flush=True))
        if s is None:
            SCHED, SCHED_REASON = None, why; return
        s.sync(all_manifests())
        s.bus.start()
        threading.Thread(target=s.run, args=(all_manifests,), daemon=True).start()
        SCHED, SCHED_REASON = s, ""
    except Exception as e:
        SCHED, SCHED_REASON = None, "scheduler failed to start: %s" % type(e).__name__


def init_provisioner(o):
    """Start the settings hub timer thread when option provision is true. Never raises."""
    global PROV
    if o.get("provision") is not True:
        PROV = None; return
    try:
        PROV = provisioner.Provisioner(DATA, log=lambda m: print(m, flush=True), options=lambda: getattr(load_options, "last", {}))
        threading.Thread(target=provisioner.loop, args=(PROV, lambda: getattr(load_options, "last", {}).get("provision_interval_min", 10)), daemon=True).start()
    except Exception as e:
        PROV = None
        print("provisioner failed to start:", type(e).__name__, flush=True)


def seen_map(now=None):
    """{manifest name: {state, age_s}}: health state plus the age of the push (host or module source) that carried it."""
    now = time.time() if now is None else now
    with LOCK:
        pushed = {m["name"]: STATE["received_at"] for m in STATE["manifests"]}
        for e in (STATE.get("extra") or {}).values():
            for m in e["manifests"]:
                pushed.setdefault(m["name"], e.get("received_at"))
    states = {r["name"]: r["state"] for r in snapshot(now=now)["services"]}
    return {n: {"state": states.get(n, "UNKNOWN"), "age_s": None if t is None else int(now - t)} for n, t in pushed.items()}


def init_manager():
    """Start the ingress-only manager page server on its own port. Never raises."""
    try:
        mgr = manager.Manager(manager.load_catalogue(CATALOGUE), prov=lambda: PROV, seen=seen_map, log=lambda m: print(m, flush=True))
        threading.Thread(target=mgr.warm, daemon=True).start()
        threading.Thread(target=manager.serve, args=(mgr, MANAGER_PORT), daemon=True).start()
        return mgr
    except Exception as e:
        print("manager failed to start:", type(e).__name__, flush=True)


def load_options(path=None):
    global TOKEN, ENABLE_BROKER, LOG_PAYLOADS
    try:
        o = json.load(open(path or OPTIONS))
    except Exception:
        o = {}
    load_options.last = o
    TOKEN = str(o.get("ingest_token") or "").strip()
    ENABLE_BROKER = bool(o.get("enable_broker", False))
    LOG_PAYLOADS = o.get("log_payloads") is True


def sensor(snap):
    bad = [r["name"] for r in snap["services"] if r["state"] != "OK"]
    return {"state": "%d/%d OK" % (snap["counts"]["OK"], snap["total"]), "ok": snap["counts"]["OK"], "fail": snap["counts"]["FAIL"],
            "unknown": snap["counts"]["UNKNOWN"], "total": snap["total"], "problems": ", ".join(bad) or "none",
            "stale": snap["stale"], "age_s": snap["age_s"],
            "updated": time.strftime("%H:%M:%S", time.localtime(snap["received_at"])) if snap["received_at"] else "never"}


COL = {"OK": "#2e7d32", "FAIL": "#c62828", "UNKNOWN": "#ef6c00"}


def page(snap):
    rows = "".join(
        "<tr><td>%s<div class=d>%s</div></td><td style='color:%s;font-weight:bold'>%s</td><td>%s</td><td>%s</td></tr>" % (
            html.escape(r["name"]), html.escape(r.get("description") or ""), COL[r["state"]], r["state"], html.escape(r.get("kind") or ""),
            html.escape("; ".join(c["detail"] for c in r["checks"])))
        for r in snap["services"])
    c = snap["counts"]
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta http-equiv=refresh content=30><title>Intelligent Home</title>"
            "<style>body{font:15px system-ui;margin:12px}td,th{padding:4px 8px;text-align:left;border-bottom:1px solid #8884;vertical-align:top}.d{font-size:13px;opacity:.7;font-weight:normal}</style>"
            "<h2>Intelligent Home: %d OK, %d FAIL, %d unknown</h2><table><tr><th>Service<th>State<th>Kind<th>Detail</tr>%s</table>"
            "%s%s<p>Status only. %s</p>" % (c["OK"], c["FAIL"], c["UNKNOWN"], rows, schedule_html(snap.get("schedule")),
                                 provisioner.settings_html(snap.get("settings"), html.escape),
                                 "STALE: no push for %s s." % snap["age_s"] if snap["stale"] and snap["age_s"] is not None
                                 else "STALE: nothing pushed yet." if snap["stale"] else "Pushed %d s ago." % snap["age_s"]))


SCOL = {"ok": "#2e7d32", "missed": "#c62828", "paused": "#ef6c00", "off": "#888", "no key": "#c62828"}


def _ts(t):
    return time.strftime("%d %b %H:%M", time.localtime(t)) if t else "-"


def schedule_html(sc):
    if not sc:
        return ""
    head = "<h3>Schedule: %s%s</h3>" % (html.escape(sc["state"]), " (%s)" % html.escape(sc["reason"]) if sc.get("reason") else "") + ("<p>Rejected messages: %d</p>" % sc["rejected"] if sc.get("rejected") else "")
    if not sc["jobs"]:
        return head + "<p>No jobs declared.</p>"
    rows = "".join("<tr><td>%s / %s<div class=d>%s</div></td><td style='color:%s;font-weight:bold'>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
        html.escape(j["module"]), html.escape(j["job"]), html.escape(j["when"]), SCOL.get(j["state"], "#888"), j["state"],
        _ts(j["next_due"]), _ts(j["last_run"]), html.escape(j.get("detail") or "")) for j in sc["jobs"])
    return head + "<table><tr><th>Job<th>State<th>Next<th>Last run<th>Detail</tr>%s</table>" % rows


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/healthz":
            body, ct = b"ok", "text/plain"
        elif p == "/status":
            body, ct = json.dumps(snapshot()).encode(), "application/json"
        elif p == "/sensor":
            body, ct = json.dumps(sensor(snapshot())).encode(), "application/json"
        elif p == "/provision/status":
            if not TOKEN:
                return self._json(503, {"error": "disabled (no ingest_token)"})
            if not token_ok(self.headers.get("Authorization")):
                return self._json(401, {"error": "unauthorized"})
            if not PROV:
                return self._json(503, {"error": "provision off"})
            return self._json(200, PROV.get_status())
        elif p == "/audit" and BROKER:
            body, ct = "\n".join(BROKER.audit_tail(100)).encode(), "text/plain"
        elif p == "/digest" and BROKER:
            body, ct = json.dumps(BROKER.digest()).encode(), "application/json"
        elif p == "/":
            body, ct = page(snapshot()).encode(), "text/html; charset=utf-8"
        else:
            self.send_error(404); return
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/ingest":
            if not TOKEN:
                return self._json(503, {"error": "ingest disabled (no ingest_token)"})
            if not token_ok(self.headers.get("Authorization")):
                return self._json(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._json(411, {"error": "length required"})
            if n < 0 or n > MAX_BODY:
                return self._json(413, {"error": "too large"})
            return self._json(*ingest(self.rfile.read(n)))
        if p in ("/provision", "/provision/import"):
            if not TOKEN:
                return self._json(503, {"error": "disabled (no ingest_token)"})
            if not token_ok(self.headers.get("Authorization")):
                return self._json(401, {"error": "unauthorized"})
            if not PROV:
                return self._json(503, {"error": "provision off"})
            try:
                return self._json(200, PROV.do_import() if p.endswith("import") else PROV.run())
            except Exception as e:
                return self._json(502, {"error": str(e) if isinstance(e, provisioner.SupError) else type(e).__name__})
        if p == "/pause":
            if not TOKEN:
                return self._json(503, {"error": "disabled (no ingest_token)"})
            if not token_ok(self.headers.get("Authorization")):
                return self._json(401, {"error": "unauthorized"})
            if not SCHED:
                return self._json(503, {"error": "scheduler off"})
            t0 = time.time()
            q = parse_qs(urlparse(self.path).query)
            mod, job, act = (q.get(k, [""])[0] for k in ("module", "job", "action"))
            if act not in ("", "pause", "resume"):
                inv("api", "pause", len(self.path), 0, t0, False, "bad_action", self.path)
                return self._json(400, {"error": "action is pause or resume"})
            if not SCHED.set_paused(mod, job, act != "resume"):
                inv("api", "pause", len(self.path), 0, t0, False, "unknown_job", self.path)
                return self._json(404, {"error": "unknown job"})
            inv("api", "pause:%s/%s" % (mod, job), len(self.path), 0, t0, True, "-", self.path, "paused" if act != "resume" else "resumed")
            return self._json(200, {"ok": True, "module": mod, "job": job, "paused": act != "resume"})
        if not BROKER or p not in ("/register", "/heartbeat", "/admin"):
            return self.send_error(404)
        import broker
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
            if not isinstance(req, dict):
                raise ValueError
        except (ValueError, TypeError):
            return self._json(400, {"error": "bad json"})
        ip = self.client_address[0]
        if not broker.is_local(ip):
            return self._json(403, {"error": "local only"})
        t0 = time.time()
        if p == "/register":
            code, out = BROKER.register(req)
            decl = req.get("declares_secrets") if isinstance(req.get("declares_secrets"), list) else []
            inv(req.get("name") if isinstance(req.get("name"), str) else "?", "register", len(json.dumps(req)), len(json.dumps(out)), t0, code == 200,
                "-" if code == 200 else (out.get("error") if isinstance(out, dict) else "error"), "declares=%s" % ",".join(str(x) for x in decl),
                "http=%d given=%s pending=%s" % (code, ",".join(sorted(out.get("secrets", {}))) if isinstance(out, dict) else "", ",".join(out.get("pending", [])) if isinstance(out, dict) else ""))   # names only, never values or tokens
            return self._json(code, out)
        if p == "/heartbeat":
            return self._json(*BROKER.heartbeat(req.get("module"), req.get("token")))   # a heartbeat is a health ping: not logged
        code, out = BROKER.admin(ip, req)
        inv("owner", "admin:%s" % _tag(req.get("action")), len(json.dumps(req)), len(json.dumps(out)), t0, code == 200,
            "-" if code == 200 else (out.get("error") if isinstance(out, dict) else "error"))   # never the request (code/pin): no payload even when log_payloads is on
        return self._json(code, out)

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if "--once" in sys.argv:
        print(json.dumps(snapshot(MANIFESTS, STATUS), indent=1)); sys.exit(0)
    load_options()
    load_state()
    if ENABLE_BROKER:
        init_broker()
    init_scheduler(load_options.last)
    init_provisioner(load_options.last)
    init_manager()
    print("Intelligent Home status on port", PORT, "| ingest", "on" if TOKEN else "OFF (no ingest_token)", "| broker", "on" if BROKER else "off", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
