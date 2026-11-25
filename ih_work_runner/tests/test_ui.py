"""Status page tests: files, ingress-only, threaded server, data endpoint, no secrets."""
import json, os, re, socket, sys, tempfile, threading, time, unittest, urllib.request, urllib.error

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
sys.path.insert(0, APP)
import ui_server as U

TOKEN = "WORKTOKEN" + "x" * 30
LOG = """2026-11-25 01:00:00 [DRY] Claiming paused: session at 90% | queued 2
2026-11-25 01:10:00 task #136 (build): IH UI page: Work Runner
2026-11-25 01:25:30 done abc123: task #136: built the thing, SECRET TASK TEXT here
2026-11-25 01:30:00 task #140 (plan): Another title
2026-11-25 01:31:00 FAILED abc124 (task #140): TimeoutError: very long error text with details
2026-11-25 01:40:00 [DRY] next task would be #5 (plan): hidden title
"""


def opts(**k):
    o = {"mode": "dry", "stop": False, "work_token": TOKEN, "work_url": "", "claude_oauth_token": "CLAUDETOKEN"}
    o.update(k); return o


def fake_fetch(path, token, urls, **kw):
    if path == "/workers":
        return {"workers": [{"worker": "claude-runner", "stage": "building", "title": "IH UI page: Work Runner", "ref": "task-136", "elapsed_s": 125,
                             "stalled": False, "detail": "SECRET TASK TEXT", "started_at": "x"}]}
    return {"pace": 55.5, "active": {"kind": "night", "state": "running", "phase_text": "block 1"}}


class Files(unittest.TestCase):
    def test_ui_files(self):
        ui = os.path.join(APP, "ui")
        for f in ("index.html", "ui.js", "ui.css", "ui.json", "ih-ui.css", "ih-ui.js", "ih-patterns.css"):
            self.assertTrue(os.path.isfile(os.path.join(ui, f)), f)
        html = open(os.path.join(ui, "index.html")).read()
        self.assertIn("<ih-nav", html); self.assertIn('data-home-href="/app/32c8114c_ih_orchestrator"', html)
        for f in ("index.html", "ui.js", "ui.css"):
            t = open(os.path.join(ui, f)).read()
            self.assertIsNone(re.search(r"(src|href)=\"(https?:)?//", t), f)
            self.assertNotIn("https://", t.replace("https://claude", "")) if f != "ui.js" else None
        j = json.load(open(os.path.join(ui, "ui.json")))
        for k in ("title", "purpose", "navigation", "access"): self.assertIn(k, j)

    def test_no_buttons_that_act(self):
        html = open(os.path.join(APP, "ui", "index.html")).read()
        self.assertNotIn("<button", html); self.assertNotIn("<form", html)


class Status(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        open(os.path.join(self.d, "worker.log"), "w").write(LOG)

    def test_fields_and_title_only(self):
        s = U.build_status(opts(), {"paused": "", "problems": ["x"], "last_pass": "t", "last_result": "dry pass exit 0"}, self.d, fetch=fake_fetch)
        self.assertEqual(s["mode"], "dry"); self.assertTrue(s["work_reachable"])
        self.assertEqual(s["jobs"][0]["title"], "IH UI page: Work Runner"); self.assertEqual(s["jobs"][0]["elapsed_s"], 125)
        self.assertEqual([r["task"] for r in s["runs"]], [140, 136])
        self.assertEqual(s["runs"][1]["result"], "done"); self.assertEqual(s["runs"][1]["seconds"], 930); self.assertEqual(s["runs"][1]["mode"], "build")
        self.assertEqual(s["runs"][0]["result"], "failed"); self.assertEqual(s["runs"][0]["error"], "TimeoutError")
        self.assertIn("paused", s["gate"]["text"]); self.assertEqual(len(s["failures"]), 1)
        blob = json.dumps(s)
        for bad in (TOKEN, "CLAUDETOKEN", "SECRET TASK TEXT", "hidden title", "very long error"): self.assertNotIn(bad, blob)

    def test_work_down_is_friendly(self):
        s = U.build_status(opts(), {"problems": []}, self.d, fetch=lambda *a, **k: None)
        self.assertFalse(s["work_reachable"]); self.assertEqual(s["jobs"], [])

    def test_no_token_no_request(self):
        self.assertIsNone(U.work_get("/workers", "", ["http://127.0.0.1:1"]))

    def test_scrub(self):
        self.assertNotIn("sk-ant-abcdefghij", U.scrub("key sk-ant-abcdefghij here"))

    def test_20_runs_max(self):
        open(os.path.join(self.d, "worker.log"), "w").write("".join(
            f"2026-11-25 01:{i % 60:02d}:00 task #{i} (plan): t\n2026-11-25 01:{i % 60:02d}:05 done a: task #{i}: x\n" for i in range(1, 31)))
        self.assertEqual(len(U.parse_log(U.tail_lines(os.path.join(self.d, "worker.log")))[0]), 20)


class Server(unittest.TestCase):
    def start(self, peers):
        srv = U.make_server(0, lambda: {"ok": True}, peers=peers, host="127.0.0.1")
        threading.Thread(target=srv.serve_forever, daemon=True).start(); self.addCleanup(srv.shutdown); self.addCleanup(srv.server_close)
        return srv.server_address[1]

    def get(self, port, path):
        return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3)

    def test_serves_and_status(self):
        port = self.start(("127.0.0.1",))
        self.assertIn(b"ih-nav", self.get(port, "/").read())
        self.assertEqual(json.loads(self.get(port, "/api/status").read()), {"ok": True})
        self.assertEqual(json.loads(self.get(port, "/ui.json").read())["title"], "Work Runner")

    def test_non_gateway_403(self):
        port = self.start(("172.30.32.2",))
        with self.assertRaises(urllib.error.HTTPError) as c: self.get(port, "/api/status")
        self.assertEqual(c.exception.code, 403)

    def test_idle_connection_does_not_block(self):
        port = self.start(("127.0.0.1",))
        idle = socket.create_connection(("127.0.0.1", port))
        try:
            t = time.time(); self.assertEqual(self.get(port, "/api/status").status, 200); self.assertLess(time.time() - t, 2)
        finally: idle.close()

    def test_post_refused(self):
        port = self.start(("127.0.0.1",))
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/status", data=b"{}", method="POST"), timeout=3)
        self.assertEqual(c.exception.code, 405)


if __name__ == "__main__":
    unittest.main()
