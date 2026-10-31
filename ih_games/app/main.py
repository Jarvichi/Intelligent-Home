
#!/usr/bin/env python3
"""ih_games: game activity facts feeds and orchestrator add-on."""
import hmac, json, os, sys, threading, time, traceback, logging, collections, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import schedule_client, orchestrator, library as library_mod, played, todos, tools as tools_mod
from jobs import common
from providers import psn_trophies, xbox_gamerscore, destiny, steam_presence

VERSION = "0.6.0"
DEFAULT_INGEST_URL = "http://172.30.32.1:8790"
SOURCE = "ih_games"
JOBS = ("providers", "orchestrate")
STOP_FILE = "STOP_LIVE"
OPTIONS = os.environ.get("IHG_OPTIONS", "/data/options.json")
DATA = os.environ.get("IHG_DATA", "/data")
PORT = int(os.environ.get("IHG_PORT", "8794"))
PUSH_EVERY = 30
WINDOWS = {"providers": 60, "orchestrate": 120}   # = manifest window_s
START_DELAY_S = 60

PROVIDERS = {
    "psn_trophies": psn_trophies,
    "xbox_gamerscore": xbox_gamerscore,
    "destiny": destiny,
    "steam_presence": steam_presence,
}

def load_options(path=None):
    try:
        o = json.load(open(path or OPTIONS))
    except (OSError, ValueError):
        o = {}
    return {
        "mode": "live" if str(o.get("mode") or "dry").strip() == "live" else "dry",
        "players": o.get("players", []),
        "providers_enabled": o.get("providers_enabled", ["psn_trophies"]),
        "quiet_start": str(o.get("quiet_start") or "22:00").strip(),
        "quiet_end": str(o.get("quiet_end") or "08:00").strip(),
        "timezone": str(o.get("timezone") or "Europe/London").strip(),
        "ingest_url": str(o.get("ingest_url") or DEFAULT_INGEST_URL).rstrip("/"),
        "ingest_token": str(o.get("ingest_token") or "").strip(),
        "mqtt_host": str(o.get("mqtt_host") or "core-mosquitto").strip(),
        "mqtt_port": int(o.get("mqtt_port") or 1883),
        "mqtt_user": str(o.get("mqtt_user") or "ih_games").strip(),
        "mqtt_password": str(o.get("mqtt_password") or ""),
        "enable_scheduler": bool(o.get("enable_scheduler", False)),
        "signing_key": str(o.get("signing_key") or "").strip(),
        "outbox_token": str(o.get("outbox_token") or "").strip(),
        "assistant_url": str(o.get("assistant_url") or "").strip().rstrip("/"),
        "assistant_token": str(o.get("assistant_token") or "").strip(),
        "bungie_api_key": str(o.get("bungie_api_key") or "").strip(),
        "destiny_poll_s": int(o.get("destiny_poll_s") or 900),
        "log_payloads": o.get("log_payloads") is True,
        "admin_token": str(o.get("admin_token") or "").strip(),
        "arc_data_dir": str(o.get("arc_data_dir") or "").strip(),
        "destiny_reset_utc_hour": int(o.get("destiny_reset_utc_hour") if o.get("destiny_reset_utc_hour") is not None else 17),
        "todo_entity": str(o.get("todo_entity") or "todo.claude_to_do").strip(),
        "todo_sync": o.get("todo_sync") is not False,
    }

def _snip(s, n=200):
    """First n chars as ONE line: backslash, quote and newlines escaped. Only used when option log_payloads is true."""
    s = str(s or '')[:n]
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n')


def _tag(v, default='-'):
    """A short safe token for a log line (letters, digits and . _ : - only)."""
    t = ''.join(c if (c.isalnum() and c.isascii()) or c in '._:-' else '_' for c in str(v if v is not None else default))[:64]
    return t or default


