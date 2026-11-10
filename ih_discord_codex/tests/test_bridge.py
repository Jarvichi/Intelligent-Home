import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import bus_client
import bus_crypto
from bridge import Bridge
from main import CommandRunner, DurableClient


class Runner:
    def run(self, thread, text, save_thread):
        save_thread(thread or "thread-one")
        return "Reviewed answer"


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.bridge = Bridge(self.path, channels=["codex"], users=["owner"])

    def tearDown(self):
        self.bridge.close()
        self.tmp.cleanup()

    def event(self, message="1", **overrides):
        event = {"channel": "codex", "author": "owner", "id": message,
                 "text": "Review this", "bot": False}
        event.update(overrides)
        return event

    def test_default_deny(self):
        closed = Bridge(Path(self.tmp.name) / "closed.sqlite3")
        self.assertFalse(closed.accept(self.event()))
        closed.close()

    def test_author_channel_and_bot_are_enforced_durably(self):
        self.assertFalse(self.bridge.accept(self.event("other", channel="other")))
        for mid, values in [("2", {"author": "other"}), ("3", {"bot": True}),
                            ("4", {"text": ""})]:
            self.assertTrue(self.bridge.accept(self.event(mid, **values)))
        self.assertEqual(self.bridge.status()["jobs"], {"rejected": 3})

    def test_duplicate_is_ackable_after_restart(self):
        self.assertTrue(self.bridge.accept(self.event()))
        self.bridge.close()
        self.bridge = Bridge(self.path, channels=["codex"], users=["owner"])
        self.assertTrue(self.bridge.accept(self.event()))
        self.assertEqual(self.bridge.status()["jobs"], {"queued": 1})

    def test_thread_and_delivery_receipt(self):
        self.bridge.accept(self.event())
        self.assertTrue(self.bridge.deliver_one(lambda *args, **kwargs: "ack-receipt"))
        self.assertTrue(self.bridge.run_one(Runner()))
        sent = []
        def sender(*args, **kwargs):
            sent.append((args, kwargs))
            return "discord-receipt"
        self.assertTrue(self.bridge.deliver_one(sender))
        self.assertFalse(self.bridge.deliver_one(sender))
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.bridge.status()["delivery"], {"sent": 2})

    def test_ambiguous_delivery_is_never_replayed(self):
        self.bridge.accept(self.event())
        self.bridge.run_one(Runner())
        calls = []
        def uncertain(*args, **kwargs):
            calls.append(1)
            raise TimeoutError("receipt missing")
        self.bridge.deliver_one(uncertain)
        self.assertFalse(self.bridge.deliver_one(uncertain))
        self.assertEqual(calls, [1])
        self.assertEqual(self.bridge.status()["delivery"], {"needs_review": 1})

    def test_shadow_reply_is_recorded_as_dry_not_uncertain(self):
        self.bridge.accept(self.event())
        self.bridge.run_one(Runner())
        self.assertTrue(self.bridge.deliver_one(lambda *args, **kwargs: "dry"))
        self.assertEqual(self.bridge.status()["delivery"], {"dry": 1})
        self.assertFalse(self.bridge.deliver_one(lambda *args, **kwargs: "unexpected-replay"))

    def test_interrupted_turn_is_abandoned_and_notices_owner(self):
        self.bridge.accept(self.event())
        self.assertTrue(self.bridge.deliver_one(lambda *args, **kwargs: "ack-one"))
        with self.bridge.db:
            self.bridge.db.execute("UPDATE jobs SET state='running'")
        self.bridge.close()
        self.bridge = Bridge(self.path, channels=["codex"], users=["owner"])
        self.assertEqual(self.bridge.status()["jobs"], {"abandoned": 1})
        self.bridge.accept(self.event("2"))
        sent=[]
        self.assertTrue(self.bridge.deliver_one(lambda ch,text,**kw: sent.append(text) or "notice"))
        self.assertEqual(sent, ["Restart cut off your last Codex job; send it again if needed."])
        self.assertTrue(self.bridge.deliver_one(lambda *args, **kwargs: "ack-two"))
        self.assertTrue(self.bridge.run_one(Runner()))
        self.assertEqual(self.bridge.status()["jobs"], {"abandoned": 1, "answered": 1})

    def test_existing_needs_review_is_cleared_on_startup(self):
        self.bridge.accept(self.event())
        with self.bridge.db:
            self.bridge.db.execute("UPDATE jobs SET state='needs_review',reason='old uncertainty'")
        self.bridge.close()
        self.bridge = Bridge(self.path, channels=["codex"], users=["owner"])
        row=self.bridge.db.execute("SELECT state,reason,text FROM jobs").fetchone()
        self.assertEqual(row, ("abandoned","restart cut off model turn",""))
        notice=self.bridge.db.execute("SELECT part,text,state FROM outbox WHERE part=-3").fetchone()
        self.assertEqual(notice, (-3,"Restart cut off your last Codex job; send it again if needed.","pending"))

    def test_uncertain_delivery_is_abandoned_and_notice_uses_same_dedupe_key(self):
        self.bridge.accept(self.event())
        with self.bridge.db:
            self.bridge.db.execute("UPDATE outbox SET state='needs_review' WHERE part=-2")
        self.bridge.close()
        self.bridge = Bridge(self.path, channels=["codex"], users=["owner"])
        rows=self.bridge.db.execute("SELECT part,state,text FROM outbox ORDER BY part").fetchall()
        self.assertEqual(rows, [
            (-3,"pending","Restart cut off your last Codex job; send it again if needed."),
            (-2,"abandoned","")])

    def test_process_lock_prevents_concurrent_recovery(self):
        code = "from bridge import Bridge; Bridge(__import__('sys').argv[1])"
        app = Path(__file__).resolve().parents[1] / "app"
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.path)],
                                capture_output=True, text=True, cwd=app,
                                env={**os.environ, "PYTHONPATH": str(app)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BlockingIOError", result.stderr)

    def test_database_mode_is_private(self):
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_bounded_queue_keeps_owner_recovery_commands_available(self):
        for number in range(8):
            self.assertTrue(self.bridge.accept(self.event(str(number))))
        self.assertTrue(self.bridge.accept(self.event("overflow")))
        self.assertTrue(self.bridge.accept(self.event("recover", text="/new")))
        self.assertEqual(self.bridge.status()["jobs"], {"queued": 9, "rejected": 1})
        # Eight accepted prompts each have a visible queued acknowledgement;
        # the overflow gets the queue-full reply instead.
        self.assertEqual(self.bridge.status()["delivery"], {"pending": 9})


class CommandTests(unittest.TestCase):
    def test_new_clears_uncertain_work_and_status_is_content_free(self):
        class FakeBridge:
            def __init__(self): self.cleared=[]
            def status(self): return {"jobs":{"needs_review":1},"delivery":{"needs_review":1},"claude_handoffs":{}}
            def abandon_uncertain(self,channel): self.cleared.append(channel)
        bridge=FakeBridge(); runner=CommandRunner(bridge,object(),"codex")
        self.assertIn("held",runner.run(None,"/new",lambda _:None))
        self.assertEqual(bridge.cleared,["codex"])
        self.assertIn("needs_review",runner.run(None,"/status",lambda _:None))
        self.assertIn("no Claude handoffs",runner.run(None,"/status",lambda _:None))
        self.assertIn("IH code",runner.run(None,"/help",lambda _:None))

    def test_status_handles_missing_claude_handoffs(self):
        class LegacyFakeBridge:
            def status(self): return {"jobs":{},"delivery":{}}
        runner=CommandRunner(LegacyFakeBridge(),object(),"codex")
        self.assertIn("no Claude handoffs",runner.run(None,"/status",lambda _:None))


class DurableAckTests(unittest.TestCase):
    def setUp(self):
        self.bus = bus_client.FakeBus()
        self.key = "0123456789abcdef0123456789abcdef"
        self.client = DurableClient("ih_discord_codex", self.key, self.bus, threaded=False)
        self.client.handlers["codex"] = lambda event: True

    def signed(self, message="42", channel="codex"):
        topic = bus_client.topic_in("ih_discord_codex", channel)
        body = {"type": "message", "msg_id": message, "channel_key": channel,
                "author_id": "owner", "content": "hello", "id": "m:" + message}
        protected = bus_crypto.encrypt_private_event(self.key, "ih_discord_codex", topic, body)
        return topic, bus_client.encode(self.key, "in", "ih_discord_codex", topic, protected)

    def test_ack_follows_durable_accept(self):
        observed = []
        def handler(event):
            observed.append(event["msg_id"])
            return True
        self.client.handlers["codex"] = handler
        topic, raw = self.signed()
        self.client._on_in(topic, raw)
        self.assertEqual(observed, ["42"])
        self.assertEqual([t for t, _ in self.bus.sent], [bus_client.topic_ack("ih_discord_codex")])

    def test_failed_persist_does_not_ack(self):
        self.client.handlers["codex"] = lambda event: (_ for _ in ()).throw(OSError("disk full"))
        topic, raw = self.signed()
        self.client._on_in(topic, raw)
        self.assertEqual(self.bus.sent, [])

    def test_transport_signature_and_configured_key_required(self):
        topic, raw = self.signed()
        self.client.key = ""
        self.client._on_in(topic, raw)
        self.assertEqual(self.bus.sent, [])


if __name__ == "__main__":
    unittest.main()
