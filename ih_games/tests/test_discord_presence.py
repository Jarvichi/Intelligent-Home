import datetime, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import discord_presence as D
import dgateway_base as G
from providers import discord_presence as P
from jobs import common

PLAYER = {"key": "rob", "discord_user_id": "111"}
T0 = datetime.datetime(2026, 11, 27, 20, 0, tzinfo=datetime.timezone.utc)


class ExtractTests(unittest.TestCase):
    def test_first_playing_activity_wins_and_others_are_ignored(self):
        acts = [{"type": 4, "name": "Custom Status"}, {"type": 2, "name": "Spotify"}, {"type": 0, "name": " Elden Ring "}, {"type": 0, "name": "Other"}]
        self.assertEqual(D.extract_game(acts), "Elden Ring")
        self.assertIsNone(D.extract_game([{"type": 2, "name": "Spotify"}]))
        self.assertIsNone(D.extract_game(None))
        self.assertIsNone(D.extract_game([{"type": 0, "name": "  "}, "junk", {"type": 0}]))
        self.assertEqual(len(D.extract_game([{"type": 0, "name": "x" * 500}])), D.MAX_NAME)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.s = D.PresenceSession("tok", ["111"])

    def test_identify_asks_for_guilds_and_presences_only(self):
        acts = self.s.on_payload({"op": G.OP_HELLO, "d": {"heartbeat_interval": 41250}}, 0.0)
        ident = [a[1] for a in acts if a[0] == G.A_SEND][0]
        self.assertEqual(ident["op"], G.OP_IDENTIFY)
        self.assertEqual(ident["d"]["intents"], (1 << 0) | (1 << 8))

    def test_only_the_watched_user_produces_an_action(self):
        mine = {"op": 0, "s": 1, "t": "PRESENCE_UPDATE", "d": {"user": {"id": "111"}, "guild_id": "9", "activities": [{"type": 0, "name": "Doom"}]}}
        other = {"op": 0, "s": 2, "t": "PRESENCE_UPDATE", "d": {"user": {"id": "222"}, "guild_id": "9", "activities": [{"type": 0, "name": "Doom"}]}}
        self.assertEqual(self.s.on_payload(mine, 0), [(D.A_PRESENCE, "111", "Doom")])
        self.assertEqual(self.s.on_payload(other, 0), [])

    def test_guild_create_lists_presences_and_stopping_a_game_gives_none(self):
        gc = {"op": 0, "s": 3, "t": "GUILD_CREATE", "d": {"id": "9", "presences": [
            {"user": {"id": "222"}, "activities": [{"type": 0, "name": "X"}]}, {"user": {"id": "111"}, "activities": [{"type": 0, "name": "Doom"}]}]}}
        self.assertEqual(self.s.on_payload(gc, 0), [(D.A_PRESENCE, "111", "Doom")])
        off = {"op": 0, "s": 4, "t": "PRESENCE_UPDATE", "d": {"user": {"id": "111"}, "activities": []}}
        self.assertEqual(self.s.on_payload(off, 0), [(D.A_PRESENCE, "111", None)])

    def test_no_intent_close_is_fatal(self):
        self.assertFalse(self.s.on_close(D.CLOSE_NO_INTENT))


class TrackerAndProviderTests(unittest.TestCase):
    def test_tracker_writes_only_on_change_and_ignores_strangers(self):
        with tempfile.TemporaryDirectory() as d:
            tr = D.Tracker(d, [PLAYER, {"key": "x"}], now=lambda: T0)
            self.assertFalse(tr.update("222", "Doom"))
            self.assertTrue(tr.update("111", "Doom"))
            self.assertFalse(tr.update("111", "Doom"))
            f = json.load(open(os.path.join(d, "presence_rob.json")))
            self.assertEqual((f["game"], f["since"]), ("Doom", T0.isoformat(timespec="seconds")))
            self.assertEqual(os.listdir(d), ["presence_rob.json"])

    def run_provider(self, d, game, minute):
        json.dump({"game": game}, open(os.path.join(d, "presence_rob.json"), "w"))
        ctx = common.Ctx(dry=True, log=lambda m: None, data_dir=d, opts={})
        P.run(ctx, PLAYER, now=T0 + datetime.timedelta(minutes=minute))
        return json.load(open(os.path.join(d, "feeds", "discord_presence_rob.json")))

    def test_long_session_becomes_an_event_short_one_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            f = self.run_provider(d, "Doom", 0)
            self.assertEqual(f["current"]["now_playing"], "Doom")
            f = self.run_provider(d, None, 45)
            self.assertIsNone(f["current"]["now_playing"])
            self.assertEqual([(e["game"], e["count"], e["kind"]) for e in f["events"]], [("Doom", 45, "session")])
            self.run_provider(d, "Quick", 50)
            f = self.run_provider(d, None, 60)
            self.assertEqual(len(f["events"]), 1)

    def test_no_user_id_or_no_listener_file_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = common.Ctx(dry=True, log=lambda m: None, data_dir=d, opts={})
            P.run(ctx, {"key": "rob"}, now=T0)
            P.run(ctx, PLAYER, now=T0)
            self.assertFalse(os.path.exists(os.path.join(d, "feeds")))

    def test_start_needs_a_token_and_a_watched_player(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(D.start(d, [PLAYER], ""))
            self.assertIsNone(D.start(d, [{"key": "rob"}], "tok"))


if __name__ == "__main__":
    unittest.main()
