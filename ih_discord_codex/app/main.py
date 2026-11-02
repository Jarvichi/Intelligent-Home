#!/usr/bin/env python3
"""Private Codex worker behind the existing signed IH Discord transport.

The transport authenticates messages; this service applies a second author and
channel allowlist. It stores the event before ACK, never logs message text, and
holds ambiguous model or delivery outcomes for explicit review.
"""
import json
import logging
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("IHB_DATA", "/data"))
OPTIONS = DATA / "options.json"
WORKSPACE = Path(os.environ.get("CODEX_WORKSPACE", "/config/workspace"))
CODEX_HOME = Path(os.environ.get("CODEX_HOME", "/config/codex-home"))
PORT = int(os.environ.get("IHB_PORT", "8804"))
MAX_REPLY = 1800
LOG = logging.getLogger("ih_discord_codex")

sys.path.insert(0, str(HERE))
import bus_client
import bus_crypto as crypto
from bridge import Bridge


def load_options():
    try:
        data = json.loads(OPTIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    def text(key, default=""):
        value = data.get(key, default)
        return value.strip() if isinstance(value, str) else default
    channel_keys = data.get("channel_keys")
    user_ids = data.get("allowed_user_ids")
    return {
        "module": text("module_name", "ih_discord_codex"),
        "key": text("signing_key"),
        "channel_keys": [str(x).strip() for x in channel_keys if isinstance(x, str) and x.strip()] if isinstance(channel_keys, list) else [],
        "users": [str(x).strip() for x in user_ids if str(x).strip().isdigit()] if isinstance(user_ids, list) else [],
        "shadow": data.get("shadow") is not False,
        "mqtt_host": text("mqtt_host", "core-mosquitto"),
        "mqtt_port": int(data.get("mqtt_port", 1883)),
        "mqtt_user": text("mqtt_user", "ih_discord_codex"),
        "mqtt_password": text("mqtt_password"),
        "codex_home": str(CODEX_HOME),
        "workspace": str(WORKSPACE),
    }


class DurableClient(bus_client.ModuleClient):
    """ModuleClient with ACK-after-durable-receipt for this persistent worker."""
    def _publish(self, kind, topic, body):
        # Protect generated replies on MQTT as well as inbound channel content.
        safe = dict(body)
        if kind == "out" and safe.get("op") in ("post", "reply", "edit") and "text" in safe:
            safe["text_encrypted"] = crypto.encrypt_field(
                self.key, self.module, topic, safe, "text", safe.pop("text"))
        return super()._publish(kind, topic, safe)

    def _on_in(self, topic, raw):
        parsed = bus_client.parse_topic(topic)
        code, event = bus_client.open_message(self.key, "in", self.module, topic, raw,
                                              self.clock, guard=None)
        if code != "ok":
            self._reject(code, topic)
            return
        if event.get("type") == "message":
            try:
                event = crypto.decrypt_private_event(self.key, self.module, topic, event)
            except ValueError:
                self._reject("encrypted_content_invalid", topic)
                return
        handler = self.handlers.get(parsed[2]) if parsed else None
        if handler is None:
            return
        mid = event.get("msg_id") or event.get("id")
        try:
            # True means the exact Discord message is durably recorded (or was
            # recorded before). Exceptions and failed writes remain unacked.
            stored = handler(event)
        except Exception as exc:
            self.log("inbox persistence failed (%s)" % type(exc).__name__)
            return
        if stored is True and mid:
            self._publish("ack", bus_client.topic_ack(self.module), {"ids": [str(mid)]})


class CodexProcessRunner:
    def __init__(self, workspace, codex_home, timeout_s=240):
        self.workspace, self.codex_home, self.timeout_s = str(workspace), str(codex_home), timeout_s
        self._lock = threading.Lock()
        self._active = None

    def cancel(self):
        with self._lock:
            proc = self._active
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def run(self, thread, text, save_thread):
        payload = json.dumps({"thread": thread, "text": text, "workspace": self.workspace,
                              "codex_home": self.codex_home}).encode("utf-8")
        safe_env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
        proc = subprocess.Popen([sys.executable, str(HERE / "codex_worker.py")],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, cwd=self.workspace,
                                env=safe_env, start_new_session=True)
        with self._lock:
            self._active = proc
        answer = None
        try:
            proc.stdin.write(payload + b"\n")
            proc.stdin.close()
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + self.timeout_s
                while time.monotonic() < deadline:
                    events = selector.select(max(0.05, deadline - time.monotonic()))
                    if not events:
                        break
                    line = proc.stdout.readline()
                    if not line:
                        break
                    record = json.loads(line)
                    if record.get("type") == "thread":
                        save_thread(record["id"])
                    elif record.get("type") == "answer":
                        answer = record.get("text")
                    elif record.get("type") == "error":
                        raise RuntimeError("Codex runtime error (%s)" % record.get("kind", "unknown"))
            try:
                code = proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                code = None
            if answer and code == 0:
                return answer
            raise RuntimeError("Codex turn not confirmed")
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            with self._lock:
                if self._active is proc:
                    self._active = None


class CommandRunner:
    def __init__(self, bridge, codex_runner, channel):
        self.bridge, self.codex, self.channel = bridge, codex_runner, channel

    def run(self, thread, text, save_thread):
        command = text.strip().lower()
        if command == "/help":
            return "Send a coding request here. /status shows queue state; /new starts a fresh thread and clears uncertain pending work."
        if command == "/status":
            state = self.bridge.status()
            jobs = ", ".join("%s %s" % (count, name) for name, count in sorted(state["jobs"].items())) or "no queued work"
            delivery = ", ".join("%s %s" % (count, name) for name, count in sorted(state["delivery"].items())) or "no replies pending"
            return "Codex status: %s. Replies: %s." % (jobs, delivery)
        if command == "/new":
            self.bridge.abandon_uncertain(self.channel)
            return "Started a fresh Codex thread. Any interrupted turn or uncertain reply was held and will not be replayed."
        return self.codex.run(thread, text, save_thread)


class Service:
    def __init__(self, options, bridge=None, bus=None):
        self.opts = options
        self.started = time.time()
        self.bridge = bridge or Bridge(DATA / "bridge.sqlite3", options["channel_keys"], options["users"])
        self.bus = bus
        self.client = None
        raw_runner = CodexProcessRunner(options["workspace"], options["codex_home"])
        self.runner = CommandRunner(self.bridge, raw_runner, options["channel_keys"][0] if options["channel_keys"] else "")
        self.stop = threading.Event()
        self.errors = 0
        self.last_result = None

    def receive(self, event):
        if event.get("type") != "message":
            return False
        ck = str(event.get("channel_key") or "")
        uid = str(event.get("author_id") or "")
        content = event.get("content")
        safe = {"channel": ck, "author": uid, "id": str(event.get("msg_id") or ""),
                "text": content, "bot": False if not event.get("author_bot") and not event.get("is_bot") else True}
        return self.bridge.accept(safe)

    def run_loop(self):
        while not self.stop.is_set():
            try:
                if self.bridge.run_one(self.runner):
                    self.last_result = "completed"
                    continue
            except Exception as exc:
                self.errors += 1
                LOG.info("Codex queue step failed (%s)", type(exc).__name__)
            self.stop.wait(0.5)

    def send_loop(self):
        while not self.stop.is_set():
            try:
                def sender(channel, text, reply_to=None, dedupe_key=None):
                    if self.client is None:
                        return None
                    if self.opts["shadow"]:
                        self.client.dry = True
                    result = self.client.reply(channel, text, reply_to=reply_to, tag="codex")
                    if isinstance(result, dict) and result.get("code") == "dry":
                        return "dry"
                    if isinstance(result, dict) and result.get("ok") and result.get("discord_id"):
                        return str(result["discord_id"])
                    return None
                if self.bridge.deliver_one(sender):
                    continue
            except Exception as exc:
                self.errors += 1
                LOG.info("outbox step failed (%s)", type(exc).__name__)
            self.stop.wait(0.5)

    def start(self):
        if len(self.opts["channel_keys"]) != 1 or len(self.opts["users"]) != 1:
            raise RuntimeError("no channel or author allowlist configured")
        if not self.opts["mqtt_password"]:
            raise RuntimeError("no MQTT credential configured")
        auth_file = CODEX_HOME / "auth.json"
        if not auth_file.is_file():
            raise RuntimeError("Codex ChatGPT login is not installed in this add-on")
        os.chmod(auth_file, 0o600)
        self.bus = self.bus or bus_client.PahoBus(self.opts["mqtt_host"], self.opts["mqtt_port"],
             self.opts["mqtt_user"], self.opts["mqtt_password"], "ih_discord_codex_mod",
             log=lambda msg: LOG.info("bus: %s", msg))
        self.client = DurableClient(self.opts["module"], self.opts["key"], self.bus,
             version="0.1.1", log=lambda msg: LOG.info("%s", msg), threaded=True,
             ops_needed=["reply"], dry=self.opts["shadow"])
        for channel in self.opts["channel_keys"]:
            self.client.on_message(channel, self.receive, any=True)
        if not self.client.start():
            raise RuntimeError("signed Discord bus did not start")
        threading.Thread(target=self.run_loop, name="codex-queue", daemon=True).start()
        threading.Thread(target=self.send_loop, name="codex-outbox", daemon=True).start()

    def status(self):
        bus = self.bus
        return {"name": self.opts["module"], "version": "0.1.1", "healthy": self.client is not None,
                "shadow": self.opts["shadow"], "channel_keys": list(self.opts["channel_keys"]),
                "allowed_users": len(self.opts["users"]), "codex_home_ready": (CODEX_HOME / "auth.json").is_file(),
                "workspace_exists": WORKSPACE.is_dir(), "bus_connected": bool(bus and bus.connected),
                "bus_active": bool(self.client and self.client.active), "uptime_s": int(time.time()-self.started),
                "queue": self.bridge.status(), "last_result": self.last_result, "errors": self.errors}

    def close(self):
        self.stop.set()
        self.runner.codex.cancel()
        if self.client:
            self.client.stop()
        self.bridge.close()


def handler(service):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                ok = bool(service.client and service.client.active and service.bus and service.bus.connected)
                body = b"ok" if ok else b"not ok"
                self.send_response(200 if ok else 503)
                self.send_header("Content-Type", "text/plain")
            elif self.path == "/status":
                body = json.dumps(service.status()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            else:
                self.send_error(404)
                return
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    return H


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    os.umask(0o077)
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DATA, 0o700)
    WORKSPACE.mkdir(mode=0o700, parents=True, exist_ok=True)
    CODEX_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(WORKSPACE, 0o700)
    os.chmod(CODEX_HOME, 0o700)
    options = load_options()
    service = Service(options)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), handler(service))
    try:
        service.start()
        threading.Thread(target=server.serve_forever, name="status", daemon=True).start()
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        service.close()


if __name__ == "__main__":
    main()