class Module:
    def __init__(self, opts, data=None, log=None, token=None, opener=None):
        self.opts, self.started = opts, time.time()
        self.data = data or DATA
        self.log = log or (lambda m: logging.getLogger("ihgames").info(m))
        self._token, self._opener = token, opener
        self.locks = {n: threading.Lock() for n in JOBS}
        self.runs, self.errors, self.last, self.last_ok = collections.Counter(), collections.Counter(), {}, {}
        self.err_last, self.last_mode, self.last_trigger = {}, {}, {}
        self.sched, self.sched_reason = None, ""
        self.tool_mods = tools_mod.all_tools()
        self.library = library_mod.Library(self.data, self.tool_mods, opts.get("players", []))

    def tool_ctx(self):
        return tools_mod.ToolCtx(self.data, self.opts, opener=self._opener, log=self.log)

    def tool_for(self, entry_or_name):
        for t in self.tool_mods:
            if t.NAME == entry_or_name or (isinstance(entry_or_name, dict) and t.matches(entry_or_name)):
                return t
        return None

    def api_authorised(self, header):
        t = self.opts.get("admin_token", "")
        return bool(t) and hmac.compare_digest(str(header or ""), "Bearer " + t)

    def todo_sync(self):
        """Create the 'research a tracker' to-dos for games without a tool (live only; dry logs)."""
        if not self.opts.get("todo_sync"):
            return [], []
        live, _ = self.live_mode()
        ctx = common.Ctx(dry=not live, log=self.log, token=self._token, opener=self._opener, data_dir=self.data, opts=self.opts)
        ctx.opts = dict(self.opts)
        return todos.sync(self.library, ctx, entity=self.opts.get("todo_entity") or todos.ENTITY)

    def api(self, method, path, query, body):
        """The library API. Returns (code, object). Bearer admin_token is checked by the caller."""
        lib, parts = self.library, [x for x in path.strip("/").split("/") if x]
        who = lambda: lib.resolve_player((body or {}).get("player") or query.get("player"))
        if parts == ["library"] and method == "GET":
            pk = who()
            if not pk:
                return 400, {"ok": False, "why": "unknown player"}
            lib.refresh()
            return 200, {"ok": True, "player": pk, "games": lib.list(pk, query.get("hidden") == "1")}
        if parts == ["library", "new-games-without-todo"] and method == "GET":
            lib.refresh()
            return 200, {"ok": True, "games": lib.games_without_todo()}
        if parts == ["library", "sync-todos"] and method == "POST":
            lib.refresh()
            made, skipped = self.todo_sync()
            return 200, {"ok": True, "created": made, "skipped": skipped, "mode": self.mode()}
        if parts == ["library", "add"] and method == "POST":
            pk = who()
            if not pk or not (body or {}).get("title"):
                return 400, {"ok": False, "why": "player and title required"}
            e = lib.add(pk, body["title"], body.get("platform"), body.get("tools"))
            made, _ = [], []
            try:
                made, _ = self.todo_sync()
            except Exception as ex:
                self.log("todo sync failed: " + type(ex).__name__)
            return 200, {"ok": True, "game": lib.get(pk, e["id"]), "todo_created": made}
        if parts == ["library", "hide"] and method == "POST":
            pk = who()
            e = pk and lib.hide(pk, str((body or {}).get("id") or (body or {}).get("title") or ""), (body or {}).get("hidden", True) is not False)
            return (200, {"ok": True, "game": e}) if e else (404, {"ok": False, "why": "not found"})
        if len(parts) == 3 and parts[0] == "games":
            pk = who()
            if not pk:
                return 400, {"ok": False, "why": "unknown player"}
            lib.refresh()
            entry = lib.find(pk, parts[1])
            tool = self.tool_for(entry or parts[1])
            if not entry and not tool:
                return 404, {"ok": False, "why": "game not in library"}
            if not entry:
                entry = {"title": parts[1], "platform": None, "tools": [tool.NAME]}
            tc, what = self.tool_ctx(), parts[2]
            if what == "summary" and method == "GET":
                pl = played.played(self.data, pk, entry)
                text = played.text(entry, pl)
                if tool:
                    text += " " + tool.summary(tc, pk)
                return 200, {"ok": True, "game": entry["title"], "played": pl, "text": text, "tool": tool.NAME if tool else None}
            if what == "upcoming" and method == "GET":
                return 200, {"ok": True, "game": entry["title"], "upcoming": tool.upcoming(tc, pk) if tool else []}
            if what == "cmd" and method == "POST":
                if not tool:
                    return 404, {"ok": False, "why": "no tool for this game yet"}
                name = str((body or {}).get("cmd") or "").strip().lower()
                fn = tool.commands.get(name)
                if not fn:
                    return 404, {"ok": False, "why": "unknown command", "commands": sorted(tool.commands)}
                args = [str(a) for a in ((body or {}).get("args") or [])]
                return 200, {"ok": True, "text": fn(tc, pk, args)}
        return 404, {"ok": False, "why": "no such endpoint"}

    def live_mode(self):
        if self.opts.get("mode") != "live":
            return False, "mode option is dry"
        if os.path.exists(os.path.join(self.data, STOP_FILE)):
            return False, "kill switch file STOP_LIVE exists"
        return True, "live"

    def mode(self):
        return "live" if self.live_mode()[0] else "dry"

    def run_job(self, name, trigger="manual"):
        if name not in JOBS:
            return False
        with self.locks[name]:
            live, why = self.live_mode()
            t0, seen = time.time(), []
            self.inv_seq = getattr(self, "inv_seq", 0) + 1
            rid = self.inv_seq

            def jlog(m):
                seen.append(str(m)); self.log(m)
            ctx = common.Ctx(dry=not live, log=jlog, token=self._token, opener=self._opener, data_dir=self.data, opts=self.opts)
            self.last_mode[name] = "live" if live else "dry"
            self.last_trigger[name] = trigger
            ok, ewhy = False, "-"
            try:
                if name == "providers":
                    for p_name in self.opts.get("providers_enabled", []):
                        if p_name in PROVIDERS:
                            for player in self.opts.get("players", []):
                                PROVIDERS[p_name].run(ctx, player)
                    self.library.refresh()
                    self.todo_sync()
                elif name == "orchestrate":
                    orchestrator.run(ctx)
                
                self.last_ok[name] = True
                self.last[name] = time.time()
                self.runs[name] += 1
                self.err_last.pop(name, None)
                ok = True
                return True
            except Exception as e:
                self.last[name] = time.time()
                self.last_ok[name] = False
                self.errors[name] += 1
                ewhy = type(e).__name__
                self.err_last[name] = traceback.format_exc(limit=3).strip().splitlines()[-1][:200]
                self.log(f"{name} error: {self.err_last[name]}")
                return False
            finally:
                self.invoke_line("run%d" % rid, trigger, name, 0, sum(len(x) for x in seen), t0, ok, ewhy, "%s %s" % (trigger, name), seen[-1] if seen else "")

    def invoke_line(self, rid, src, what, n_in, n_out, t0, ok, why, req="", ans=""):
        """ONE line per invocation (job run, /outbox read, /ack): id, source, what, chars in/out, ms, ok, why. Text only when log_payloads is on."""
        try:
            f = ["invoke", "id=" + _tag(rid), "src=" + _tag(src), "what=" + _tag(what), "in=%d" % n_in, "out=%d" % n_out,
                 "ms=%d" % int((time.time() - t0) * 1000), "ok=%s" % bool(ok), "why=" + _tag(why)]
            if self.opts.get("log_payloads"):
                f += ['req="%s"' % _snip(req), 'ans="%s"' % _snip(ans)]
            self.log(" ".join(f))
        except Exception:
            pass

    def start_run_all(self, delay=START_DELAY_S):
        def go():
            time.sleep(delay)
            for n in JOBS:
                self.run_job(n, "startup")
        t = threading.Thread(target=go, daemon=True, name="startup")
        t.start()
        return t

    def start_scheduler(self, bus=None):
        o = self.opts
        if not o.get("enable_scheduler"):
            return False
        if not o.get("signing_key"):
            self.sched_reason = "no signing key"
            return False
        if bus is None:
            if not o.get("mqtt_password"):
                self.sched_reason = "no mqtt password"
                return False
            bus = schedule_client.PahoBus(o["mqtt_host"], o["mqtt_port"], o["mqtt_user"], o["mqtt_password"], SOURCE, log=lambda m: self.log("schedule: " + m))
        self.sched = schedule_client.ScheduleClient(SOURCE, bus, log=self.log, signing_key=o["signing_key"])
        for n in JOBS:
            self.sched.on(n, lambda payload, n=n: self.run_job(n, "scheduler"), window_s=WINDOWS[n])
        return self.sched.start() is not False

    def outbox_pending(self, limit=20):
        """Unacknowledged messages, oldest first. The sender on the host delivers them and then acks."""
        acked = self._acked()
        out = []
        try:
            for line in open(os.path.join(self.data, "outbox.jsonl")):
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("id") not in acked:
                    out.append(m)
        except OSError:
            pass
        return out[:limit]

    def _acked(self):
        try:
            return set(json.load(open(os.path.join(self.data, "outbox_acked.json"))))
        except (OSError, ValueError):
            return set()

    def ack(self, mid):
        acked = self._acked()
        acked.add(mid)
        tmp = os.path.join(self.data, "outbox_acked.json.tmp")
        json.dump(sorted(acked)[-1000:], open(tmp, "w"))
        os.replace(tmp, os.path.join(self.data, "outbox_acked.json"))

    def authorised(self, header):
        t = self.opts.get("outbox_token", "")
        return bool(t) and hmac.compare_digest(str(header or ""), "Bearer " + t)

    def start_fallback_timer(self, tick=5):
        """No signed scheduler (needs MQTT login + key, not set up): run the jobs on our own clock at the manifest intervals.
        Off automatically when the scheduler is active."""
        every = {"providers": 120, "orchestrate": 300}
        last = {n: time.time() for n in JOBS}

        def loop():
            while True:
                time.sleep(tick)
                if self.sched is not None:
                    continue
                for n in JOBS:
                    if time.time() - last[n] >= every[n]:
                        last[n] = time.time()
                        self.run_job(n, "timer")
        t = threading.Thread(target=loop, daemon=True, name="fallback-timer")
        t.start()
        return t

    def feeds(self):
        """Per feed file: when it was last written and how many events it holds (no content)."""
        out = {}
        d = os.path.join(self.data, "feeds")
        try:
            for fn in sorted(os.listdir(d)):
                try:
                    f = json.load(open(os.path.join(d, fn)))
                    out[fn[:-5]] = {"updated": f.get("updated"), "events": len(f.get("events", []))}
                except (OSError, ValueError):
                    out[fn[:-5]] = {"updated": None, "events": 0}
        except OSError:
            pass
        return out

    def messages_sent(self):
        try:
            return sum(1 for l in open(os.path.join(self.data, "messages.jsonl")) if '"sent": true' in l)
        except OSError:
            return 0

    def healthy(self):
        return True

    def status(self):
        now = time.time()
        return {
            "name": SOURCE,
            "version": VERSION,
            "mode": self.mode(),
            "kill_switch": os.path.exists(os.path.join(self.data, STOP_FILE)),
            "uptime_s": int(now - self.started),
            "healthy": self.healthy(),
            "messages_sent": self.messages_sent(),
            "feeds": self.feeds(),
            "scheduler": {"enabled": bool(self.opts.get("enable_scheduler")), "active": self.sched is not None,
                          "reason": self.sched_reason if self.sched is None and self.opts.get("enable_scheduler") else "",
                          "rejected": self.sched.rejected if self.sched else 0},
            "jobs": {n: {"mode": self.mode(), "runs": self.runs[n], "errors": self.errors[n],
                         "last_ok": self.last_ok.get(n), "last_run_s": None if n not in self.last else int(now - self.last[n]),
                         "last_mode": self.last_mode.get(n), "last_trigger": self.last_trigger.get(n), "last_error": self.err_last.get(n)} for n in JOBS}
        }

