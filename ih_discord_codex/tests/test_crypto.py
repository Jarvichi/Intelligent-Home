"""Cross-check encrypted IH Discord transport payloads against the Codex receiver."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TRANSPORT_NAME = "addon_discord" if (REPO / "addon_discord").is_dir() else "ih_discord"
CODEX_NAME = "addon_discord_codex" if (REPO / "addon_discord_codex").is_dir() else "ih_discord_codex"
TRANSPORT_APP = REPO / TRANSPORT_NAME / "app"
CODEX_APP = REPO / CODEX_NAME / "app"
sys.path.insert(0, str(TRANSPORT_APP))
sys.path.insert(0, str(CODEX_APP))
import bus_client as B
import bus_crypto as TC
import busside

spec = importlib.util.spec_from_file_location("codex_crypto", CODEX_APP / "bus_crypto.py")
MC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(MC)
main_spec = importlib.util.spec_from_file_location("codex_main_test", CODEX_APP / "main.py")
CM = importlib.util.module_from_spec(main_spec)
main_spec.loader.exec_module(CM)

KEY = "test-signing-key-with-more-than-32-characters"
MODULE = "ih_discord_codex"
CHANNEL = "codex"
TOPIC_IN = B.topic_in(MODULE, CHANNEL)


class Bus:
    def __init__(self): self.sent, self.subs = [], []
    def publish(self, topic, payload, retain=False):
        self.sent.append((topic, payload)); return True
    def subscribe(self, topic, handler): self.subs.append((topic, handler))
    def start(self): return True
    def deliver(self, topic, payload):
        for filt, handler in self.subs:
            if filt == topic: handler(topic, payload)


class Router:
    def __init__(self): self.channels = {CHANNEL: "test-channel"}
    def remove_module(self, module): pass
    def add_claim(self, *args, **kwargs): pass
    def select(self, event): return []
    def allowed(self): return {"owner-id"}
    def resolve(self, key): return self.channels.get(key)


class Discord:
    def __init__(self): self.requests = []
    def req(self, method, path, body=None):
        self.requests.append((method, path, body)); return 200, {"id": "test-message"}


class CryptoTests(unittest.TestCase):
    def test_field_round_trip_is_bound_to_topic_and_operation(self):
        body = {"op_id": "one", "channel_key": CHANNEL, "op": "reply"}
        envelope = TC.encrypt_field(KEY, MODULE, B.topic_out(MODULE), body, "text", "private reply")
        self.assertEqual(MC.decrypt_field(KEY, MODULE, B.topic_out(MODULE), body, "text", envelope), "private reply")
        with self.assertRaises(ValueError):
            MC.decrypt_field(KEY, MODULE, B.topic_res(MODULE), body, "text", envelope)
        changed = dict(body, op_id="two")
        with self.assertRaises(ValueError):
            MC.decrypt_field(KEY, MODULE, B.topic_out(MODULE), changed, "text", envelope)

    def test_private_event_wire_contains_no_private_fields(self):
        event = {"type": "message", "id": "m:123", "msg_id": "123", "channel_key": CHANNEL,
                 "author_id": "owner-id", "author_name": "Owner Name", "allowed": True,
                 "content": "private prompt text", "msg_ts": 42, "age_s": 1,
                 "reply_to": None, "attachments": {"count": 0, "kinds": []}, "attempt": 1}
        protected = TC.encrypt_private_event(KEY, MODULE, TOPIC_IN, event)
        wire = B.encode(KEY, "in", MODULE, TOPIC_IN, protected)
        for private in ("private prompt text", "Owner Name", "owner-id"):
            self.assertNotIn(private, wire)
        code, opened = B.open_message(KEY, "in", MODULE, TOPIC_IN, wire)
        self.assertEqual(code, "ok")
        self.assertEqual(MC.decrypt_private_event(KEY, MODULE, TOPIC_IN, opened)["content"], "private prompt text")

    def test_codex_receiver_decrypts_before_durable_ack(self):
        bus = Bus(); order = []; client = CM.DurableClient(MODULE, KEY, bus, threaded=False)
        client.on_message(CHANNEL, lambda event: order.append(("stored", event["content"], event["author_id"])) or True)
        body = {"type": "message", "id": "m:123", "msg_id": "123", "channel_key": CHANNEL,
                "author_id": "owner-id", "author_name": "Owner Name", "allowed": True,
                "content": "private prompt text", "msg_ts": 42, "age_s": 1,
                "reply_to": None, "attachments": {"count": 0, "kinds": []}}
        protected = TC.encrypt_private_event(KEY, MODULE, TOPIC_IN, body)
        raw = B.encode(KEY, "in", MODULE, TOPIC_IN, protected)
        client._on_in(TOPIC_IN, raw)
        self.assertEqual(order, [("stored", "private prompt text", "owner-id")])
        ack = json.loads(bus.sent[-1][1])
        self.assertEqual(ack["ids"], ["123"])
        self.assertNotIn("private prompt text", bus.sent[-1][1])

    def test_codex_receiver_rejects_plaintext_inbound_without_ack(self):
        bus = Bus(); received = []; client = CM.DurableClient(MODULE, KEY, bus, threaded=False)
        client.on_message(CHANNEL, lambda event: received.append(event) or True)
        body = {"type": "message", "id": "m:123", "msg_id": "123", "channel_key": CHANNEL,
                "allowed": True, "author_id": "owner-id", "content": "plaintext"}
        raw = B.encode(KEY, "in", MODULE, TOPIC_IN, body)
        client._on_in(TOPIC_IN, raw)
        self.assertEqual(received, [])
        self.assertEqual(bus.sent, [])

    def test_codex_replies_are_encrypted_before_publish(self):
        bus = Bus(); client = CM.DurableClient(MODULE, KEY, bus, threaded=False)
        client.post(CHANNEL, "private reply text", reply_to="123", wait=0)
        topic, raw = bus.sent[-1]
        code, body = B.open_message(KEY, "out", MODULE, topic, raw)
        self.assertEqual(code, "ok")
        self.assertNotIn("text", body)
        self.assertNotIn("private reply text", raw)
        self.assertEqual(MC.decrypt_field(KEY, MODULE, topic, body, "text", body["text_encrypted"]), "private reply text")

    def test_transport_encrypts_private_event_and_decrypts_reply(self):
        bus, router, discord = Bus(), Router(), Discord()
        grant = busside.parse_modules([{"name": MODULE, "signing_key": KEY, "channels": [CHANNEL],
                                        "ops": ["reply"], "encrypt_content": True}])[MODULE]
        t = busside.BusTransport(bus, router, discord, {MODULE: grant}, {CHANNEL: "test-channel"},
                                 tempfile.mkdtemp(), threaded=False, is_live=lambda m: (True, "live"))
        t.start(); t.mods[MODULE]["last_hb"] = t.clock()
        t.deliver_message(MODULE, {"id": "123", "channel_key": CHANNEL, "author_id": "owner-id",
                                   "author_name": "Owner Name", "content": "private prompt text", "msg_ts": t.clock()}, True)
        topic, raw = bus.sent[-1]
        code, body = B.open_message(KEY, "in", MODULE, topic, raw)
        self.assertEqual(code, "ok")
        for private in ("private prompt text", "Owner Name", "owner-id"):
            self.assertNotIn(private, raw)
        self.assertEqual(MC.decrypt_private_event(KEY, MODULE, topic, body)["content"], "private prompt text")

        captured = []
        t._run_op = lambda module, g, d: captured.append(d)
        out_topic = B.topic_out(MODULE)
        op = {"op": "reply", "op_id": "reply-1", "channel_key": CHANNEL, "reply_to": "123"}
        op["text_encrypted"] = TC.encrypt_field(KEY, MODULE, out_topic, op, "text", "private reply text")
        out_raw = B.encode(KEY, "out", MODULE, out_topic, op)
        self.assertNotIn("private reply text", out_raw)
        t._on_out(out_topic, out_raw)
        self.assertEqual(captured[0]["text"], "private reply text")

    def test_encrypted_grant_rejects_plaintext_reply_operation(self):
        bus, router, discord = Bus(), Router(), Discord()
        grant = busside.parse_modules([{"name": MODULE, "signing_key": KEY, "channels": [CHANNEL],
                                        "ops": ["reply"], "encrypt_content": True}])[MODULE]
        t = busside.BusTransport(bus, router, discord, {MODULE: grant}, {CHANNEL: "test-channel"},
                                 tempfile.mkdtemp(), threaded=False)
        topic = B.topic_out(MODULE)
        raw = B.encode(KEY, "out", MODULE, topic, {"op": "reply", "op_id": "reply-2", "channel_key": CHANNEL, "text": "plaintext"})
        t._on_out(topic, raw)
        self.assertEqual(t.rejected["unencrypted_content"], 1)
        self.assertEqual(discord.requests, [])

    def test_encrypted_module_payloads_stay_out_of_transport_logs(self):
        bus, router, discord, lines = Bus(), Router(), Discord(), []
        grant = busside.parse_modules([{"name": MODULE, "signing_key": KEY, "channels": [CHANNEL],
                                        "ops": ["reply"], "encrypt_content": True}])[MODULE]
        t = busside.BusTransport(bus, router, discord, {MODULE: grant}, {CHANNEL: "test-channel"},
                                 tempfile.mkdtemp(), threaded=False, log=lines.append, log_payloads=True)
        t._invocation_line(MODULE, "reply", {"op_id": "reply-3", "channel_key": CHANNEL, "text": "private prompt text"},
                           {"ok": True, "text": "private response text"}, 0)
        self.assertNotIn("private prompt text", " ".join(lines))
        self.assertNotIn("private response text", " ".join(lines))


if __name__ == "__main__": unittest.main()
