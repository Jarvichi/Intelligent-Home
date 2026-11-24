"""Orchestrator holds a plain trophy event up to 10 min for the trophy name. No network."""
import datetime, json, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import orchestrator as o

TZ = datetime.timezone.utc
T0 = datetime.datetime(2026, 11, 23, 20, 0, 0, tzinfo=TZ)


class Ctx:
    dry = False

    def __init__(self, d, opts):
        self.data_dir, self.opts, self.sent = d, opts, []

    def log(self, m):
        pass

    def ha(self, path, payload):
        self.sent.append((path, payload))

    def open(self, req, timeout=10):
        self.sent.append(("assistant", json.loads(req.data)))
        return _Resp()


class _Resp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


PLAIN = {"time": T0.isoformat(), "kind": "trophy", "tier": "bronze", "game": "GTA VI", "text": "Earned 1 bronze trophy"}
NAMED = {"time": (T0 + datetime.timedelta(minutes=3)).isoformat(), "kind": "trophy_named", "tier": "bronze", "game": "GTA VI",
         "achievement": "Baited my tackle", "text": "Earned bronze trophy: Baited my tackle in GTA VI"}


class HoldTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "feeds"))

    def tearDown(self):
        shutil.rmtree(self.d)

    def run_at(self, events, minutes, names=True):
        with open(os.path.join(self.d, "feeds", "psn_p.json"), "w") as f:
            json.dump({"current": {}, "events": events}, f)
        opts = {"timezone": "UTC", "assistant_url": "http://a", "assistant_token": "t",
                "providers_enabled": ["psn_trophies"] + (["psn_trophy_names"] if names else []),
                "players": [{"key": "p", "speaker": "bot", "psn_online_id": "X"}]}
        ctx = Ctx(self.d, opts)
        o.run(ctx, now=T0 + datetime.timedelta(minutes=minutes))
        return ctx.sent

    def facts(self, sent):
        return [p for path, p in sent if path == "assistant"][0]["facts"]

    def test_plain_held_then_named_arrives(self):
        self.assertEqual(self.run_at([PLAIN], 2), [])
        sent = self.run_at([PLAIN, NAMED], 4)
        self.assertIn("Baited my tackle", sent[0][1]["text"])
        self.assertNotIn("1 bronze trophy", sent[0][1]["text"])
        self.assertEqual(self.facts(sent)["trophies"], "Baited my tackle")

    def test_name_never_arrives_sent_after_10_min(self):
        self.assertEqual(self.run_at([PLAIN], 9), [])
        sent = self.run_at([PLAIN], 11)
        self.assertIn("Earned 1 bronze trophy", sent[0][1]["text"])
        self.assertNotIn("trophies", self.facts(sent))

    def test_name_already_present_sent_at_once(self):
        sent = self.run_at([PLAIN, NAMED], 4)
        self.assertEqual(len(sent), 2)
        self.assertEqual(self.facts(sent)["trophies"], "Baited my tackle")

    def test_names_disabled_no_hold(self):
        sent = self.run_at([PLAIN], 1, names=False)
        self.assertIn("Earned 1 bronze trophy", sent[0][1]["text"])


if __name__ == "__main__":
    unittest.main()
