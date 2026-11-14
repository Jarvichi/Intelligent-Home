"""Setup wizard: allow-list, no values echoed, saves only named options, restarts a running add-on."""
import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import manager, provisioner, wizard

DEF = {"version": 1, "title": "Games setup", "enable_option": "providers_enabled", "groups": [
    {"id": "steam", "label": "Steam", "help": "Needs a Steam web API key", "enable_values": ["steam_library", "steam_achievements"],
     "fields": [{"option": "steam_api_key", "label": "Steam API key", "kind": "secret"}]},
    {"id": "psn", "label": "PlayStation", "enable_values": ["psn_trophies"],
     "fields": [{"option": "psn_npsso", "label": "PSN npsso token", "kind": "secret"}]}]}
SCHEMA = {"steam_api_key": "password?", "psn_npsso": "password?", "providers_enabled": ["str"], "mode": "str"}


def info(opts, state="started"):
    return {"version": "1", "state": state, "repository": "repo", "options": opts, "schema": SCHEMA}


class WizardUnit(unittest.TestCase):
    def test_validate_rejects_bad(self):
        for bad in (None, {"version": 2}, dict(DEF, groups=[]),
                    dict(DEF, enable_option=None),  # groups carry enable_values but no enable option
                    dict(DEF, title="<b>x</b>")):
            with self.assertRaises(ValueError):
                wizard.validate(bad)
        dup = json.loads(json.dumps(DEF)); dup["groups"][1]["fields"][0]["option"] = "steam_api_key"
        with self.assertRaises(ValueError):
            wizard.validate(dup)

    def test_long_help_text_is_accepted(self):
        d = json.loads(json.dumps(DEF)); d["groups"][0]["help"] = "Needs a key; get it from the site. " * 4
        self.assertTrue(wizard.validate(d)["groups"][0]["help"])
        d["groups"][0]["help"] = "x" * 181
        with self.assertRaises(ValueError):
            wizard.validate(d)

    def test_state_never_has_values(self):
        d = wizard.validate(DEF)
        st = wizard.state(d, info({"steam_api_key": "SECRETVALUE", "psn_npsso": "", "providers_enabled": ["steam_library"]}))
        self.assertNotIn("SECRETVALUE", json.dumps(st))
        self.assertTrue(st["groups"][0]["fields"][0]["set"]); self.assertFalse(st["groups"][1]["fields"][0]["set"])
        self.assertTrue(st["groups"][0]["enabled"]); self.assertFalse(st["groups"][1]["enabled"])
        self.assertFalse(st["first_run"])
        self.assertTrue(wizard.state(d, info({"steam_api_key": "", "providers_enabled": []}))["first_run"])

    def test_apply_only_named_options(self):
        d = wizard.validate(DEF)
        cur = {"steam_api_key": "", "psn_npsso": "keep", "mode": "dry", "providers_enabled": ["xbox_gamerscore"]}
        opts, changed = wizard.apply(d, info(cur), {"groups": {"steam": {"enabled": True, "values": {"steam_api_key": " K1 "}},
                                                                 "psn": {"enabled": False, "values": {"psn_npsso": ""}}}})
        self.assertEqual(opts["steam_api_key"], "K1"); self.assertEqual(opts["psn_npsso"], "keep"); self.assertEqual(opts["mode"], "dry")
        self.assertEqual(opts["providers_enabled"], ["steam_achievements", "steam_library", "xbox_gamerscore"])
        self.assertEqual(changed, ["providers_enabled", "steam_api_key"])
        for bad in ({"groups": {"steam": {"values": {"mode": "live"}}}}, {"groups": {"nope": {}}},
                    {"groups": {"steam": {"values": {"steam_api_key": 5}}}}, {"x": 1}):
            with self.assertRaises(ValueError):
                wizard.apply(d, info(cur), bad)

    def test_non_text_option_refused(self):
        d = wizard.validate(DEF)
        i = info({}); i["schema"] = dict(SCHEMA, steam_api_key="int")
        with self.assertRaises(ValueError):
            wizard.apply(d, i, {"groups": {"steam": {"values": {"steam_api_key": "x"}}}})


class WizardManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        (Path(self.tmp.name) / "ih_games.json").write_text(json.dumps(DEF))
        old = wizard.WIZ_DIR; wizard.WIZ_DIR = Path(self.tmp.name)
        self.addCleanup(setattr, wizard, "WIZ_DIR", old)
        self.calls, self.opts, self.state = [], {"steam_api_key": "", "psn_npsso": "", "providers_enabled": []}, "started"

        def sup(method, path, body=None):
            self.calls.append((method, path, body))
            if path == "/addons/self/info":
                return {"slug": "abc_ih_orchestrator", "repository": "repo"}
            if path == "/addons/abc_ih_games/info":
                return info(self.opts, self.state)
            return {}
        self.m = manager.Manager([{"slug": "ih_games", "name": "Games", "requires": []}], sup=sup, usage_path="/nonexistent/u.json", log=lambda *_: None)

    def test_state_and_list(self):
        code, st = self.m.wizard_state("ih_games")
        self.assertEqual(code, 200); self.assertTrue(st["first_run"]); self.assertTrue(st["restarts"])
        self.assertEqual(self.m.wizards()[1]["wizards"], [{"slug": "ih_games", "installed": True, "first_run": True}])
        self.assertEqual(self.m.wizard_state("ih_nothing")[0], 404)

    def test_save_writes_and_restarts(self):
        code, out = self.m.wizard_save("ih_games", {"groups": {"psn": {"enabled": True, "values": {"psn_npsso": "TOKEN"}}}})
        self.assertEqual(code, 200); self.assertTrue(out["restarted"]); self.assertNotIn("TOKEN", json.dumps(out))
        posts = [c for c in self.calls if c[0] == "POST"]
        self.assertEqual(posts[0][1], "/addons/abc_ih_games/options"); self.assertEqual(posts[0][2]["options"]["psn_npsso"], "TOKEN")
        self.assertEqual(posts[1][1], "/addons/abc_ih_games/restart")

    def test_save_stopped_addon_no_restart_and_noop(self):
        self.state = "stopped"
        code, out = self.m.wizard_save("ih_games", {"groups": {"psn": {"values": {"psn_npsso": "T"}}}})
        self.assertFalse(out["restarted"])
        self.calls.clear()
        self.assertEqual(self.m.wizard_save("ih_games", {"groups": {}})[1]["changed"], [])
        self.assertFalse([c for c in self.calls if c[0] == "POST"])

    def test_bad_answers_400_and_no_write(self):
        code, out = self.m.wizard_save("ih_games", {"groups": {"steam": {"values": {"mode": "x"}}}})
        self.assertEqual(code, 400); self.assertFalse([c for c in self.calls if c[0] == "POST"])


if __name__ == "__main__":
    unittest.main()
