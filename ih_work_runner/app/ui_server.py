#!/usr/bin/env python3
"""Read-only status page for the Work Runner (0.2.0). Served on its own ingress port; answers ONLY the Supervisor ingress gateway.
Threaded on purpose: the gateway keeps idle connections open and a single-threaded server froze the page (Themed Events 0.1.1).
It shows: mode, pause reasons, the job running now (TITLE ONLY), the last 20 runs, the pace gate line, the worker lock and failures.
It never returns the Work token, the Claude token, the MCP file or any task text beyond a title. Page text is never logged."""
import json, os, re, sys, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
UI_ROOT = Path(HERE) / "ui"
INGRESS_PEER = "172.30.32.2"
ASSETS = {"/": "index.html", "/index.html": "index.html", "/ui.css": "ui.css", "/ui.js": "ui.js", "/ih-ui.css": "ih-ui.css", "/ih-ui.js": "ih-ui.js",
          "/ih-patterns.css": "ih-patterns.css", "/ui.json": "ui.json"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json"}
WORK_URLS = ["http://172.30.32.1:8802", "http://127.0.0.1:8802", "http://32c8114c-ih-work:8802"]
LOG_TAIL = 96 * 1024
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|[A-Fa-f0-9]{32,}|[A-Za-z0-9_\-]{40,}|ghp_\w+|Bearer\s+\S+)")


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True   # one thread per connection
    """A reset or broken pipe from the gateway is not an error: no traceback."""
    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def scrub(text, n=160):
    """Short plain text with anything that looks like a key removed."""
    return SECRET_RE.sub("[hidden]", str(text or ""))[:n]


def tail_lines(path, limit=LOG_TAIL):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END); size = f.tell(); f.seek(max(0, size - limit))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = data.splitlines()
    return lines[1:] if size > limit and lines else lines


LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*)$")
START = re.compile(r"^task #(\d+) \((plan|replan|build)\): (.*)$")
END = re.compile(r"^(\w+) \S+: task #(\d+): ")
FAIL = re.compile(r"^FAILED \S+(?: \(task #(\d+)\))?: (\w+)")
GATE = re.compile(r"\[DRY\]|gate|pace|session|week|STOP|waits", re.I)


def _epoch(ts):
    try: return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    except ValueError: return None


def parse_log(lines):
    """-> (runs newest first, up to 20; gate line or None). Reads only: task number, mode, TITLE, result word, error class, times."""
    open_runs, runs, gate = {}, [], None
    for raw in lines:
        m = LINE.match(raw)
        if not m: continue
        ts, msg = m.groups()
        s = START.match(msg)
        if s:
            open_runs[s.group(1)] = {"task": int(s.group(1)), "mode": s.group(2), "title": scrub(s.group(3), 70), "at": ts}
            continue
        e = END.match(msg)
        if e and e.group(2) in open_runs:
            r = open_runs.pop(e.group(2)); a, b = _epoch(r["at"]), _epoch(ts)
            r.update(result=e.group(1), seconds=int(b - a) if a and b else None); runs.append(r)
            continue
        f = FAIL.match(msg)
        if f:
            r = open_runs.pop(f.group(1), None) if f.group(1) else None
            r = r or {"task": int(f.group(1)) if f.group(1) else None, "mode": "", "title": "", "at": ts}
            a, b = _epoch(r["at"]), _epoch(ts)
            r.update(result="failed", error=scrub(f.group(2), 40), seconds=int(b - a) if a and b else None); runs.append(r)
            continue
        if GATE.search(msg) and not msg.startswith(("task #", "run item", "queued task", "[DRY] next task")) and "title" not in msg:
            gate = {"at": ts, "text": scrub(msg)}
    return list(reversed(runs))[:20], gate


def lock_info(wq):
    p = os.path.join(wq, ".worker.lock")
    try:
        return {"present": True, "age_s": max(0, int(time.time() - os.stat(p).st_mtime))}
    except OSError:
        return {"present": False, "age_s": None}


def work_get(path, token, urls, timeout=3, opener=urllib.request.urlopen):
    """GET from the Work add-on. Returns parsed JSON or None. The token goes only in the request header."""
    if not token: return None
    for base in urls:
        try:
            req = urllib.request.Request(base.rstrip("/") + path, headers={"Authorization": "Bearer " + token})
            with opener(req, timeout=timeout) as r:
                return json.loads(r.read(262144))
        except Exception:
            continue
    return None


def build_status(opts, state, wq, work_urls=None, fetch=work_get):
    urls = ([opts["work_url"]] if opts.get("work_url") else []) + (work_urls or WORK_URLS)
    tok = opts.get("work_token", "")
    runs, gate = parse_log(tail_lines(os.path.join(wq, "worker.log")))
    w, rn = fetch("/workers", tok, urls), fetch("/runs", tok, urls)
    out = {"mode": "live" if opts.get("mode") == "live" else "dry",
           "live_file": os.path.exists(os.path.join(wq, "LIVE")),
           "paused": state.get("paused") or "",
           "problems": [scrub(p, 120) for p in (state.get("problems") or [])][:10],
           "last_pass": state.get("last_pass"), "last_result": scrub(state.get("last_result"), 120),
           "work_reachable": w is not None, "jobs": [], "pace": None, "run": None,
           "gate": gate, "lock": lock_info(wq), "runs": runs,
           "failures": [r for r in runs if r.get("result") == "failed"][:5]}
    for v in (w or {}).get("workers", [])[:10]:
        if isinstance(v, dict):
            out["jobs"].append({"worker": scrub(v.get("worker"), 40), "stage": scrub(v.get("stage"), 30), "title": scrub(v.get("title"), 100), "ref": scrub(v.get("ref"), 30),
                                "elapsed_s": v.get("elapsed_s") if isinstance(v.get("elapsed_s"), int) else None, "stalled": bool(v.get("stalled"))})
    if isinstance(rn, dict):
        out["pace"] = rn.get("pace") if isinstance(rn.get("pace"), (int, float, str)) else None
        a = rn.get("active")
        if isinstance(a, dict): out["run"] = {"kind": scrub(a.get("kind"), 20), "state": scrub(a.get("state"), 20), "text": scrub(a.get("phase_text"), 120)}
    return out


def make_server(port, get_status, peers=(INGRESS_PEER,), host="0.0.0.0"):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct="application/json"):
            if not isinstance(body, bytes): body = json.dumps(body).encode()
            self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff"); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            if self.client_address[0] not in peers: return self._send(403, {"error": "forbidden"})
            p = urlparse(self.path).path
            while p.startswith("//"): p = p[1:]
            if p == "/api/status":
                try: return self._send(200, get_status())
                except Exception as e: return self._send(500, {"error": type(e).__name__})
            name = ASSETS.get(p)
            if not name or not (UI_ROOT / name).is_file(): return self._send(404, {"error": "not found"})
            self._send(200, (UI_ROOT / name).read_bytes(), TYPES.get(Path(name).suffix, "application/octet-stream"))

        def do_POST(self): self._send(405, {"error": "read-only"})
        do_PUT = do_DELETE = do_PATCH = do_POST

        def log_message(self, *a): pass
    return QuietServer((host, port), H)
