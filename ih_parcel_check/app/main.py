"""ih_parcel_check: dry-mode parcel verdicts for saved doorstep pictures, asked of the Intelligent Home AI layer (stdlib only)."""
import base64, json, os, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PROMPT = "Is there a parcel, package or cardboard box on the ground or doorstep in this picture? Answer only yes or no."
STATE = {"seen": 0, "last": None, "ai_layer_ok": None, "dry": True, "checks": {}}


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
    return obj.get("answer", "")


def layer_ready(opts):
    """True when the AI layer answers /healthz and a token is set."""
    if not opts.get("api_token"):
        STATE["ai_layer_ok"] = False; STATE["last_error"] = "api_token is empty (set it, or let the settings hub fill it)"
        return False
    try:
        with urllib.request.urlopen(opts["ai_layer_url"].rstrip("/") + "/healthz", timeout=10) as r:
            ok = r.read(10).strip() == b"ok"
    except Exception as e:
        STATE["ai_layer_ok"] = False; STATE["last_error"] = "AI layer not reachable: " + str(e)[:150]
        return False
    STATE["ai_layer_ok"] = ok
    STATE["last_error"] = None if ok else "AI layer did not answer ok"
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
            try:
                with open(p, "rb") as f:
                    ans = ask(opts, f.read(), n, chk["question"])
                STATE["ai_layer_ok"] = True
            except Exception as e:  # AI layer down, no vision agent, or a spend cap: retry next round
                STATE["ai_layer_ok"] = False
                STATE["last_error"] = str(e)[:200]
                return
            rec = {"check": chk["name"], "file": n, "time": int(time.time()), "verdict": parse_verdict(ans),
                   "raw": ans[:200], "seconds": round(time.time() - t0, 1)}
            with open(log_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            done.add(key)
            STATE["seen"] = len(done)
            STATE["last"] = rec
            STATE["checks"][chk["name"]] = rec  # latest verdict per check, shown on /status
            # dry mode: nothing is posted. Live posting is not implemented.


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        out = b"ok" if self.path == "/healthz" else json.dumps(STATE).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


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
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", 8816), H).serve_forever(), daemon=True).start()
    while True:
        scan(opts, done, log_path)
        time.sleep(opts["poll_seconds"])


if __name__ == "__main__":
    main()
