#!/usr/bin/env python3
"""Module-side helper for the Intelligent Home scheduler. stdlib; paho-mqtt (1.6.1) is imported only by PahoBus.
Use:  c = ScheduleClient("myjobs", bus); c.on("nightly_cleanup", fn); c.start()
fn(payload) gets {id, due_at, attempt}; return normally (or True) = ok, raise or return False = not ok.
Jobs MUST be idempotent: the scheduler may send the same id again (retry) or run a missed job late (catch-up).
This client answers a repeated id it already finished with the old result instead of running it again.
SIGNING (0.4.1): the client needs `signing_key` (shared with the orchestrator only). Every due message is verified (HMAC, age, id not seen);
bad ones are ignored and counted in `rejected`. Done replies are signed. No key = inactive (reason 'no signing key'). Keys are never logged.
A module may also run a late job itself on start; it does not need the scheduler for that."""
import hashlib, hmac, json, re, threading, time
from collections import OrderedDict

ROOT = "ih/schedule"
SEEN_MAX = 200
EXTRA_AGE_S = 60


def sign(key, text):
    return hmac.new(key.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class ScheduleClient:
    def __init__(self, module, bus, log=print, threaded=True, keep=50, signing_key="", clock=time.time):
        if not NAME_RE.match(module):
            raise ValueError("bad module name")
        self.module, self.bus, self.log, self.threaded, self.keep = module, bus, log, threaded, keep
        self.handlers, self.results, self.running = {}, {}, set()  # results: id -> ok
        self.key = str(signing_key or "").strip()
        self.clock, self.rejected, self.windows = clock, 0, {}
        self.seen = OrderedDict()  # (id, attempt) pairs already accepted (LRU)
        self.lock = threading.Lock()

    @property
    def active(self):
        return bool(self.key)

    def inactive_reason(self):
        return "" if self.key else "no signing key"

    def status(self):
        """Counts only: never keys, signatures or message content."""
        return {"active": self.active, "reason": self.inactive_reason(), "rejected": self.rejected}

    def topic(self, job):
        return "%s/%s/%s" % (ROOT, self.module, job)

    def on(self, job, fn, window_s=300):
        if not NAME_RE.match(job):
            raise ValueError("bad job name")
        self.handlers[job] = fn
        self.windows[job] = int(window_s)
        self.bus.subscribe(self.topic(job), self._message)

    def start(self):
        if not self.key:
            self.log("schedule: inactive (no signing key)")
            return False
        return self.bus.start()

    def _message(self, topic, payload):
        parts = topic.split("/")
        if len(parts) != 4 or parts[:2] != ["ih", "schedule"] or parts[2] != self.module or parts[3] not in self.handlers:
            return
        job = parts[3]
        if not self.key:
            return
        try:
            d = json.loads(payload)
            jid, sig, due_at, attempt = d["id"], d["sig"], d["due_at"], d["attempt"]
            if not (isinstance(jid, str) and isinstance(sig, str) and d.get("v") == 1 and not isinstance(due_at, bool)
                    and isinstance(due_at, (int, float)) and not isinstance(attempt, bool) and isinstance(attempt, int)):
                raise ValueError
        except Exception:
            self.rejected += 1; self.log("schedule: rejected a message on %s" % topic); return
        want = sign(self.key, "v1|%s|%s|%s|%d|%d" % (self.module, job, jid, int(due_at), attempt))
        if not hmac.compare_digest(sig, want) or self.clock() - due_at > self.windows.get(job, 300) + EXTRA_AGE_S:
            self.rejected += 1; self.log("schedule: rejected a message on %s" % topic); return
        with self.lock:
            if (jid, attempt) in self.seen:  # each (id, attempt) is accepted once; a retry has a new attempt number
                self.rejected += 1; return
            self.seen[(jid, attempt)] = True
            while len(self.seen) > SEEN_MAX:
                self.seen.popitem(last=False)
            if jid in self.results:  # already ran: repeat the answer, do not run again
                ok, run = self.results[jid], False
            elif jid in self.running:
                return  # still running; the scheduler will retry later if needed
            else:
                self.running.add(jid); ok, run = None, True
        if not run:
            self._done(job, jid, ok); return
        if self.threaded:
            threading.Thread(target=self._run, args=(job, jid, d), daemon=True).start()
        else:
            self._run(job, jid, d)

    def _run(self, job, jid, d):
        try:
            ok = self.handlers[job](d) is not False
        except Exception as e:
            self.log("schedule job %s failed: %s" % (job, type(e).__name__)); ok = False
        with self.lock:
            self.running.discard(jid)
            if ok:  # only successes are remembered, so a failed id runs again on retry
                self.results[jid] = True
                while len(self.results) > self.keep:
                    self.results.pop(next(iter(self.results)))
        self._done(job, jid, ok)

    def _done(self, job, jid, ok):
        try:
            ts = int(self.clock()); ok = bool(ok)
            sig = sign(self.key, "v1done|%s|%s|%s|%d|%d" % (self.module, job, jid, 1 if ok else 0, ts))
            self.bus.publish(self.topic(job) + "/done", json.dumps({"id": jid, "ok": ok, "ts": ts, "sig": sig}))
        except Exception:
            self.log("schedule: could not publish done")


class PahoBus:
    """Same interface as the orchestrator's bus. Connects only to the host you pass. Not covered by tests (needs a broker)."""
    def __init__(self, host, port, user, password, client_id, log=print):
        self.host, self.port, self.user, self.password, self.client_id, self.log = host, int(port), user, password, client_id, log
        self.connected, self._subs, self._c, self._last = False, [], None, 0

    def _say(self, m):
        if time.time() - self._last >= 60:
            self._last = time.time(); self.log(m)

    def subscribe(self, topic, handler):
        self._subs.append((topic, handler))
        if self.connected and self._c:
            self._c.subscribe(topic, 1)

    def start(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self._say("paho-mqtt not installed"); return False
        c = mqtt.Client(client_id=self.client_id)
        c.username_pw_set(self.user, self.password)
        c.reconnect_delay_set(min_delay=2, max_delay=60)

        def on_connect(cl, ud, flags, rc):
            self.connected = rc == 0
            for t, _ in self._subs:
                cl.subscribe(t, 1)
            self._say("mqtt connected" if rc == 0 else "mqtt refused (%s)" % rc)

        def on_disconnect(cl, ud, rc):
            self.connected = False

        def on_message(cl, ud, msg):
            for t, h in list(self._subs):
                if t == msg.topic:
                    try:
                        h(msg.topic, msg.payload.decode("utf-8", "replace"))
                    except Exception:
                        self._say("mqtt handler error")
        c.on_connect, c.on_disconnect, c.on_message = on_connect, on_disconnect, on_message
        self._c = c
        c.connect_async(self.host, self.port, keepalive=60)
        c.loop_start()
        return True

    def publish(self, topic, payload):
        if not (self.connected and self._c):
            return False
        return self._c.publish(topic, payload, qos=1).rc == 0
