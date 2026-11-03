#!/usr/bin/env python3
"""Private Codex worker behind the existing signed IH Discord transport.

The transport authenticates messages; this service applies a second author and
channel allowlist. It stores the event before ACK, never logs message text, and
holds ambiguous model or delivery outcomes for explicit review.
"""
import json
import base64
import logging
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("IHB_DATA", "/data"))
OPTIONS = DATA / "options.json"
WORKSPACE = Path(os.environ.get("CODEX_WORKSPACE", "/config/workspace"))
CODEX_HOME = Path(os.environ.get("CODEX_HOME", "/config/codex-home"))
PORT = int(os.environ.get("IHB_PORT", "8815"))
MAX_REPLY = 1800
BRAIN_MCP_URL = "http://homeassistant:8820/codex-mcp"
LOG = logging.getLogger("ih_discord_codex")
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 4 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_discord_images(items):
    """Fetch only bounded images from Discord's attachment CDN; never follow redirects."""
    images, total = [], 0
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    for item in items[:3] if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url, mime = item.get("url"), item.get("mime")
        try:
            from urllib.parse import urlsplit
            parsed = urlsplit(url if isinstance(url, str) else "")
            port = parsed.port
            advertised = int(item.get("size", 0))
        except (TypeError, ValueError):
            continue
        if (parsed.scheme != "https" or parsed.hostname not in ("cdn.discordapp.com", "media.discordapp.net")
                or port not in (None, 443) or parsed.fragment or len(url) > 4096
                or not parsed.path.startswith("/attachments/") or parsed.username or parsed.password
                or mime not in IMAGE_MIMES or advertised <= 0 or advertised > MAX_IMAGE_BYTES):
            continue
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "IH-Discord-Codex/1"})
            with opener.open(request, timeout=5) as response:
                if response.status != 200 or response.headers.get_content_type() != mime:
                    continue
                data = response.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES or total + len(data) > 8 * 1024 * 1024:
                continue
            signatures = {"image/jpeg": data.startswith(b"\xff\xd8\xff"),
                          "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
                          "image/webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"}
            if not signatures.get(mime):
                continue
            images.append({"mime": mime, "data": data})
            total += len(data)
        except (OSError, ValueError, urllib.error.URLError):
            continue
    return images

sys.path.insert(0, str(HERE))
import bus_client
import bus_crypto as crypto
from bridge import Bridge
from ha_tools import HATools
from workspace import prepare_workspace, branch_status, branch_diff
from code_publish import publish_review_branch, PublishError


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
    review_channel_key = text("review_channel_key", "claude_review")
    if review_channel_key not in ("", "claude_review"):
        raise ValueError("review_channel_key must be empty or the fixed claude_review channel key")
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
        "brain_read_token": text("brain_read_token"),
        "github_repo_token": text("github_repo_token"),
        "review_channel_key": review_channel_key,
        "codex_home": str(CODEX_HOME),
        "workspace": str(WORKSPACE),
    }


