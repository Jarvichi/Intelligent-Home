"""The Add-ons list is built from the Supervisor store + catalogue.json + registrations (13 Nov 2026)."""
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
        self.assertEqual(out[0]["name"], "Intelligent Home - Jobs")     # catalogue.json beats the store for known add-ons

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
