"""The Add-ons list is built from the Supervisor store + each add-on's ih.json + registrations (13 Nov 2026)."""
import json, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import manager, provisioner

STATIC = [{"slug": "ih_jobs", "name": "Intelligent Home - Jobs", "description": "Jobs", "kind": "addon", "requires": [], "version": "1.0.0", "image": True,
           "ui": {"title": "Jobs", "purpose": "Runs jobs"}}]
STORE = [{"slug": "abc_ih_jobs", "name": "Jobs store name", "repository": "repo", "version": "9"},
         {"slug": "abc_ih_newthing", "name": "Intelligent Home - New Thing", "description": "Does new things", "repository": "repo", "version": "0.1.0"},
         {"slug": "abc_ih_other_repo", "name": "Elsewhere", "repository": "other", "version": "1"},
         {"slug": "xyz_ih_foreign", "name": "Foreign prefix", "repository": "repo", "version": "1"},
         {"slug": "abc_not_ours", "name": "Not ih", "repository": "repo", "version": "1"}]


class Merge(unittest.TestCase):
    def test_store_adds_unknown_ours_only(self):
        out = manager.merge_catalogue(STATIC, STORE, {}, "abc", "repo")
        self.assertEqual([c["slug"] for c in out], ["ih_jobs", "ih_newthing"])
        new = out[1]
        self.assertEqual((new["name"], new["version"], new["requires"], new["ui"]["title"]), ("Intelligent Home - New Thing", "0.1.0", [], "New Thing"))
        self.assertEqual(out[0]["name"], "Intelligent Home - Jobs")     # a known (static) entry keeps its name over the store's

    def test_registration_sets_requires_and_ui_for_known_only(self):
        reg = {"ih_newthing": {"requires": ["ih_jobs", 5], "ui": {"title": "Thing", "purpose": "x" * 300}}, "ih_ghost": {"requires": ["ih_jobs"]}}
        out = manager.merge_catalogue(STATIC, STORE, reg, "abc", "repo")
        new = [c for c in out if c["slug"] == "ih_newthing"][0]
        self.assertEqual(new["requires"], ["ih_jobs"])
        self.assertEqual(new["ui"]["title"], "Thing")
        self.assertEqual(len(new["ui"]["purpose"]), 160)
        self.assertNotIn("ih_ghost", [c["slug"] for c in out])             # a registration alone never creates an add-on

    def test_manager_falls_back_and_scopes(self):
        def broken(method, path, body=None):
            if path == "/addons/self/info":
                return {"slug": "abc_ih_orchestrator", "repository": "repo"}
            raise RuntimeError("store down")
        m = manager.Manager(STATIC, sup=broken, usage_path="/nonexistent/usage.json")
        self.assertEqual([c["slug"] for c in m.catalogue], ["ih_jobs"])

        def ok(method, path, body=None):
            if path == "/addons/self/info":
                return {"slug": "abc_ih_orchestrator", "repository": "repo"}
            if path == "/store/addons":
                return {"addons": STORE}
            if path.startswith("/addons/abc_ih_") and path.endswith("/info"):
                raise provisioner.SupError("not installed")   # like the real Supervisor for an add-on that is not installed
            if path == "/store/addons/abc_ih_newthing":
                return {"repository": "repo"}
            if path == "/store/addons/abc_ih_other_repo":
                return {"repository": "other"}
            return {"addons": []}
        m2 = manager.Manager(STATIC, sup=ok, usage_path="/nonexistent/usage.json")
        self.assertEqual(m2.scope("ih_newthing")[0], "abc_ih_newthing")
        with self.assertRaises(ValueError):
            m2.scope("ih_other_repo")


    def test_ih_json_layer_beats_store_and_registration_beats_ih_json(self):
        meta = {"ih_newthing": {"requires": ["ih_jobs"], "ui": {"title": "From file", "purpose": "file purpose"}}}
        out = manager.merge_catalogue([], STORE, {}, "abc", "repo", meta)
        new = [c for c in out if c["slug"] == "ih_newthing"][0]
        self.assertEqual((new["requires"], new["ui"]["title"]), (["ih_jobs"], "From file"))
        out = manager.merge_catalogue([], STORE, {"ih_newthing": {"ui": {"title": "Live"}}}, "abc", "repo", meta)
        new = [c for c in out if c["slug"] == "ih_newthing"][0]
        self.assertEqual((new["requires"], new["ui"]["title"], new["ui"]["purpose"]), (["ih_jobs"], "Live", "file purpose"))

    def test_raw_base_only_for_github_and_clean_meta_is_bounded(self):
        self.assertEqual(manager.raw_base("https://github.com/Jarvichi/Intelligent-Home"), "https://raw.githubusercontent.com/Jarvichi/Intelligent-Home/main/")
        self.assertEqual(manager.raw_base("https://github.com/o/r.git"), "https://raw.githubusercontent.com/o/r/main/")
        for bad in ("http://github.com/o/r", "https://evil.example/o/r", "https://github.com/o", None, ""):
            self.assertIsNone(manager.raw_base(bad))
        self.assertIsNone(manager.clean_meta("x"))
        self.assertIsNone(manager.clean_meta({"other": 1}))
        m = manager.clean_meta({"requires": ["a"] * 30 + [1], "ui": {"title": "t" * 99, "purpose": "p" * 999}, "secret": "x"})
        self.assertEqual((len(m["requires"]), len(m["ui"]["title"]), len(m["ui"]["purpose"]), "secret" in m), (12, 40, 160, False))

    def test_manager_reads_ih_json_caches_and_survives_github_down(self):
        import os, tempfile
        calls = []
        def sup(method, path, body=None):
            if path == "/addons/self/info":
                return {"slug": "abc_ih_orchestrator", "repository": "repo"}
            if path == "/store/addons":
                return {"addons": STORE}
            if path == "/store/repositories":
                return [{"slug": "repo", "source": "https://github.com/Jarvichi/Intelligent-Home"}]
            return {"addons": []}
        state = {"down": False}
        def http(url, timeout=4):
            calls.append(url)
            if state["down"]:
                return None
            return {"requires": ["ih_jobs"], "ui": {"title": "T", "purpose": "P"}}
        os.environ["IH_META_CACHE"] = os.path.join(tempfile.mkdtemp(), "meta.json")
        m = manager.Manager([], sup=sup, http=http, usage_path="/nonexistent/usage.json")
        cat = {c["slug"]: c for c in m.catalogue}
        self.assertEqual(cat["ih_newthing"]["requires"], ["ih_jobs"])
        self.assertTrue(all(u.startswith("https://raw.githubusercontent.com/Jarvichi/Intelligent-Home/main/ih_") and u.endswith("/ih.json") for u in calls))
        n = len(calls)
        m._cat = (0, None); m.catalogue                                   # rebuilt within the hour: no new fetches
        self.assertEqual(len(calls), n)
        state["down"] = True
        m._meta = {k: (0, v[1]) for k, v in m._meta.items()}              # make every copy stale; GitHub is down: last good copy stays
        m._cat = (0, None)
        self.assertEqual({c["slug"]: c for c in m.catalogue}["ih_newthing"]["requires"], ["ih_jobs"])


class Register(unittest.TestCase):
    def setUp(self):
        import main
        self.main = main
        main.STATE["registered"] = {}

    def test_accepts_and_stores(self):
        code, _ = self.main.register(json.dumps({"slug": "ih_x", "requires": ["ih_jobs"], "ui": {"title": "X", "purpose": "does x"}}).encode())
        self.assertEqual(code, 200)
        self.assertEqual(self.main.STATE["registered"]["ih_x"]["requires"], ["ih_jobs"])

    def test_rejects_bad_input(self):
        for body in (b"nope", json.dumps({"slug": "evil/../x"}).encode(), json.dumps({"slug": "ih_orchestrator"}).encode(),
                     json.dumps({"slug": "ih_x", "requires": "ih_jobs"}).encode(), json.dumps({"slug": "ih_x", "ui": "t"}).encode()):
            self.assertEqual(self.main.register(body)[0], 400)
        self.assertEqual(self.main.STATE["registered"], {})


if __name__ == "__main__":
    unittest.main()
