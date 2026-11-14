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
        opts = {"snapshot_dir": d, "cameras": "front_door", "recheck_after_seconds": 120, "ai_layer_url": "http://x", "api_token": "t", "timeout_seconds": 5}
        orig, om = main.ask, main.layer_ready; main.ask = lambda o, b, n="x": "Yes there is a parcel"; main.layer_ready = lambda o: True
        try:
            done = set(); main.scan(opts, done, log); main.scan(opts, done, log)
        finally:
            main.ask, main.layer_ready = orig, om
        rows = [json.loads(l) for l in open(log)]
        self.assertEqual([r["file"] for r in rows], ["front_door_1.jpg"]); self.assertEqual(rows[0]["verdict"], "yes")

    def test_ai_layer_down_is_retried_not_logged(self):
        d = tempfile.mkdtemp(); log = os.path.join(d, "v.jsonl"); p = os.path.join(d, "front_door_1.jpg")
        open(p, "wb").write(b"x"); os.utime(p, (time.time() - 600, time.time() - 600))
        opts = {"snapshot_dir": d, "cameras": "front_door", "recheck_after_seconds": 0, "ai_layer_url": "http://x", "api_token": "t", "timeout_seconds": 5}
        def boom(o, b, n="x"): raise OSError("down")
        orig, om = main.ask, main.layer_ready; main.ask = boom; main.layer_ready = lambda o: True
        try:
            done = set(); main.scan(opts, done, log)
        finally:
            main.ask, main.layer_ready = orig, om
        self.assertFalse(os.path.exists(log)); self.assertEqual(done, set()); self.assertFalse(main.STATE["ai_layer_ok"])

    def test_nothing_is_checked_until_the_layer_answers(self):
        d = tempfile.mkdtemp(); log = os.path.join(d, "v.jsonl"); p = os.path.join(d, "front_door_1.jpg")
        open(p, "wb").write(b"x"); os.utime(p, (time.time() - 600, time.time() - 600))
        opts = {"snapshot_dir": d, "cameras": "front_door", "recheck_after_seconds": 0, "ai_layer_url": "http://127.0.0.1:9", "api_token": "t", "timeout_seconds": 5}
        called = []; orig = main.ask; main.ask = lambda o, b, n="x": called.append(1) or "yes"
        try:
            main.scan(opts, set(), log)       # layer unreachable: nothing asked, nothing logged
        finally:
            main.ask = orig
        self.assertEqual(called, []); self.assertFalse(os.path.exists(log)); self.assertFalse(main.STATE["ai_layer_ok"]); self.assertIn("not reachable", main.STATE["last_error"])

    def test_ask_sends_vision_request_to_the_layer(self):
        seen = {}
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, n=-1): return json.dumps({"ok": True, "answer": "No."}).encode()
        orig = main.urllib.request.urlopen
        def fake(req, timeout=None):
            seen["url"], seen["auth"], seen["body"] = req.full_url, req.get_header("Authorization"), json.loads(req.data)
            return R()
        main.urllib.request.urlopen = fake
        try:
            ans = main.ask({"ai_layer_url": "http://x:8801/", "api_token": "tok", "timeout_seconds": 5}, b"\xff\xd8\xff", "front door 1.jpg")
        finally:
            main.urllib.request.urlopen = orig
        self.assertEqual(ans, "No.")
        self.assertEqual(seen["url"], "http://x:8801/request"); self.assertEqual(seen["auth"], "Bearer tok")
        self.assertEqual(seen["body"]["capability"], "vision"); self.assertEqual(seen["body"]["caller"], "ih_parcel_check")
        self.assertRegex(seen["body"]["request_id"], r"^[A-Za-z0-9._:-]{1,64}$")


if __name__ == "__main__":
    unittest.main()
