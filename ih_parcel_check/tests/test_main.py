import json, os, sys, tempfile, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import main


class ParcelCheck(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(main.parse_verdict("Yes, a box"), "yes")
        self.assertEqual(main.parse_verdict(" No."), "no")
        self.assertEqual(main.parse_verdict("maybe"), "unsure")
        self.assertEqual(main.parse_verdict(None), "unsure")

    def test_wanted(self):
        self.assertTrue(main.wanted("front_door_20261113.jpg", ["front_door"]))
        self.assertFalse(main.wanted("back_garden_1.jpg", ["front_door"]))
        self.assertFalse(main.wanted("front_door_1.png", ["front_door"]))

    def test_scan_logs_once_and_skips_fresh_pictures(self):
        d = tempfile.mkdtemp(); log = os.path.join(d, "v.jsonl")
        old, fresh = os.path.join(d, "front_door_1.jpg"), os.path.join(d, "front_door_2.jpg")
        for p in (old, fresh):
            open(p, "wb").write(b"x")
        os.utime(old, (time.time() - 600, time.time() - 600))
        opts = {"snapshot_dir": d, "cameras": "front_door", "recheck_after_seconds": 120, "model": "m", "ollama_url": "http://x", "timeout_seconds": 5}
        orig, om = main.ask, main.model_ready; main.ask = lambda o, b: "Yes there is a parcel"; main.model_ready = lambda o: True
        try:
            done = set(); main.scan(opts, done, log); main.scan(opts, done, log)
        finally:
            main.ask, main.model_ready = orig, om
        rows = [json.loads(l) for l in open(log)]
        self.assertEqual([r["file"] for r in rows], ["front_door_1.jpg"]); self.assertEqual(rows[0]["verdict"], "yes")

    def test_ollama_down_is_retried_not_logged(self):
        d = tempfile.mkdtemp(); log = os.path.join(d, "v.jsonl"); p = os.path.join(d, "front_door_1.jpg")
        open(p, "wb").write(b"x"); os.utime(p, (time.time() - 600, time.time() - 600))
        opts = {"snapshot_dir": d, "cameras": "front_door", "recheck_after_seconds": 0, "model": "m", "ollama_url": "http://x", "timeout_seconds": 5}
        def boom(o, b): raise OSError("down")
        orig, om = main.ask, main.model_ready; main.ask = boom; main.model_ready = lambda o: True
        try:
            done = set(); main.scan(opts, done, log)
        finally:
            main.ask, main.model_ready = orig, om
        self.assertFalse(os.path.exists(log)); self.assertEqual(done, set()); self.assertFalse(main.STATE["ollama_ok"])

    def test_nothing_is_checked_until_the_model_is_there(self):
        d = tempfile.mkdtemp(); log = os.path.join(d, "v.jsonl"); p = os.path.join(d, "front_door_1.jpg")
        open(p, "wb").write(b"x"); os.utime(p, (time.time() - 600, time.time() - 600))
        opts = {"snapshot_dir": d, "cameras": "front_door", "recheck_after_seconds": 0, "model": "moondream", "ollama_url": "http://127.0.0.1:9", "timeout_seconds": 5}
        called = []; orig = main.ask; main.ask = lambda o, b: called.append(1) or "yes"
        try:
            main.scan(opts, set(), log)       # engine unreachable: nothing asked, nothing logged
        finally:
            main.ask = orig
        self.assertEqual(called, []); self.assertFalse(os.path.exists(log)); self.assertFalse(main.STATE["ollama_ok"]); self.assertIn("not reachable", main.STATE["last_error"])


if __name__ == "__main__":
    unittest.main()