def push(mod, manifest, now=None, opener=None, log=print):
    """POST this module's manifest and status to the Intelligent Home add-on. Never raises. The token is never printed."""
    o = mod.opts
    if not o["ingest_token"]:
        return False
    now = time.time() if now is None else now
    st = mod.status()
    body = json.dumps({"source": SOURCE, "manifests": [manifest],
                       "status": [{"name": SOURCE, "probed_at": now,
                                   "checks": [{"type": "http", "ok": st["healthy"], "detail": "%s, %d message(s) sent" % (st["mode"], st["messages_sent"])}]}]}).encode()
    try:
        r = urllib.request.Request(o["ingest_url"] + "/ingest", data=body, method="POST",
                                   headers={"Authorization": "Bearer " + o["ingest_token"], "Content-Type": "application/json"})
        with (opener or urllib.request.urlopen)(r, timeout=5) as x:
            return x.status == 200
    except Exception as e:
        log("ih push: failed (%s)" % type(e).__name__)
        return False


def make_handler(mod):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            p = self.path.split("?")[0]
            if p == "/healthz":
                code, body = 200, b"ok"
            elif p == "/status":
                code, body = 200, json.dumps(mod.status()).encode()
            elif p == "/outbox":
                t0 = time.time()
                if not mod.opts.get("outbox_token"):
                    code, body = 503, b"outbox off"
                elif not mod.authorised(self.headers.get("Authorization")):
                    code, body = 401, b"no"
                else:
                    code, body = 200, json.dumps(mod.outbox_pending()).encode()
                mod.invoke_line("-", "outbox_client", "outbox", 0, len(body), t0, code == 200, "-" if code == 200 else ("outbox_off" if code == 503 else "unauthorized"), "GET /outbox", body.decode("utf-8", "replace"))
            elif p.startswith("/library") or p.startswith("/games"):
                code, body = self.library_call("GET")
            else:
                self.send_error(404)
                return
            self.send_response(code)
            self.send_header("Content-Type", "application/json" if (p == "/status" or p.startswith(("/library", "/games"))) else "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def library_call(self, method):
            import urllib.parse
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if not mod.opts.get("admin_token"):
                return 503, b'{"ok": false, "why": "api off: set admin_token"}'
            if not mod.api_authorised(self.headers.get("Authorization")):
                return 401, b'{"ok": false, "why": "bearer admin_token required"}'
            body = {}
            if method == "POST":
                try:
                    n = min(int(self.headers.get("Content-Length") or 0), 65536)
                    body = json.loads(self.rfile.read(n) or b"{}")
                except Exception:
                    return 400, b'{"ok": false, "why": "bad body"}'
            try:
                code, obj = mod.api(method, u.path, q, body)
            except Exception as e:
                mod.log("api error: " + type(e).__name__)
                code, obj = 500, {"ok": False, "why": "error"}
            return code, json.dumps(obj).encode()

        def do_POST(self):
            if self.path.split("?")[0].startswith("/library") or self.path.split("?")[0].startswith("/games"):
                code, body = self.library_call("POST")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.split("?")[0] != "/ack":
                self.send_error(404)
                return
            t0, mid = time.time(), ""
            if not mod.opts.get("outbox_token"):
                code, body = 503, b"outbox off"
            elif not mod.authorised(self.headers.get("Authorization")):
                code, body = 401, b"no"
            else:
                try:
                    n = min(int(self.headers.get("Content-Length") or 0), 4096)
                    mid = str(json.loads(self.rfile.read(n))["id"])
                    mod.ack(mid)
                    code, body = 200, b"ok"
                except Exception:
                    code, body = 400, b"bad"
            mod.invoke_line(mid or "-", "outbox_client", "ack", len(mid), len(body), t0, code == 200, "-" if code == 200 else ("outbox_off" if code == 503 else "unauthorized" if code == 401 else "bad_request"), "POST /ack " + mid, body.decode())
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    return H

def main():
    lg = logging.getLogger("ihgames")
    lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__)
    sh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    lg.addHandler(sh)
    
    mod = Module(load_options(), log=lg.info)
    try:
        mod.start_scheduler()
    except Exception as e:
        lg.info(f"scheduler not started ({type(e).__name__})")
    
    mod.start_run_all()
    mod.start_fallback_timer()
    lg.info(f"ih_games {VERSION} starting: mode={mod.mode()}")
    manifest = json.load(open(os.path.join(HERE, "manifest.json")))

    def pusher():
        while True:
            push(mod, manifest, log=lg.info)
            time.sleep(PUSH_EVERY)
    threading.Thread(target=pusher, daemon=True).start()
    
    ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(mod)).serve_forever()

if __name__ == "__main__":
    main()


