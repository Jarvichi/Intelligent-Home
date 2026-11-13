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
        orig = main.ask; main.ask = lambda o, b: "Yes there is a parcel"
        try:
            done = set(); main.scan(opts, done, log); main.scan(opts, done, log)
        finally:
            main.ask = orig
        rows = [json.loads(l) for l in open(log)]
        self.assertEqual([r["file"] for r in rows], ["front_door_1.jpg"]); self.assertEqual(rows[0]["verdict"], "yes")

    def test_ollama_down_is_retried_not_logged(self):
        d = tempfile.mkdtemp(); log = os.path.join(d, "v.jsonl"); p = os.path.join(d, "front_door_1.jpg")
        open(p, "wb").write(b"x"); os.utime(p, (time.time() - 600, time.time() - 600))
        opts = {"snapshot_dir": d, "cameras": "front_door", "recheck_after_seconds": 0, "model": "m", "ollama_url": "http://x", "timeout_seconds": 5}
        def boom(o, b): raise OSError("down")
        orig = main.ask; main.ask = boom
        try:
            done = set(); main.scan(opts, done, log)
        finally:
            main.ask = orig
        self.assertFalse(os.path.exists(log)); self.assertEqual(done, set()); self.assertFalse(main.STATE["ollama_ok"])


if __name__ == "__main__":
    unittest.main()
