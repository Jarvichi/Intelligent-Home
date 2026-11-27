import datetime, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from providers import psn_sessions as P
import orchestrator
from jobs import common

PLAYER = {"key": "p1", "trophy_prefix": "pre", "speaker": "clara"}
T0 = datetime.datetime(2026, 11, 20, 14, 0, tzinfo=datetime.timezone.utc)


class FakeCtx(common.Ctx):
    def __init__(self, d, state, dry=True, opts=None):
        super().__init__(dry=dry, log=lambda m: self.logs.append(m), data_dir=d, opts=opts or {})
        self.logs, self.state = [], state

    def ha(self, path, data=None):
        return {"state": self.state}


def play(d, minutes, game="Elden Ring"):
    """Play `game` for `minutes`, then show no title for 11 minutes (the session closes)."""
    ctx = FakeCtx(d, game)
    for i in range(0, minutes + 1, 5):
        P.run(ctx, PLAYER, now=T0 + datetime.timedelta(minutes=i))
    ctx.state = "unknown"
    for i in (minutes + 5, minutes + 16):
        P.run(ctx, PLAYER, now=T0 + datetime.timedelta(minutes=i))
    return ctx


def feed(d):
    try:
        return json.load(open(os.path.join(d, "feeds", "psn_sessions_p1.json")))
    except OSError:
        return None


class SessionEvents(unittest.TestCase):
    def test_25_min_session_writes_one_event(self):
        d = tempfile.mkdtemp()
        play(d, 25)
        f = feed(d)
        self.assertEqual(len(f["events"]), 1)
        e = f["events"][0]
        self.assertEqual((e["kind"], e["game"], e["minutes"], e["big"]), ("session", "Elden Ring", 25, False))
        self.assertEqual(e["text"], "Elden Ring for 25 min")
        self.assertNotIn("current", f)

    def test_short_sessions_write_none(self):
        for m in (8, 19):
            d = tempfile.mkdtemp()
            play(d, m)
            self.assertIsNone(feed(d), m)
            self.assertTrue(os.path.exists(os.path.join(d, "psn_playtime_p1.json")))   # totals still counted

    def test_exactly_20_counts(self):
        d = tempfile.mkdtemp()
        play(d, 20)
        self.assertEqual(len(feed(d)["events"]), 1)

    def test_restart_does_not_repeat(self):
        d = tempfile.mkdtemp()
        play(d, 25)
        ctx = FakeCtx(d, "unknown")   # restart: same data dir, still no title
        for i in (30, 40):
            P.run(ctx, PLAYER, now=T0 + datetime.timedelta(minutes=i + 100))
        self.assertEqual(len(feed(d)["events"]), 1)

    def test_keeps_last_20(self):
        d = tempfile.mkdtemp()
        for n in range(23):
            ctx = FakeCtx(d, "Game %d" % n)
            base = T0 + datetime.timedelta(hours=3 * n)
            for i in (0, 20, 25):
                P.run(ctx, PLAYER, now=base + datetime.timedelta(minutes=i))
            ctx.state = "unknown"
            for i in (30, 41):
                P.run(ctx, PLAYER, now=base + datetime.timedelta(minutes=i))
        ev = feed(d)["events"]
        self.assertEqual(len(ev), 20)
        self.assertEqual(ev[-1]["game"], "Game 22")

    def test_orchestrator_dry_would_send(self):
        d = tempfile.mkdtemp()
        play(d, 25)
        opts = {"players": [PLAYER], "assistant_url": "http://assistant", "timezone": "UTC"}
        ctx = FakeCtx(d, "unknown", dry=True, opts=opts)
        end = T0 + datetime.timedelta(minutes=25)
        orchestrator.run(ctx, now=end + datetime.timedelta(minutes=30))
        self.assertTrue(any("would fire ih_games_message" in m and "Elden Ring for 25 min" in m for m in ctx.logs), ctx.logs)
        self.assertTrue(any("would send event to persona clara" in m for m in ctx.logs), ctx.logs)
        n = len(ctx.logs)
        orchestrator.run(ctx, now=end + datetime.timedelta(minutes=40))   # handled: nothing new
        self.assertEqual(len(ctx.logs), n)


if __name__ == "__main__":
    unittest.main()
