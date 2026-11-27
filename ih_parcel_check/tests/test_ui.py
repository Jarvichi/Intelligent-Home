import json, os, re, socket, sys, tempfile, threading, time, unittest, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import main

UI = Path(__file__).resolve().parents[1] / "app" / "ui"


def serve(ui, peer=""):
    main.INGRESS_PEER = peer
    srv = main.QuietServer(("127.0.0.1", 0), main.make_handler(ui))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def get(port, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=5) as r:
        return r.status, r.read()


class UiFiles(unittest.TestCase):
    def test_files_exist_and_nav_present(self):
        for n in ("index.html", "ui.js", "ui.css", "ih-ui.js", "ih-ui.css", "ih-patterns.css", "ui.json"):
            self.assertTrue((UI / n).is_file(), n)
        html = (UI / "index.html").read_text()
        self.assertIn("<ih-nav", html)
        self.assertIn('data-home-href="/app/32c8114c_ih_orchestrator"', html)
        info = json.loads((UI / "ui.json").read_text())
        for k in ("title", "purpose", "navigation", "access"):
            self.assertTrue(info.get(k), k)

    def test_no_external_urls_and_relative_api(self):
        for n in ("index.html", "ui.js", "ui.css"):
            text = (UI / n).read_text()
            self.assertEqual(re.findall(r"""(?:src|href)=["']https?://""", text), [], n)
            self.assertNotRegex(text, r"""fetch\(\s*['"]/""", n)
        self.assertIn("api/data", (UI / "ui.js").read_text())

    def test_config_has_ingress(self):
        cfg = (UI.parents[1] / "config.yaml").read_text()
        for line in ("ingress: true", "ingress_port: 8099", "panel_admin: true", "boot: manual"):
            self.assertIn(line, cfg)
        self.assertIn("8816", cfg)


class Server(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        main.open_db(os.path.join(self.d, "ih.db"))
        main.STATE.update({"dry": True, "ai_layer_ok": True})
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.shutdown(); s.server_close()
        main.DBH["db"].close(); main.DBH["db"] = None

    def start(self, ui, peer=""):
        s, p = serve(ui, peer); self.servers.append(s); return p

    def test_idle_connection_does_not_block(self):
        port = self.start(True)
        idle = socket.create_connection(("127.0.0.1", port))   # opens, sends nothing (like the ingress gateway)
        try:
            t0 = time.time()
            self.assertEqual(get(port, "/")[0], 200)
            self.assertLess(time.time() - t0, 3)
        finally:
            idle.close()

    def test_old_api_unchanged(self):
        port = self.start(False)
        self.assertEqual(get(port, "/healthz"), (200, b"ok"))
        st = json.loads(get(port, "/status")[1])
        self.assertIn("seen", st); self.assertTrue(st["dry"])

    def test_non_gateway_refused(self):
        port = self.start(True, peer="172.30.32.2")
        for path in ("/", "/api/data"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                get(port, path)
            self.assertEqual(cm.exception.code, 403)

    def test_data_counts_last20_errors_and_no_secrets(self):
        now = int(time.time())
        cams = ["front_door", "side_door"]
        for i in range(25):
            main.db_add_verdict({"time": now - i * 60, "check": "parcel", "file": "front_door_2026_%d.jpg" % i,
                                 "verdict": ("yes", "no", "unsure")[i % 3], "raw": "answer text", "seconds": 1.5}, cams, "gemini")
        main.db_add_verdict({"time": now - 3 * 86400, "check": "parcel", "file": "side_door_1.jpg", "verdict": "yes", "raw": "Yes", "seconds": 1}, cams)
        main.db_add_verdict({"time": now - 10 * 86400, "check": "parcel", "file": "side_door_0.jpg", "verdict": "no", "raw": "No", "seconds": 1}, cams)
        main.set_error("AI layer not reachable: boom"); main.set_error("AI layer not reachable: boom")  # deduped
        port = self.start(True)
        j = json.loads(get(port, "/api/data")[1])
        self.assertEqual(j["mode"], "DRY"); self.assertTrue(j["ai_layer_ok"])
        self.assertEqual(len(j["verdicts"]), 20)
        self.assertEqual(j["verdicts"][0]["camera"], "front_door"); self.assertEqual(j["verdicts"][0]["ai"], "gemini")
        self.assertEqual(j["verdicts"][0]["label"], "Parcel")
        self.assertEqual(j["week"]["checked"], 26)   # 25 recent + the 3-day-old one; the 10-day-old one is out
        self.assertEqual(j["week"]["parcel"], 9 + 1)  # i = 0,3,...,24 -> 9 yes, plus the old yes
        self.assertEqual(j["week"]["errors"], 1)
        self.assertEqual(len(j["errors"]), 1)
        self.assertLessEqual(j["today"]["checked"], 26)
        self.assertEqual(j["last_check"], now)
        blob = json.dumps(j)
        self.assertNotIn("api_token", blob); self.assertNotIn(".jpg", blob); self.assertNotIn("Bearer", blob)

    def test_import_old_log_once_and_errors_capped(self):
        log = os.path.join(self.d, "verdicts.jsonl")
        with open(log, "w") as f:
            f.write(json.dumps({"file": "front_door_1.jpg", "time": int(time.time()), "verdict": "no", "raw": "No", "seconds": 1}) + "\n")
            f.write("not json\n")
        self.assertEqual(main.import_verdicts(log, ["front_door"]), 1)
        self.assertEqual(main.import_verdicts(log, ["front_door"]), 0)   # table not empty any more
        rows = main.DBH["db"].query("SELECT check_name, camera FROM verdicts")
        self.assertEqual(rows, [("parcel", "front_door")])
        for i in range(60):
            main.db_add_error("error %d" % i)
        self.assertEqual(main.DBH["db"].query("SELECT COUNT(*) FROM errors")[0][0], main.KEEP_ERRORS)

    def test_camera_of(self):
        self.assertEqual(main.camera_of("front_door_2026.jpg", ["front_door"]), "front_door")
        self.assertEqual(main.camera_of("back_garden_2026.jpg"), "back_garden")


if __name__ == "__main__":
    unittest.main()
