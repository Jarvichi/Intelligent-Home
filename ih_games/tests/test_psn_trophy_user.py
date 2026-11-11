"""psn_trophy_names reads trophies through the token's own account when it matches the Online ID (0.9.7)."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from providers import psn_trophy_names as p


class Me:
    online_id = "ExamplePlayer"


class Client:
    def __init__(self):
        self.looked_up = []

    def me(self):
        return Me()

    def user(self, *, online_id):
        self.looked_up.append(online_id)
        return "other-user"


class TrophyUserTest(unittest.TestCase):
    def test_own_account_used_without_lookup(self):
        c = Client()
        self.assertIsInstance(p._trophy_user(c, "exampleplayer"), Me)   # case-insensitive
        self.assertEqual(c.looked_up, [])

    def test_other_id_still_looked_up(self):
        c = Client()
        self.assertEqual(p._trophy_user(c, "SomeoneElse"), "other-user")
        self.assertEqual(c.looked_up, ["SomeoneElse"])

    def test_me_failure_falls_back_to_lookup(self):
        class Bad(Client):
            def me(self):
                class M:
                    @property
                    def online_id(self):
                        raise RuntimeError("x")
                return M()
        c = Bad()
        self.assertEqual(p._trophy_user(c, "ExamplePlayer"), "other-user")


if __name__ == "__main__":
    unittest.main()
