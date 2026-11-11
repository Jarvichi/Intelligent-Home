"""psn_trophy_names keys trophies by (np communication id, trophy id) (0.13.1). No network."""
import json, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from providers import psn_trophy_names as p

PLAYER = {"key": "player_a", "trophy_prefix": "x", "psn_online_id": "ExamplePlayer"}
CHECKED = "2026-11-01T10:00:00+00:00"
EARNED = "2026-11-02T10:00:00+00:00"


class Ctx:
    def __init__(self, d):
        self.data_dir, self.opts, self.logs = d, {"psn_npsso": "t"}, []

    def log(self, m):
        self.logs.append(m)


class DedupTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orig = p._recent_trophies
        self.records = []
        p._recent_trophies = lambda *a: list(self.records)
        self.n = 0

    def tearDown(self):
        p._recent_trophies = self.orig
        shutil.rmtree(self.d)

    def path(self, name):
        return os.path.join(self.d, name)

    def write(self, name, data):
        with open(self.path(name), "w") as f:
            json.dump(data, f)

    def setup_state(self, state=None):
        self.n += 1
        self.write("state_psn_trophy_names_player_a.json", state or {"counts": {"bronze": 0}, "earned_ids": [], "checked_at": CHECKED})

    def run_once(self, game):
        self.n += 1
        self.write("state_psn_trophies_player_a.json", {"counts": {"bronze": self.n}, "now_playing": game})
        p.run(Ctx(self.d), PLAYER)

    def rec(self, np_id, tid, name):
        return {"name": name, "tier": "bronze", "earned_at": EARNED, "id": tid, "np_id": np_id}

    def history(self):
        try:
            return [json.loads(l) for l in open(self.path("trophy_history_player_a.jsonl")) if l.strip()]
        except OSError:
            return []

    def events(self):
        return json.load(open(self.path("feeds/psn_trophy_names_player_a.json")))["events"]

    def test_two_games_same_trophy_id_both_recorded(self):
        self.setup_state()
        self.records = [self.rec("NPWR1", "5", "A five")]
        self.run_once("Game A")
        st = json.load(open(self.path("state_psn_trophy_names_player_a.json")))
        st["checked_at"] = CHECKED   # real clock is later than the fake earn time
        self.write("state_psn_trophy_names_player_a.json", st)
        self.records = [self.rec("NPWR2", "5", "B five")]
        self.run_once("Game B")
        self.assertEqual([h["name"] for h in self.history()], ["A five", "B five"])
        self.assertEqual([e["achievement"] for e in self.events()], ["A five", "B five"])
        st = json.load(open(self.path("state_psn_trophy_names_player_a.json")))
        self.assertEqual(sorted(st["earned_ids"]), ["NPWR1:5", "NPWR2:5"])

    def test_migration_keeps_legacy_and_does_not_hide_other_games(self):
        self.setup_state({"counts": {"bronze": 0}, "earned_ids": ["5", "7"], "checked_at": CHECKED})
        with open(self.path("trophy_history_player_a.jsonl"), "w") as f:
            f.write(json.dumps({"id": "5", "game": "Old", "name": "Old five"}) + "\n")
        self.records = [self.rec("NPWR2", "5", "B five")]
        self.run_once("Game B")
        st = json.load(open(self.path("state_psn_trophy_names_player_a.json")))
        self.assertEqual(st["legacy_ids"], ["5", "7"])
        self.assertEqual(st["earned_ids"], ["NPWR2:5"])
        names = [h["name"] for h in self.history()]
        self.assertEqual(names, ["Old five", "B five"])   # old line kept, new one added

    def test_legacy_not_reannounced_when_earned_before_check(self):
        self.setup_state({"counts": {"bronze": 0}, "earned_ids": ["5"], "checked_at": CHECKED})
        old = self.rec("NPWR1", "5", "Old five")
        old["earned_at"] = "2026-10-01T10:00:00+00:00"
        self.records = [old]
        self.run_once("Game A")
        self.assertEqual(self.events(), [])
        self.assertEqual(self.history(), [])

    def test_no_reannounce_after_restart(self):
        self.setup_state()
        self.records = [self.rec("NPWR1", "5", "A five")]
        self.run_once("Game A")
        # restart: state is read from disk again; PSN still returns the same trophy, checked_at is reset to
        # before the earn time to force the key check to do the work
        st = json.load(open(self.path("state_psn_trophy_names_player_a.json")))
        st["checked_at"] = CHECKED
        self.write("state_psn_trophy_names_player_a.json", st)
        self.run_once("Game A")
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(len(self.events()), 1)


if __name__ == "__main__":
    unittest.main()
