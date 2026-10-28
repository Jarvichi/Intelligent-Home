#!/usr/bin/env python3
"""Intelligent Home scheduler (orchestrator v0.4.1). One clock for every module's timers.
Reads `schedules` from the pushed manifests, and when a job is due publishes MQTT `ih/schedule/<module>/<job>`
with {v:1, id, due_at, attempt, sig}. The module runs the job (it MUST be idempotent: a retry or catch-up can run it twice)
and replies on `ih/schedule/<module>/<job>/done` with {id, ok, ts, sig}.
SIGNING (0.4.1): the broker does not enforce topic ACLs, so every message carries an HMAC-SHA256 with a per-module key
(option module_keys). A module with no key gets no jobs. Unsigned, wrong, replayed or stale done messages are ignored and only COUNTED.
Keys and signatures are never logged or put in /status. State is kept in <data>/schedule_state.json.
Safety-critical timers (oven alerts etc.) keep their OWN backup timer: this scheduler is a convenience, not a guarantee.
Stdlib only, except the optional paho-mqtt import in PahoBus. The only network target is the broker host option."""
import hashlib, hmac, json, os, re, time, threading
from collections import OrderedDict
from datetime import datetime, timedelta, time as dtime

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
KINDS = ("interval", "daily", "weekly")
TOPIC_ROOT = "ih/schedule"
DONE_FILTER = TOPIC_ROOT + "/+/+/done"
RETRY_AFTER_FAIL_S = 60
MIN_INTERVAL_S = 10
SKEW_S = 60  # a done ts this far in the future is tolerated
SEEN_MAX = 500


def _snip(s, n=200):
    """First n chars as ONE line: backslash, quote and newlines escaped. Only used when option log_payloads is true."""
    s = str(s or "")[:n]
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")


def _tag(v, default="-"):
    """A short safe token for a log line (letters, digits and . _ : / - only)."""
    t = re.sub(r"[^A-Za-z0-9._:/-]", "_", str(v if v is not None else default))[:64]
    return t or default