def brain_readonly_probe(token):
    """Verify the separately authenticated MCP endpoint exposes exactly two read tools."""
    try:
        for rid, method, params in (
            (1, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                               "clientInfo": {"name": "ih-discord-codex", "version": "0.1.23"}}),
            (2, "tools/list", {}),
        ):
            body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode()
            req = urllib.request.Request(BRAIN_MCP_URL, data=body, method="POST",
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as res:
                result = json.loads(res.read())
            if result.get("error") or result.get("id") != rid:
                return False
            if method == "tools/list":
                names = {x.get("name") for x in result.get("result", {}).get("tools", [])}
                if names != {"brain_search", "brain_read"}:
                    return False
        return True
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        return False


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
    def __init__(self, workspace, codex_home, brain_read_token="", claude_review_enabled=False, timeout_s=240):
        self.workspace, self.codex_home, self.timeout_s = str(workspace), str(codex_home), timeout_s
        self.brain_read_token = str(brain_read_token)
        self.claude_review_enabled = bool(claude_review_enabled)
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
        return self._run(thread, text, [], save_thread)

    def run_with_images(self, thread, text, images, save_thread):
        return self._run(thread, text, images, save_thread)

    def _run(self, thread, text, images, save_thread):
        encoded_images = [{"mime": item["mime"], "data": base64.b64encode(item["data"]).decode("ascii")}
                          for item in images[:3] if item.get("mime") in IMAGE_MIMES and isinstance(item.get("data"), bytes)]
        payload = json.dumps({"thread": thread, "text": text, "images": encoded_images, "workspace": self.workspace,
                              "codex_home": self.codex_home,
                              "brain_read_token": self.brain_read_token,
                              "claude_review_enabled": self.claude_review_enabled}).encode("utf-8")
        safe_env = {k: os.environ[k] for k in ("LANG", "LC_ALL", "TMPDIR") if k in os.environ}
        safe_env["PATH"] = "/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"
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
                    if record.get("type") == "sandbox_probe":
                        LOG.info("sandbox launcher probe: %s", json.dumps({k: record.get(k) for k in
                                 ("path", "exec_ok", "supports_perms", "supports_pid1", "exec_errno")}, sort_keys=True))
                    elif record.get("type") == "thread":
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
    def __init__(self, bridge, codex_runner, channel, ha_tools=None, workspace=None, github_token="", review_channel_key=""):
        self.bridge, self.codex, self.channel, self.ha_tools, self.workspace = bridge, codex_runner, channel, ha_tools, workspace
        self.github_token = github_token
        self.review_channel_key = review_channel_key

    def run(self, thread, text, save_thread):
        command = text.strip().lower()
        if command == "/help":
            return "Ask about Home Assistant, device diagnostics, or IH code. Device changes require `/approve CODE`. `/code status` shows your review branch, `/code diff` prints changes, and `/code submit` opens a PR after a privacy scan. `/status` shows queue state; `/new` starts a fresh thread."
        if command == "/status":
            state = self.bridge.status()
            jobs = ", ".join("%s %s" % (count, name) for name, count in sorted(state["jobs"].items())) or "no queued work"
            delivery = ", ".join("%s %s" % (count, name) for name, count in sorted(state["delivery"].items())) or "no replies pending"
            handoffs = ", ".join("%s %s" % (count, name) for name, count in sorted(state.get("claude_handoffs", {}).items())) or "no Claude handoffs"
            return "Codex status: %s. Replies: %s. Claude task handoffs: %s." % (jobs, delivery, handoffs)
        if command == "/new":
            self.bridge.abandon_uncertain(self.channel)
            return "Started a fresh Codex thread. Any interrupted turn or uncertain reply was held and will not be replayed."
        if command.startswith("/approve"):
            match = re.fullmatch(r"/approve\s+([A-F0-9]{16})", text.strip(), re.I)
            if not match:return "Use `/approve CODE` with the 16-character code from the pending action proposal."
            return self.ha_tools.approve(self.bridge.active_context(), match.group(1))
        if command.startswith("/deny"):
            match = re.fullmatch(r"/deny\s+([A-F0-9]{16})", text.strip(), re.I)
            if not match:return "Use `/deny CODE` with the 16-character code from the pending action proposal."
            return self.ha_tools.deny(self.bridge.active_context(), match.group(1))
        if command == "/code status":
            return branch_status(self.workspace)
        if command == "/code diff":
            return branch_diff(self.workspace)
        if command == "/code submit":
            if not self.github_token:
                return "The private IH code branch is ready locally, but GitHub submission is not configured. Add a fine-grained token for the IH add-on source repository with Contents read/write and Pull requests read/write, then use `/code submit`."
            try:
                result = publish_review_branch(self.workspace, self.github_token)
                if self.review_channel_key:
                    state = self.bridge.queue_claude_handoff(self.bridge.active_context(), "Review and finish this public IH code change.", result["url"])
                    notice = " Claude task handoff: %s." % ("queued" if state == "pending" else state)
                else:
                    notice = " Claude handoff is not configured."
                return "Review pull request created: %s\nBranch: `%s`. Nothing was merged or deployed.%s" % (result["url"], result["branch"], notice)
            except PublishError as exc:
                return str(exc)
        if command.startswith("/code "):
            return "Use `/code status` or `/code diff`. Code stays on the private local review branch and is not merged or deployed."
        answer = self.codex.run(thread, text, save_thread)
        context = self.bridge.active_context()
        plans = self.ha_tools.pending_for(context["message"]) if self.ha_tools and context else []
        if plans:
            plan = plans[0]
            answer += ("\n\nApproval required: I propose %s for `%s`. Nothing has changed yet. "
                       "Send `/approve %s` to do this one step, or `/deny %s` to cancel. The code expires in five minutes." %
                       (plan["domain"] + "." + plan["service"], plan["entity_id"], plan["id"], plan["id"]))
        handoffs = self.bridge.claude_handoffs_for(context["message"]) if context else []
        if handoffs:
            answer += "\n\nClaude task handoff queued."
        return answer

    def run_with_images(self, thread, text, images, save_thread):
        command = text.strip().lower()
        if command.startswith("/"):
            return self.run(thread, text, save_thread)
        answer = self.codex.run_with_images(thread, text, images, save_thread)
        context = self.bridge.active_context()
        plans = self.ha_tools.pending_for(context["message"]) if self.ha_tools and context else []
        if plans:
            plan = plans[0]
            answer += ("\n\nApproval required: I propose %s for `%s`. Nothing has changed yet. "
                       "Send `/approve %s` to do this one step, or `/deny %s` to cancel. The code expires in five minutes." %
                       (plan["domain"] + "." + plan["service"], plan["entity_id"], plan["id"], plan["id"]))
        handoffs = self.bridge.claude_handoffs_for(context["message"]) if context else []
        if handoffs:
            answer += "\n\nClaude task handoff queued."
        return answer


class Service:
    def __init__(self, options, bridge=None, bus=None):
        self.opts = options
        self.started = time.time()
        self.bridge = bridge or Bridge(DATA / "bridge.sqlite3", options["channel_keys"], options["users"])
        self.bus = bus
        self.client = None
        self.brain_readonly_connected = False
        self.brain_thread_policy_reset = False
        self.ha_tools = HATools(self.bridge, review_enabled=bool(options.get("review_channel_key")))
        raw_runner = CodexProcessRunner(options["workspace"], options["codex_home"], options.get("brain_read_token", ""), bool(options.get("review_channel_key")))
        self.runner = CommandRunner(self.bridge, raw_runner, options["channel_keys"][0] if options["channel_keys"] else "", self.ha_tools, options["workspace"], options.get("github_repo_token", ""), options.get("review_channel_key", ""))
        self.stop = threading.Event()
        self.errors = 0
        self.last_result = None

    def receive(self, event):
        if event.get("type") != "message":
            return False
        ck = str(event.get("channel_key") or "")
        uid = str(event.get("author_id") or "")
        content = event.get("content")
        if not isinstance(content, str):
            content = ""
        image_items = event.get("images") if (uid in self.opts["users"] and ck in self.opts["channel_keys"]
                                                and not event.get("author_bot") and not event.get("is_bot")) else []
        images = fetch_discord_images(image_items)
        attachments = event.get("attachments") if isinstance(event.get("attachments"), dict) else {}
        if attachments.get("image_count", 0) and not images:
            notice = "[An attached image could not be safely fetched. Supported formats are JPEG, PNG, and WebP.]"
            content = (content + "\n\n" + notice) if content.strip() else notice
        safe = {"channel": ck, "author": uid, "id": str(event.get("msg_id") or ""),
                "text": content, "images": images, "bot": False if not event.get("author_bot") and not event.get("is_bot") else True}
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
                def review_sender(text):
                    if self.client is None or not self.opts.get("review_channel_key"):
                        return None
                    result = self.client.post(self.opts["review_channel_key"], text, tag="codex-review")
                    if isinstance(result, dict) and result.get("code") == "dry":
                        return "dry"
                    if isinstance(result, dict) and result.get("ok") and result.get("discord_id"):
                        return str(result["discord_id"])
                    return None
                if self.bridge.deliver_claude_handoff(review_sender):
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
        if not self.ha_tools.token:
            raise RuntimeError("Home Assistant API access is not enabled for this add-on")
        self.ha_tools.start()
        if self.opts.get("brain_read_token"):
            self.brain_readonly_connected = brain_readonly_probe(self.opts["brain_read_token"])
            if not self.brain_readonly_connected:
                raise RuntimeError("read-only shareable Brain MCP unavailable or unexpected tool set")
        context_version = "ha-tools-todo-claude-delegation-v2"
        self.brain_thread_policy_reset = self.bridge.ensure_context_version(context_version, self.opts["channel_keys"])
        os.chmod(auth_file, 0o600)
        self.bus = self.bus or bus_client.PahoBus(self.opts["mqtt_host"], self.opts["mqtt_port"],
             self.opts["mqtt_user"], self.opts["mqtt_password"], "ih_discord_codex_mod",
             log=lambda msg: LOG.info("bus: %s", msg))
        self.client = DurableClient(self.opts["module"], self.opts["key"], self.bus,
             version="0.1.23", log=lambda msg: LOG.info("%s", msg), threaded=True,
             ops_needed=["reply"] + (["post"] if self.opts.get("review_channel_key") else []), dry=self.opts["shadow"])
        for channel in self.opts["channel_keys"]:
            self.client.on_message(channel, self.receive, any=True)
        if not self.client.start():
            raise RuntimeError("signed Discord bus did not start")
        threading.Thread(target=self.run_loop, name="codex-queue", daemon=True).start()
        threading.Thread(target=self.send_loop, name="codex-outbox", daemon=True).start()

    def status(self):
        bus = self.bus
        return {"name": self.opts["module"], "version": "0.1.23", "healthy": self.client is not None,
                "shadow": self.opts["shadow"], "channel_keys": list(self.opts["channel_keys"]),
                "allowed_users": len(self.opts["users"]), "codex_home_ready": (CODEX_HOME / "auth.json").is_file(),
                "brain_readonly_configured": bool(self.opts.get("brain_read_token")),
                "brain_readonly_connected": self.brain_readonly_connected,
                "ha_tools_enabled": bool(self.ha_tools.token),
                "claude_handoff_enabled": bool(self.opts.get("review_channel_key")),
                "thread_policy_reset": self.brain_thread_policy_reset,
                "workspace_exists": WORKSPACE.is_dir(), "bus_connected": bool(bus and bus.connected),
                "bus_active": bool(self.client and self.client.active), "uptime_s": int(time.time()-self.started),
                "queue": self.bridge.status(), "last_result": self.last_result, "errors": self.errors}

    def close(self):
        self.stop.set()
        self.runner.codex.cancel()
        self.ha_tools.close()
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
    prepare_workspace(WORKSPACE)
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
