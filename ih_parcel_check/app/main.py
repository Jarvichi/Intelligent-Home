"""ih_parcel_check: dry-mode parcel verdicts for saved doorstep pictures, asked of the Intelligent Home AI layer (stdlib only)."""
import base64, json, os, re, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ihlib import db as ihdb

PROMPT = "Is there a parcel, package or cardboard box on the ground or doorstep in this picture? Answer only yes or no."
STATE = {"seen": 0, "last": None, "ai_layer_ok": None, "dry": True, "checks": {}}
VERSION = "0.4.2"
PORT = int(os.environ.get("PC_PORT", "8816"))
UI_PORT = int(os.environ.get("PC_UI_PORT", "8099"))
INGRESS_PEER = os.environ.get("PC_UI_PEER", "172.30.32.2")  # Supervisor ingress gateway ('' = allow all, tests only)
UI_ROOT = Path(HERE) / "ui"
ASSETS = {"/": "index.html", "/index.html": "index.html", "/ui.js": "ui.js", "/ui.css": "ui.css", "/ih-ui.js": "ih-ui.js",
          "/ih-ui.css": "ih-ui.css", "/ih-patterns.css": "ih-patterns.css"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
KEEP_ERRORS = 50
RECENT = 20
AGENT = {"v": None}      # which AI answered the last ask() (the AI layer reply carries 'agent')
DBH = {"db": None}
MIGRATIONS = [
    (1, "CREATE TABLE verdicts(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, check_name TEXT NOT NULL, camera TEXT NOT NULL, "
        "verdict TEXT NOT NULL, ai TEXT NOT NULL, answer TEXT NOT NULL, seconds REAL NOT NULL)"),
    (2, "CREATE INDEX verdicts_ts ON verdicts(ts)"),
    (3, "CREATE TABLE errors(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, text TEXT NOT NULL)"),
]


class QuietServer(ThreadingHTTPServer):
    """One thread per connection: the ingress gateway keeps idle connections open, which froze a single-threaded server."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def camera_of(file, cams=()):
    """Camera name = the picture file's prefix (never the file name itself)."""
    for c in sorted(cams, key=len, reverse=True):
        if file.startswith(c + "_"):
            return c
    m = re.match(r"^(.*?)_\d", file)
    return (m.group(1) if m else file.rsplit(".", 1)[0])[:40]


def open_db(path="/data/ih.db"):
    d = ihdb.open_db(path, MIGRATIONS)
    DBH["db"] = d
    return d


def db_add_verdict(rec, cams=(), ai=""):
    d = DBH["db"]
    if d is None:
        return
    try:
        d.execute("INSERT INTO verdicts(ts, check_name, camera, verdict, ai, answer, seconds) VALUES (?,?,?,?,?,?,?)",
                  (int(rec["time"]), rec.get("check") or "parcel", camera_of(rec.get("file", ""), cams), rec["verdict"],
                   ai or "", str(rec.get("raw", ""))[:200], float(rec.get("seconds") or 0)))
    except Exception:
        pass  # the page is a convenience: a database problem never stops the scan


def db_add_error(text):
    d = DBH["db"]
    if d is None or not text:
        return
    text = str(text)[:200]
    try:
        with d.tx() as con:
            last = con.execute("SELECT ts, text FROM errors ORDER BY id DESC LIMIT 1").fetchone()
            if last and last[1] == text and time.time() - last[0] < 600:
                return  # same error again within 10 minutes: one row is enough
            con.execute("INSERT INTO errors(ts, text) VALUES (?,?)", (int(time.time()), text))
            con.execute("DELETE FROM errors WHERE id NOT IN (SELECT id FROM errors ORDER BY id DESC LIMIT ?)", (KEEP_ERRORS,))
    except Exception:
        pass


def set_error(text):
    STATE["last_error"] = text
    if text:
        db_add_error(text)


def import_verdicts(log_path, cams=()):
    """First start only (empty table): bring in the old verdicts.jsonl. The file itself is left untouched."""
    d = DBH["db"]
    if d is None or d.query("SELECT 1 FROM verdicts LIMIT 1"):
        return 0
    n = 0
    try:
        with open(log_path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    db_add_verdict(r, cams)
                    n += 1
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return n


def day_start(now=None):
    t = time.localtime(now or time.time())
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def _counts(since):
    d = DBH["db"]
    c = {"checked": 0, "parcel": 0, "no_parcel": 0, "unsure": 0, "errors": 0}
    for verdict, n in d.query("SELECT verdict, COUNT(*) FROM verdicts WHERE ts >= ? GROUP BY verdict", (since,)):
        c["checked"] += n
        key = {"yes": "parcel", "no": "no_parcel"}.get(verdict, "unsure")
        c[key] += n
    c["errors"] = d.query("SELECT COUNT(*) FROM errors WHERE ts >= ?", (since,))[0][0]
    return c


LABELS = {"yes": "Parcel", "no": "No parcel", "unsure": "Unsure"}


def data_snapshot(now=None):
    """Everything the page shows. No pictures, no file names, no tokens."""
    now = int(now or time.time())
    out = {"version": VERSION, "mode": "DRY" if STATE.get("dry", True) else "live", "ai_layer_ok": STATE.get("ai_layer_ok"),
           "last_check": None, "today": None, "week": None, "verdicts": [], "errors": []}
    d = DBH["db"]
    if d is None:
        return out
    out["today"], out["week"] = _counts(day_start(now)), _counts(now - 7 * 86400)
    row = d.query("SELECT MAX(ts) FROM verdicts")[0][0]
    out["last_check"] = row
    for ts, chk, cam, v, ai, ans in d.query("SELECT ts, check_name, camera, verdict, ai, answer FROM verdicts ORDER BY ts DESC, id DESC LIMIT ?", (RECENT,)):
        out["verdicts"].append({"time": ts, "check": chk, "camera": cam, "verdict": v,
                                "label": LABELS.get(v, "Unsure") if chk == "parcel" else {"yes": "Yes", "no": "No"}.get(v, "Unsure"),
                                "answer": ans[:80], "ai": ai or "AI layer"})
    out["errors"] = [{"time": ts, "text": t} for ts, t in d.query("SELECT ts, text FROM errors ORDER BY id DESC LIMIT 10")]
    return out


def load_options(path="/data/options.json"):
    try:
        with open(path) as f:
            return json.load(f)
    except OSError:
        return {}


def parse_verdict(answer):
    a = (answer or "").strip().lower()
    if a.startswith("yes"):
        return "yes"
    if a.startswith("no"):
        return "no"
    return "unsure"


def ask(opts, image_bytes, name="x", question=PROMPT):
    """One picture + the question to the AI layer (capability 'vision'). The layer picks the agent. Raises on any failure."""
    rid = "pc-" + "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)[:50] + "-%d" % int(time.time())
    body = json.dumps({"text": question, "capability": "vision", "request_id": rid, "caller": "ih_parcel_check",
                       "image": base64.b64encode(image_bytes).decode()}).encode()
    req = urllib.request.Request(opts["ai_layer_url"].rstrip("/") + "/request", body,
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + opts["api_token"]})
    with urllib.request.urlopen(req, timeout=opts["timeout_seconds"]) as r:
        obj = json.load(r)
    if not obj.get("ok"):
        raise RuntimeError(str(obj.get("answer") or obj.get("error") or "AI layer said no")[:150])
    ag = re.sub(r"^ih_ai_layer_", "", str(obj.get("agent") or ""))
    AGENT["v"] = re.sub(r"[^A-Za-z0-9_.-]", "", ag)[:40] or None
    return obj.get("answer", "")


def layer_ready(opts):
    """True when the AI layer answers /healthz and a token is set."""
    if not opts.get("api_token"):
        STATE["ai_layer_ok"] = False; set_error("api_token is empty (set it, or let the settings hub fill it)")
        return False
    try:
        with urllib.request.urlopen(opts["ai_layer_url"].rstrip("/") + "/healthz", timeout=10) as r:
            ok = r.read(10).strip() == b"ok"
    except Exception as e:
        STATE["ai_layer_ok"] = False; set_error("AI layer not reachable: " + str(e)[:150])
        return False
    STATE["ai_layer_ok"] = ok
    set_error(None if ok else "AI layer did not answer ok")
    return ok


def wanted(name, cams):
    return name.lower().endswith(".jpg") and any(name.startswith(c + "_") for c in cams)


def get_checks(opts):
    """The list of checks. No 'checks' option = the single parcel check on 'cameras' (0.2.0 behaviour)."""
    out, seen = [], set()
    for c in opts.get("checks") or []:
        name = str(c.get("name", "")).strip()[:40]
        q, cams = str(c.get("question", "")).strip()[:300], str(c.get("cameras", "")).strip()
        if not name or not q or not cams or name in seen:
            continue  # invalid or duplicate: skipped, never fatal
        seen.add(name)
        out.append({"name": name, "question": q, "cams": [x.strip() for x in cams.split(",") if x.strip()],
                    "yes_means": str(c.get("yes_means", ""))[:80]})
    if not out:
        out = [{"name": "parcel", "question": PROMPT, "yes_means": "parcel on the doorstep",
                "cams": [x.strip() for x in opts["cameras"].split(",") if x.strip()]}]
    return out


def scan(opts, done, log_path):
    if not layer_ready(opts):
        return
    try:
        names = sorted(os.listdir(opts["snapshot_dir"]))
    except OSError:
        return
    for chk in get_checks(opts):
        for n in names:
            key = chk["name"] + "|" + n
            if key in done or not wanted(n, chk["cams"]):
                continue
            p = os.path.join(opts["snapshot_dir"], n)
            if time.time() - os.path.getmtime(p) < opts["recheck_after_seconds"]:
                continue  # wait: the scene settles after the visitor leaves
            t0 = time.time()
            AGENT["v"] = None
            try:
                with open(p, "rb") as f:
                    ans = ask(opts, f.read(), n, chk["question"])
                STATE["ai_layer_ok"] = True
            except Exception as e:  # AI layer down, no vision agent, or a spend cap: retry next round
                STATE["ai_layer_ok"] = False
                set_error(str(e)[:200])
                return
            rec = {"check": chk["name"], "file": n, "time": int(time.time()), "verdict": parse_verdict(ans),
                   "raw": ans[:200], "seconds": round(time.time() - t0, 1)}
            with open(log_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            db_add_verdict(rec, chk["cams"], AGENT["v"] or "")
            done.add(key)
            STATE["seen"] = len(done)
            STATE["last"] = rec
            STATE["checks"][chk["name"]] = rec  # latest verdict per check, shown on /status
            # dry mode: nothing is posted. Live posting is not implemented.


def make_handler(ui):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass  # page text and paths are never logged

        def _send(self, code, body, typ="application/json; charset=utf-8"):
            body = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if not ui:  # the original API on 8816, unchanged
                return self._send(200, b"ok" if path == "/healthz" else json.dumps(STATE).encode(), "text/plain" if path == "/healthz" else "application/json")
            if INGRESS_PEER and self.client_address[0] != INGRESS_PEER:
                return self.send_error(403)
            if path == "/api/data":
                return self._send(200, data_snapshot())
            if path == "/ui.json":
                try:
                    info = json.loads((UI_ROOT / "ui.json").read_text())
                except (OSError, ValueError):
                    info = {}
                return self._send(200, {k: info.get(k) for k in ("title", "purpose", "description", "navigation", "access")})
            name = ASSETS.get(path)
            if not name:
                return self.send_error(404)
            try:
                body = (UI_ROOT / name).read_bytes()
            except OSError:
                return self.send_error(404)
            self._send(200, body, TYPES[Path(name).suffix])
    return H


def main():
    opts = load_options()
    defaults = {"dry": True, "snapshot_dir": "/media/eufy_snapshots", "cameras": "front_door,side_door", "checks": [],
                "ai_layer_url": "http://172.30.32.1:8801", "api_token": "",
                "poll_seconds": 30, "recheck_after_seconds": 120, "timeout_seconds": 300}
    opts = {**defaults, **opts}
    STATE["dry"] = True  # 0.1.0 is dry whatever the option says
    log_path = "/data/verdicts.jsonl"
    done = set()
    try:
        with open(log_path) as f:
            done = {(r.get("check") or "parcel") + "|" + r["file"] for r in map(json.loads, (l for l in f if l.strip()))}
    except OSError:
        pass
    try:
        open_db()
        import_verdicts(log_path, [x.strip() for x in opts["cameras"].split(",") if x.strip()])
    except Exception as e:  # no database: the scan and the old API still work, the page shows empty
        STATE["last_error"] = "database not available: " + type(e).__name__
    for port, ui in ((PORT, False), (UI_PORT, True)):
        threading.Thread(target=QuietServer(("0.0.0.0", port), make_handler(ui)).serve_forever, daemon=True).start()
    while True:
        scan(opts, done, log_path)
        time.sleep(opts["poll_seconds"])


if __name__ == "__main__":
    main()
