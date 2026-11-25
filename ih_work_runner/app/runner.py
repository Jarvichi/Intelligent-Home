#!/usr/bin/env python3
"""IH Work Runner: the loop that used to start from the host (tmux session workq running queue_worker_loop.sh).
One cycle = one worker pass (queue_worker.py claims a job from the Work add-on, runs it with the Claude CLI, reports on the task),
then a wait that ends early when a job is queued and nothing runs. The worker scripts are NOT copied here: they are read from the mapped
folder (one copy only). This file only supervises: options -> environment, STOP/LIVE switches, one-at-a-time, health page.
Safe by default: mode dry = the worker only looks (no claim). Live needs mode=live AND work_queue/LIVE AND no stop switch."""
import json, os, shutil, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

DATA = os.environ.get("IHWR_DATA", "/data")
PORT = int(os.environ.get("IHWR_PORT", "8847"))
UI_PORT = int(os.environ.get("IHWR_UI_PORT", "8099"))
WQ = os.environ.get("IHWR_WQ", "/homeassistant/claude_scripts/work_queue")
HOME = os.path.join(DATA, "claude-home")
STATE = {"started": time.time(), "last_pass": None, "last_result": "none yet", "mode": "dry", "paused": "", "problems": []}


def read_options(path=None):
    try:
        with open(path or os.path.join(DATA, "options.json")) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}
    return {"mode": "live" if o.get("mode") == "live" else "dry", "stop": bool(o.get("stop")), "work_token": str(o.get("work_token") or ""),
            "claude_oauth_token": str(o.get("claude_oauth_token") or ""), "mcp_config_file": str(o.get("mcp_config_file") or ""),
            "work_url": str(o.get("work_url") or ""), "idle_max_s": int(o.get("idle_max_s") or 300), "idle_check_s": int(o.get("idle_check_s") or 15)}


def build_env(opts, base=None):
    """Environment for the worker. Tokens come only from the add-on options and are never written to a file or a log."""
    env = dict(base if base is not None else os.environ)
    env["HOME"] = HOME
    if opts["work_token"]: env["WORK_TOKEN"] = opts["work_token"]
    if opts["work_url"]: env["WORK_URL"] = opts["work_url"]
    # queue_worker reads /homeassistant/.claudecode/oauth_token itself when that file exists; the option is the fallback
    if opts["claude_oauth_token"]: env["CLAUDE_CODE_OAUTH_TOKEN"] = opts["claude_oauth_token"]
    return env


def pause_reason(opts, wq=None):
    """Why this cycle must not claim anything ('' = fine). Dry is not a pause: the worker runs in its own dry mode."""
    wq = wq or WQ
    if opts["stop"]: return "stop option is on"
    if os.path.exists(os.path.join(wq, "STOP")): return "STOP file present"
    return ""


def is_live(opts, wq=None):
    return opts["mode"] == "live" and os.path.exists(os.path.join(wq or WQ, "LIVE")) and not pause_reason(opts, wq)


def install_mcp_config(opts, home=None):
    """Copy the MCP settings (House Brain, Home Assistant) the owner prepared into the add-on's own HOME. Returns '' or a problem text. Content is never read into logs."""
    home = home or HOME; src = opts["mcp_config_file"]
    if not src: return "no mcp_config_file set: Claude has no House Brain or Home Assistant tools"
    if not os.path.isfile(src): return "mcp_config_file not found"
    os.makedirs(home, exist_ok=True); shutil.copyfile(src, os.path.join(home, ".claude.json")); os.chmod(os.path.join(home, ".claude.json"), 0o600)
    return ""


def preflight(opts, wq=None, which=shutil.which):
    """List of plain problems found at start (none = ready)."""
    wq = wq or WQ; p = []
    for f in ("queue_worker.py", "task_flow.py", "tasks_api.py", "work_queue.py", "wait_for_work.py"):
        if not os.path.isfile(os.path.join(wq, f)): p.append(f"missing {f} in the mapped work_queue folder")
    for b in ("claude", "git", "gh", "python3"):
        if not which(b): p.append(f"{b} not found in the image")
    if not opts["work_token"]: p.append("work_token is empty: the Work add-on API cannot be reached")
    if not (os.path.isfile("/homeassistant/.claudecode/oauth_token") or opts["claude_oauth_token"]): p.append("no Claude token (file or option)")
    return p


def run_pass(opts, wq=None, run=subprocess.run):
    """One worker pass. Returns a short result text. The worker keeps its own lock (.worker.lock), so a second runner (e.g. the old tmux loop) is refused there."""
    wq = wq or WQ; live = is_live(opts, wq)
    args = ["python3", "queue_worker.py", "--live" if live else "--once"]
    with open(os.path.join(wq, "loop.out"), "a") as out:
        r = run(args, cwd=wq, env=build_env(opts), stdout=out, stderr=subprocess.STDOUT)
    return f"{'live' if live else 'dry'} pass exit {r.returncode}"


def wait(opts, wq=None, run=subprocess.run, sleep=time.sleep, waiting=None):
    """Same as wait_for_work.py but with the options' limits; returns early when a job waits and none runs."""
    end = time.time() + opts["idle_max_s"]
    while time.time() < end:
        sleep(opts["idle_check_s"])
        if (waiting or _waiting)(opts, wq): return True
    return False


def _waiting(opts, wq=None):
    wq = wq or WQ
    try:
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import work_queue as W; c = (W.status_summary() or {}).get('counts', {}); print(int(c.get('queued', 0) > 0 and c.get('running', 0) == 0))" % wq],
                           capture_output=True, text=True, timeout=30, env=build_env(opts), cwd=wq)
        return r.stdout.strip() == "1"
    except Exception:
        return False


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path.startswith("/healthz") or self.path.startswith("/status")
        body = json.dumps({"ok": True, "mode": STATE["mode"], "paused": STATE["paused"], "last_pass": STATE["last_pass"], "last_result": STATE["last_result"],
                           "problems": STATE["problems"]} if ok else {"error": "not found"}).encode()
        self.send_response(200 if ok else 404); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a): pass


def start_ui():
    """Read-only status page (ingress, own port). A failure here must never stop the runner."""
    try:
        import ui_server
        srv = ui_server.make_server(UI_PORT, lambda: ui_server.build_status(read_options(), STATE, WQ))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    except Exception as e:
        STATE["problems"] = STATE["problems"] + [f"status page not started: {type(e).__name__}"]


def main():
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", PORT), Health).serve_forever(), daemon=True).start()
    start_ui()
    while True:
        opts = read_options()
        mcp = install_mcp_config(opts)
        STATE["problems"] = preflight(opts) + ([mcp] if mcp else []); STATE["mode"] = opts["mode"]; STATE["paused"] = pause_reason(opts)
        try:
            if STATE["paused"]: STATE["last_result"] = "paused: " + STATE["paused"]
            elif any(x.startswith(("missing", "claude not", "python3 not")) for x in STATE["problems"]): STATE["last_result"] = "not ready: " + STATE["problems"][0]
            else: STATE["last_result"] = run_pass(opts)
        except Exception as e:
            STATE["last_result"] = f"pass failed: {type(e).__name__}: {str(e)[:120]}"
        STATE["last_pass"] = time.strftime("%F %T")
        wait(opts)


if __name__ == "__main__":
    main()
