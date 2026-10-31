"""TEMPLATE for a game tool. Copy to app/tools/<game>.py (or run: games_cli.py newtool "<game>"), fill in the TODOs, add a test.
Files starting with an underscore are never loaded. Everything else in this folder is found automatically.

Rules: generic code only (no personal names, ids or keys in source); read-only; use cached data and return fast;
keys come from add-on options (tc.opts), never from files in the source; commands return SHORT plain text (one or two lines).
"""
import library as L

# TODO: short lowercase id, also what 'games_cli.py cmd <person> <NAME> <command>' uses.
NAME = "mygame"
# TODO: normalised titles this tool handles (lowercase, letters and digits, single spaces).
TITLES = ("my game",)


def matches(entry):
    """True if a library entry is this game. The library then lists NAME in the entry's 'tools'."""
    return L.norm(entry.get("title")) in TITLES


def summary(tc, player):
    """What the player has done lately, one or two short sentences.
    tc.data_dir (add-on /data), tc.opts (options), tc.now() (aware datetime), tc.open(req) (HTTP, fake in tests), tc.log(msg)."""
    return "TODO: summary for %s" % player


def upcoming(tc, player):
    """List of {'when': ISO time or text, 'text': 'short line'}. Empty list if nothing is known."""
    return []


def cmd_help(tc, player, args):
    """Commands take (tc, player, args) where args is a list of words, and return a short string."""
    return "Commands: " + ", ".join(sorted(commands))


# name -> function. The assistant calls: games_cli.py cmd <person> <NAME> <name> [args...]
commands = {"help": cmd_help}

# ---- TEST TEMPLATE (copy into tests/test_tool_<name>.py) ----
# import os, sys, tempfile, unittest
# sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'addon_games', 'app'))
# from tools import ToolCtx, mygame
# class T(unittest.TestCase):
#     def test_matches(self):
#         self.assertTrue(mygame.matches({'title': 'My Game'}))
#         self.assertFalse(mygame.matches({'title': 'Other'}))
#     def test_summary_and_commands(self):
#         tc = ToolCtx(tempfile.mkdtemp(), {'players': [{'key': 'player_a'}]})   # pass opener=fake for HTTP
#         self.assertTrue(mygame.summary(tc, 'player_a'))
#         self.assertIn('help', mygame.commands)
# if __name__ == '__main__': unittest.main()
