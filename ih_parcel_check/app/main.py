"""ih_parcel_check: dry-mode parcel verdicts for saved doorstep pictures (stdlib only)."""
import base64, json, os, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PROMPT = "Is there a parcel, package or cardboard box on the ground or doorstep in this picture? Answer only yes or no."
STATE = {"seen": 0, "last": None, "ollama_ok": None, "dry": True}


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


def ask(opts, image_bytes):
    body = json.dumps({"model": opts["model"], "prompt": PROMPT, "stream": False,
                       "images": [base64.b64encode(image_bytes).decode()],
                       "keep_alive": "30s"}).encode()
    req = urllib.request.Request(opts["ollama_url"].rstrip("/") + "/api/generate", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=opts["timeout_seconds"]) as r:
        return json.load(r).get("response", "")


def model_ready(opts):
    """True when the Ollama add-on answers and already has the model (it downloads models in the background after it starts)."""
    try:
        with urllib.request.urlopen(opts["ollama_url"].rstrip("/") + "/api/tags", timeout=10) as r:
            have = [m.get("name", "") for m in json.load(r).get("models", [])]
    except Exception as e:
        STATE["ollama_ok"] = False; STATE["last_error"] = "engine not reachable: " + str(e)[:150]
        return False
    ok = any(n == opts["model"] or n.split(":")[0] == opts["model"] for n in have)
    STATE["ollama_ok"] = ok
    STATE["last_error"] = None if ok else "model %s is not downloaded yet (check the Intelligent Home - Ollama add-on log)" % opts["model"]
    return ok


def wanted(name, cams):
    return name.lower().endswith(".jpg") and any(name.startswith(c + "_") for c in cams)


def scan(opts, done, log_path):
    cams = [c.strip() for c in opts["cameras"].split(",") if c.strip()]
    if not model_ready(opts):
        return
    try:
        names = sorted(os.listdir(opts["snapshot_dir"]))
    except OSError:
        return
    for n in names:
        if n in done or not wanted(n, cams):
            continue
        p = os.path.join(opts["snapshot_dir"], n)
        if time.time() - os.path.getmtime(p) < opts["recheck_after_seconds"]:
            continue  # wait: the parcel shows only after the courier leaves
        t0 = time.time()
        try:
            with open(p, "rb") as f:
                ans = ask(opts, f.read())
            STATE["ollama_ok"] = True
        except Exception as e:  # Ollama down or busy: retry next round
            STATE["ollama_ok"] = False
            STATE["last_error"] = str(e)[:200]
            return
        rec = {"file": n, "time": int(time.time()), "verdict": parse_verdict(ans),
               "raw": ans[:200], "seconds": round(time.time() - t0, 1)}
        with open(log_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        done.add(n)
        STATE["seen"] = len(done)
        STATE["last"] = rec
        # dry mode: nothing is posted. Live posting is not implemented in 0.1.0.


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
    defaults = {"dry": True, "snapshot_dir": "/media/eufy_snapshots", "cameras": "front_door,side_door",
                "ollama_url": "http://32c8114c-ih-ollama:11434", "model": "moondream",
                "poll_seconds": 30, "recheck_after_seconds": 120, "timeout_seconds": 300}
    opts = {**defaults, **opts}
    STATE["dry"] = True  # 0.1.0 is dry whatever the option says
    log_path = "/data/verdicts.jsonl"
    done = set()
    try:
        with open(log_path) as f:
            done = {json.loads(l)["file"] for l in f if l.strip()}
    except OSError:
        pass
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", 8798), H).serve_forever(), daemon=True).start()
    while True:
        scan(opts, done, log_path)
        time.sleep(opts["poll_seconds"])


if __name__ == "__main__":
    main()