def sign(key, text):
    return hmac.new(key.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()


def due_text(module, job, jid, due_at, attempt):
    return "v1|%s|%s|%s|%d|%d" % (module, job, jid, int(due_at), int(attempt))


def done_text(module, job, jid, ok, ts):
    return "v1done|%s|%s|%s|%d|%d" % (module, job, jid, 1 if ok else 0, int(ts))


def parse_keys(raw):
    """module_keys option -> {module: key}. Skips malformed or empty entries."""
    out = {}
    for e in raw if isinstance(raw, list) else []:
        if isinstance(e, dict) and isinstance(e.get("module"), str) and NAME_RE.match(e["module"]) and isinstance(e.get("key"), str) and e["key"].strip():
            out[e["module"]] = e["key"].strip()
    return out


def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        from datetime import timezone
        return timezone.utc  # tzdata missing: UTC (status shows tz_fallback)


DEFAULT_TZ = "UTC"   # used for "daily"/"weekly" jobs whose schedule has no "tz"; set by the option default_timezone (build)


def set_default_tz(name):
    """Option default_timezone (an IANA name, e.g. Europe/London). Unknown or empty = UTC."""
    global DEFAULT_TZ
    n = str(name or "").strip() or "UTC"
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(n)
    except Exception:
        n = "UTC"
    DEFAULT_TZ = n
    return n


def parse_job(module, spec):
    """Validate one schedule entry. Returns (job dict, None) or (None, error text)."""
    if not isinstance(spec, dict):
        return None, "schedule is not an object"
    job, kind = spec.get("job"), spec.get("kind")
    if not (isinstance(job, str) and NAME_RE.match(job)):
        return None, "bad job name"
    if kind not in KINDS:
        return None, "bad kind"
    j = {"module": module, "job": job, "kind": kind, "enabled": spec.get("enabled", True) is not False,
         "catchup": spec.get("catchup", False) is True, "tz": str(spec.get("tz") or DEFAULT_TZ)}
    if kind == "interval":
        e = spec.get("every_s")
        if isinstance(e, bool) or not isinstance(e, (int, float)) or e < MIN_INTERVAL_S:
            return None, "every_s must be a number >= %d" % MIN_INTERVAL_S
        j["every_s"] = int(e)
    else:
        m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", str(spec.get("at") or ""))
        if not m:
            return None, "at must be HH:MM"
        j["at"] = "%02d:%02d" % (int(m.group(1)), int(m.group(2)))
        if kind == "weekly":
            wd = spec.get("weekdays")
            try:
                days = sorted({DAYS[str(d).lower()[:3]] if not isinstance(d, int) else d for d in wd})
            except Exception:
                return None, "weekdays must be a list like [\"mon\",\"thu\"]"
            if not days or any(d < 0 or d > 6 for d in days):
                return None, "weekdays must be a non-empty list of days"
            j["weekdays"] = days
        else:
            j["weekdays"] = list(range(7))
    w = spec.get("window_s")
    if w is None:
        w = j.get("every_s", 3600)
    if isinstance(w, bool) or not isinstance(w, (int, float)) or w < 0:
        return None, "window_s must be a number >= 0"
    j["window_s"] = int(w)
    return j, None


def _slot_at(j, day):
    h, m = j["at"].split(":")
    tz = _zone(j["tz"])
    return datetime.combine(day, dtime(int(h), int(m)), tzinfo=tz).timestamp()


def latest_slot(j, now):
    """Most recent scheduled time <= now (daily/weekly). None if none in the last 8 days."""
    local = datetime.fromtimestamp(now, _zone(j["tz"]))
    for back in range(0, 9):
        day = (local - timedelta(days=back)).date()
        if day.weekday() in j["weekdays"]:
            ts = _slot_at(j, day)
            if ts <= now:
                return ts
    return None


def next_slot(j, now):
    """First scheduled time > now (daily/weekly)."""
    local = datetime.fromtimestamp(now, _zone(j["tz"]))
    for fwd in range(0, 9):
        day = (local + timedelta(days=fwd)).date()
        if day.weekday() in j["weekdays"]:
            ts = _slot_at(j, day)
            if ts > now:
                return ts
    return None


def describe(j):
    if j["kind"] == "interval":
        return "every %d s" % j["every_s"]
    if j["kind"] == "daily":
        return "daily %s" % j["at"]
    names = list(DAYS)
    return "%s %s" % ("/".join(names[d] for d in j["weekdays"]), j["at"])


def load_jobs(manifests):
    """-> (jobs dict key 'module/job' -> job, errors list)."""
    jobs, errs = {}, []
    for m in manifests or []:
        if not isinstance(m, dict) or not isinstance(m.get("name"), str):
            continue
        sch = m.get("schedules") or []
        if not isinstance(sch, list):
            errs.append("%s: schedules is not a list" % m["name"]); continue
        for s in sch:
            j, e = parse_job(m["name"], s)
            if e:
                errs.append("%s: %s" % (m["name"], e)); continue
            k = "%s/%s" % (j["module"], j["job"])
            if k in jobs:
                errs.append("%s: duplicate job" % k); continue
            jobs[k] = j
    return jobs, errs


def off_status(manifests, reason="disabled", now=None):
    """The `schedule` section when the scheduler is not running: lists declared jobs, state off."""
    now = time.time() if now is None else now
    jobs, errs = load_jobs(manifests)
    rows = []
    for k in sorted(jobs):
        j = jobs[k]
        rows.append({"module": j["module"], "job": j["job"], "when": describe(j), "next_due": None, "last_run": None,
                     "last_ack": None, "state": "off", "detail": reason})
    return {"state": "off", "reason": reason, "mqtt_connected": False, "jobs": rows, "errors": errs}


class Scheduler:
    def __init__(self, bus, state_path, log=print, clock=time.time, started_at=None, keys=None):
        self.bus, self.path, self.log, self.clock = bus, state_path, log, clock
        self.keys = dict(keys or {})  # module -> secret; never logged, never in status
        self.rejected = 0
        self.log_payloads = False
        self.seen_done = OrderedDict()  # signatures of accepted done messages (replay guard)
        self.started_at = clock() if started_at is None else started_at
        self.jobs, self.errors = {}, []
        self.lock = threading.RLock()
        self.state = {}  # key -> {last_slot,last_run,last_ack,last_ok,pending,paused,status,missed,detail}
        self._load()

    # persistence
    def _load(self):
        try:
            d = json.load(open(self.path))
            if isinstance(d, dict) and isinstance(d.get("jobs"), dict):
                self.state = {k: v for k, v in d["jobs"].items() if isinstance(v, dict)}
        except Exception:
            self.state = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"jobs": self.state}, f)
            os.replace(tmp, self.path)
        except OSError as e:
            self.log("schedule state not saved: %s" % e)

    def _st(self, key):
        return self.state.setdefault(key, {"last_slot": None, "last_run": None, "last_ack": None, "last_ok": None,
                                           "pending": None, "paused": False, "status": "ok", "missed": 0, "detail": ""})

    def sync(self, manifests):
        with self.lock:
            self.jobs, self.errors = load_jobs(manifests)

    # core
    def _due(self, j, st, now):
        """-> (due_at or None). Also initialises the anchor for a job seen the first time."""
        if j["kind"] == "interval":
            if st["last_slot"] is None:
                st["last_slot"] = now  # first sight: first run one interval from now
                return None
            due = st["last_slot"] + j["every_s"]
            return due if due <= now else None
        slot = latest_slot(j, now)
        if slot is None:
            return None
        if st["last_slot"] is None:
            st["last_slot"] = slot  # first sight: never run an old slot
            return None
        return slot if slot > st["last_slot"] else None

    def _advance_past(self, j, st, now):
        if j["kind"] == "interval":
            n = max(1, int((now - st["last_slot"]) // j["every_s"]))
            st["last_slot"] += n * j["every_s"]
        else:
            st["last_slot"] = latest_slot(j, now) or st["last_slot"]

    def _publish(self, j, key, st, jid, due_at, attempt, now):
        key = self.keys.get(j["module"])
        if not key:
            return False
        payload = json.dumps({"v": 1, "id": jid, "due_at": int(due_at), "attempt": attempt,
                              "sig": sign(key, due_text(j["module"], j["job"], jid, due_at, attempt))})
        ok, t0 = False, time.time()
        try:
            ok = bool(self.bus.publish("%s/%s/%s" % (TOPIC_ROOT, j["module"], j["job"]), payload))
        except Exception:
            ok = False
        self._inv(jid, "scheduler", "dispatch:%s/%s" % (j["module"], j["job"]), 0, len(payload), t0, ok, "-" if ok else "publish_failed",
                  "%s/%s/%s attempt=%d" % (TOPIC_ROOT, j["module"], j["job"], attempt), "")   # never the payload: it holds the signature
        if ok:
            st["last_run"] = now
            st["pending"] = {"id": jid, "due_at": due_at, "published_at": now, "attempt": attempt, "retry_at": now + j["window_s"]}
        return ok

    def tick(self, now=None):
        now = self.clock() if now is None else now
        changed = False
        with self.lock:
            for key, j in self.jobs.items():
                st = self._st(key)
                if not j["enabled"] or not self.keys.get(j["module"]):
                    continue  # no key: no jobs published, state untouched
                p = st.get("pending")
                if p and now >= p["retry_at"]:  # no ack (or a failed ack) and the wait is over
                    if p["attempt"] < 2:
                        if self._publish(j, key, st, p["id"], p["due_at"], p["attempt"] + 1, now):
                            st["detail"] = "retry sent"
                    else:
                        st["pending"], st["status"], st["detail"] = None, "missed", "no ack after retry"
                        st["missed"] += 1
                    changed = True
                    continue
                if p:
                    continue  # waiting for the ack
                fresh = st["last_slot"] is None
                due = self._due(j, st, now)
                if due is None:
                    changed = changed or fresh
                    continue
                if st["paused"]:
                    self._advance_past(j, st, now); changed = True
                    continue
                late = now - due
                after_restart = due < self.started_at
                if late > j["window_s"] or (after_restart and not j["catchup"]):
                    st["status"] = "missed"
                    st["detail"] = "slot passed (%s)" % ("while down" if after_restart else "outside window")
                    st["missed"] += 1
                    self._advance_past(j, st, now); changed = True
                    continue
                jid = "%s.%s.%d" % (j["module"], j["job"], int(due))
                if self._publish(j, key, st, jid, due, 1, now):
                    st["last_slot"] = due
                    if j["kind"] == "interval":  # one run covers all missed intervals
                        st["last_slot"] += int((now - due) // j["every_s"]) * j["every_s"]
                    st["detail"] = "catch-up sent" if after_restart else "sent"
                    changed = True
                # publish failed: leave slot unfired; it becomes missed once the window is over
            if changed:
                self._save()

    def _reject(self):
        self.rejected += 1
        return False

    def _inv(self, rid, src, what, n_in, n_out, t0, ok, why, req="", ans=""):
        """ONE line per invocation (a job dispatch or a done message): id, source, what, chars in/out, ms, ok, why. Never keys or signatures."""
        try:
            f = ["invoke", "id=" + _tag(rid), "src=" + _tag(src), "what=" + _tag(what), "in=%d" % n_in, "out=%d" % n_out, "ms=%d" % int((time.time() - t0) * 1000),
                 "ok=%s" % bool(ok), "why=" + _tag(why)]
            if self.log_payloads:
                f += ['req="%s"' % _snip(req), 'ans="%s"' % _snip(ans)]
            self.log(" ".join(f))
        except Exception:
            pass

    def on_done(self, topic, payload, now=None):
        t0, before = time.time(), self.rejected
        r = self._on_done(topic, payload, now)
        try:
            d = json.loads(payload)
            jid, ok = d.get("id"), d.get("ok")
        except Exception:
            jid, ok = "-", None
        parts = str(topic).split("/")
        mod = parts[2] if len(parts) > 2 else "-"
        self._inv(jid, mod, "done:%s" % "/".join(parts[2:4]), len(payload) if hasattr(payload, "__len__") else 0, 0, t0, r and ok is not False,
                  "-" if r and ok is not False else ("module_reported_failure" if r else ("rejected" if self.rejected > before else "ignored")),
                  "%s ok=%s" % (topic, ok), "")   # never the payload: it holds the signature
        return r

    def _on_done(self, topic, payload, now=None):
        now = self.clock() if now is None else now
        parts = topic.split("/")
        if len(parts) != 5 or parts[:2] != ["ih", "schedule"] or parts[4] != "done":
            return False
        key = "%s/%s" % (parts[2], parts[3])
        with self.lock:
            secret, j = self.keys.get(parts[2]), self.jobs.get(key)
            try:
                d = json.loads(payload)
                jid, ok, ts, sig = d["id"], d["ok"], d["ts"], d["sig"]
                if not (isinstance(jid, str) and isinstance(ok, bool) and isinstance(sig, str)
                        and isinstance(ts, (int, float)) and not isinstance(ts, bool)):
                    raise ValueError
            except Exception:
                return self._reject()
            if not secret or not j:
                return self._reject()
            if not hmac.compare_digest(sig, sign(secret, done_text(parts[2], parts[3], jid, ok, ts))):
                return self._reject()
            if now - ts > 2 * j["window_s"] or ts - now > SKEW_S or sig in self.seen_done:
                return self._reject()  # stale, from the future, or replayed
            self.seen_done[sig] = True
            while len(self.seen_done) > SEEN_MAX:
                self.seen_done.popitem(last=False)
            st = self.state.get(key)
            p = st.get("pending") if st else None
            if not p or p["id"] != jid:
                return self._reject()  # id already acked or unknown
            st["last_ack"], st["last_ok"] = now, ok
            if ok:
                st["pending"], st["status"], st["detail"] = None, "ok", "done"
            elif p["attempt"] < 2:
                p["retry_at"] = now + RETRY_AFTER_FAIL_S
                st["detail"] = "module reported failure, retry queued"
            else:
                st["pending"], st["status"], st["detail"] = None, "missed", "module reported failure twice"
                st["missed"] += 1
            self._save()
        return True

    def set_paused(self, module, job, paused):
        key = "%s/%s" % (module, job)
        with self.lock:
            if key not in self.jobs:
                return False
            st = self._st(key)
            st["paused"] = bool(paused)
            if paused:
                st["pending"] = None
            self._save()
        return True

    def status(self, now=None):
        now = self.clock() if now is None else now
        rows = []
        with self.lock:
            for key in sorted(self.jobs):
                j, st = self.jobs[key], self._st(key)
                if not self.keys.get(j["module"]):
                    state = "no key"
                elif st["paused"]:
                    state = "paused"
                elif not j["enabled"]:
                    state = "off"
                elif st["status"] == "missed":
                    state = "missed"
                else:
                    state = "ok"
                nd = None
                if j["kind"] == "interval":
                    nd = st["last_slot"] + j["every_s"] if st["last_slot"] else None
                else:
                    nd = next_slot(j, st["last_slot"] if st["last_slot"] else now)
                rows.append({"module": j["module"], "job": j["job"], "when": describe(j),
                             "next_due": None if state in ("paused", "off", "no key") else nd, "last_run": st["last_run"],
                             "last_ack": st["last_ack"], "state": state, "missed": st["missed"],
                             "detail": "paused by switch" if state == "paused" else ("disabled in manifest" if state == "off" else ("no key" if state == "no key" else st["detail"]))})
            errs = list(self.errors)
        conn = bool(getattr(self.bus, "connected", False))
        return {"state": "on" if conn else "disconnected", "reason": "", "mqtt_connected": conn, "jobs": rows, "errors": errs, "rejected": self.rejected}

    def handle_message(self, topic, payload):
        self.on_done(topic, payload)

    def run(self, get_manifests, every=10):
        """Loop forever; never raises."""
        while True:
            try:
                self.sync(get_manifests())
                self.tick()
            except Exception as e:
                self.log("scheduler loop error: %s" % type(e).__name__)
            time.sleep(every)


class PahoBus:
    """Real MQTT bus (paho-mqtt 1.6.1, pinned in the Dockerfile). Non-blocking; paho reconnects by itself.
    One log line per minute at most. Connects ONLY to the host given in the options."""
    def __init__(self, host, port, user, password, client_id="ih_orchestrator", log=print, subs=()):
        self.host, self.port, self.user, self.password, self.log = host, int(port), user, password, log
        self.client_id, self._handlers, self._last_log, self.connected = client_id, [], 0, False
        self._subs = list(subs)
        self._client = None

    def _say(self, msg):
        if time.time() - self._last_log >= 60:
            self._last_log = time.time(); self.log(msg)

    def subscribe(self, topic_filter, handler):
        self._subs.append(topic_filter); self._handlers.append((topic_filter, handler))
        if self.connected and self._client:
            self._client.subscribe(topic_filter, 1)

    def start(self):
        try:
            import paho.mqtt.client as mqtt
            c = mqtt.Client(client_id=self.client_id)
            c.username_pw_set(self.user, self.password)
            c.reconnect_delay_set(min_delay=2, max_delay=60)

            def on_connect(cl, ud, flags, rc):
                self.connected = (rc == 0)
                if rc == 0:
                    for s in self._subs:
                        cl.subscribe(s, 1)
                    self._say("mqtt connected")
                else:
                    self._say("mqtt refused (code %s)" % rc)

            def on_disconnect(cl, ud, rc):
                self.connected = False
                self._say("mqtt disconnected")

            def on_message(cl, ud, msg):
                try:
                    text = msg.payload.decode("utf-8", "replace")
                    for f, h in list(self._handlers):
                        if topic_matches(f, msg.topic):
                            h(msg.topic, text)
                except Exception as e:
                    self._say("mqtt handler error: %s" % type(e).__name__)
            c.on_connect, c.on_disconnect, c.on_message = on_connect, on_disconnect, on_message
            self._client = c
            c.connect_async(self.host, self.port, keepalive=60)
            c.loop_start()
            return True
        except Exception as e:
            self._say("mqtt start failed: %s" % type(e).__name__)
            return False

    def publish(self, topic, payload):
        if not (self.connected and self._client):
            return False
        try:
            return self._client.publish(topic, payload, qos=1).rc == 0
        except Exception:
            return False


def topic_matches(flt, topic):
    f, t = flt.split("/"), topic.split("/")
    for i, p in enumerate(f):
        if p == "#":
            return True
        if i >= len(t) or (p != "+" and p != t[i]):
            return False
    return len(f) == len(t)


def build(options, data_dir, log=print):
    """Create (Scheduler, None) when enabled and configured, else (None, reason)."""
    if not options.get("enable_scheduler", False):
        return None, "enable_scheduler is false"
    host, user, pw = str(options.get("mqtt_host") or "").strip(), str(options.get("mqtt_user") or "").strip(), str(options.get("mqtt_password") or "")
    if not (host and user and pw):
        return None, "mqtt_host/mqtt_user/mqtt_password not set"
    try:
        port = int(options.get("mqtt_port") or 1883)
    except (TypeError, ValueError):
        return None, "bad mqtt_port"
    set_default_tz(options.get("default_timezone"))
    bus = PahoBus(host, port, user, pw, log=log, subs=[DONE_FILTER])
    s = Scheduler(bus, os.path.join(data_dir, "schedule_state.json"), log=log, keys=parse_keys(options.get("module_keys")))
    s.log_payloads = options.get("log_payloads") is True
    bus._handlers.append((DONE_FILTER, s.handle_message))
    return s, None
