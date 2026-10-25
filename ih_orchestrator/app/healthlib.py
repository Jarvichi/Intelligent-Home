"""Intelligent Home health checks (stdlib only, read-only). Used by the add-on app AND by host_probe.py.

A manifest 'health' list holds checks; ALL must pass. Check types:
  tmux      {"type":"tmux","session":"mysession"}          HOST ONLY (a container cannot see tmux)
(file_age and file_exists are HOST ONLY too: the add-on has no folders; it uses the pushed results)
  http      {"type":"http","url":"http://127.0.0.1:8767/","timeout":3,"ok_status":[200]}
  file_age  {"type":"file_age","path":"/path/to/file","max_age_s":120}   file modified recently
  file_exists {"type":"file_exists","path":"..."}
  supervisor_addon {"type":"supervisor_addon","slug":"core_mosquitto","expected_state":"started"|"stopped"}
    HOST ONLY: GET http://supervisor/addons/<slug>/info with env SUPERVISOR_TOKEN (read only, token never printed).
Each check function returns {"type", "ok": True|False|None, "detail": str}. None = could not be run here.
"""
import json, os, subprocess, time, urllib.request, urllib.error

HOST_ONLY = {"tmux", "file_age", "file_exists", "supervisor_addon"}  # the add-on maps no folders, so only the host can run these


def check_file_age(path, max_age_s, now=None):
    now = time.time() if now is None else now
    try:
        age = now - os.stat(path).st_mtime
    except OSError:
        return {"type": "file_age", "ok": False, "detail": "missing file"}
    if age < -5:
        return {"type": "file_age", "ok": False, "detail": "file time is in the future"}
    ok = age <= max_age_s
    return {"type": "file_age", "ok": ok, "detail": "age %ds (max %ds)" % (age, max_age_s)}


def check_file_exists(path):
    ok = os.path.exists(path)
    return {"type": "file_exists", "ok": ok, "detail": "present" if ok else "missing"}


def check_http(url, timeout=3, ok_status=(200,)):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # refused, timeout, dns
        return {"type": "http", "ok": False, "detail": type(e).__name__}
    ok = code in ok_status
    return {"type": "http", "ok": ok, "detail": "HTTP %d" % code}


def check_tmux(session, runner=None):
    run = runner or (lambda a: subprocess.run(a, capture_output=True, timeout=5).returncode)
    try:
        rc = run(["tmux", "has-session", "-t", "=" + session])
    except (OSError, subprocess.SubprocessError):
        return {"type": "tmux", "ok": None, "detail": "tmux not available here"}
    ok = rc == 0
    return {"type": "tmux", "ok": ok, "detail": "session present" if ok else "session missing"}


def check_supervisor_addon(slug, expected_state="started", fetch=None):
    """Read-only add-on state from the Supervisor. fetch(slug) -> info dict (tests pass a fake). ok=None if unreadable."""
    if fetch is None:
        def fetch(sl):
            tok = os.environ.get("SUPERVISOR_TOKEN", "")
            if not tok:
                raise PermissionError("no SUPERVISOR_TOKEN")
            req = urllib.request.Request("http://supervisor/addons/%s/info" % sl, headers={"Authorization": "Bearer " + tok})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.load(r).get("data") or {}
    try:
        d = fetch(slug)
        state, ver, latest = str(d.get("state")), str(d.get("version")), str(d.get("version_latest"))
    except Exception as e:  # never include the request (it holds the token)
        return {"type": "supervisor_addon", "ok": None, "detail": "supervisor unreadable (%s)" % type(e).__name__}
    upd = " (update available: %s)" % latest if latest not in ("None", "") and ver != latest else ""
    if expected_state == "stopped":
        ok = state == "stopped"
        detail = ("stopped (expected)" if ok else "%s but expected stopped" % state)
    else:
        ok = state == "started"
        detail = state
    return {"type": "supervisor_addon", "ok": ok, "detail": "%s, v%s%s" % (detail, ver, upd)}


def run_check(chk, host=True, now=None):
    """Run one check dict. host=False skips host-only types (ok=None)."""
    t = chk.get("type")
    if t in HOST_ONLY and not host:
        return {"type": t, "ok": None, "detail": "host-only check"}
    if t == "tmux":
        return check_tmux(chk["session"])
    if t == "http":
        return check_http(chk["url"], chk.get("timeout", 3), tuple(chk.get("ok_status", [200])))
    if t == "file_age":
        return check_file_age(chk["path"], chk["max_age_s"], now)
    if t == "file_exists":
        return check_file_exists(chk["path"])
    if t == "supervisor_addon":
        return check_supervisor_addon(chk["slug"], chk.get("expected_state", "started"))
    return {"type": str(t), "ok": None, "detail": "unknown check type"}


def combine(results):
    """OK if all True; FAIL if any False; UNKNOWN if none False but some None; UNKNOWN if empty."""
    oks = [r["ok"] for r in results]
    if any(o is False for o in oks):
        return "FAIL"
    if not oks or any(o is None for o in oks):
        return "UNKNOWN"
    return "OK"


def evaluate(manifest, host=True, now=None):
    results = [run_check(c, host, now) for c in manifest.get("health", [])]
    return {"name": manifest["name"], "state": combine(results), "checks": results}
