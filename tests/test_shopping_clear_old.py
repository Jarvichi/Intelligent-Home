"""ih_discord_shopping: typing 'shop' clears the bot's old checklist and 'Which list?' messages first (tagged ones only).
Fakes only; no network. Run: python3 tests/test_shopping_clear_old.py"""
import importlib.util
import os
import re
import sys
import unittest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ih_discord_shopping', 'app')
sys.path.insert(0, APP)
spec = importlib.util.spec_from_file_location('ihs_main_clear', os.path.join(APP, 'main.py'))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class FakeMod:
    def __init__(self, fail_delete=False):
        self.ops, self.lists, self.saved, self.fail = [], {'old1': {}, 'old2': {}}, 0, fail_delete

    def bus_delete_tagged(self, ck, tag):
        self.ops.append(('delete_tagged', tag))
        if self.fail:
            raise RuntimeError('discord down')

    def bus_post(self, ck, text, tag=None, reply_to=None):
        self.ops.append(('post', tag))
        return {'ok': True, 'discord_id': 'q1'}

    def bus_react(self, *a): pass
    def bus_watch(self, *a): pass
    def save_state(self): self.saved += 1


class Ctx:
    discord = type('D', (), {'me': staticmethod(lambda: {'id': 'BOT'}), 'req': staticmethod(lambda *a, **k: (200, [])), 'post': staticmethod(lambda *a, **k: (200, {'id': 'x'}))})()
    def allowed(self): return {'U1'}
    def now(self): return 1000.0


def feature(mod):
    f = object.__new__(M.BusShopping)
    f.c, f.mod, f.schan, f.pend, f.routed = Ctx(), mod, 'shopping', {'id': 'oldq', 'user': 'U1', 't': 1}, True
    f.cfg = {'receipts_dir': ''}
    f.shop_re = re.compile('shop')
    return f


class ClearOld(unittest.TestCase):
    def say(self, f, text='shop', uid='U1'):
        f.handle_message({'id': '9', 'author': {'id': uid}, 'content': text})

    def test_shop_deletes_checklist_and_scope_before_new_question(self):
        mod = FakeMod(); f = feature(mod); self.say(f)
        self.assertEqual(mod.ops[:3], [('delete_tagged', 'checklist'), ('delete_tagged', 'scope'), ('post', 'scope')])
        self.assertEqual(mod.lists, {})
        self.assertEqual(f.pend['id'], 'q1')   # only the new question is pending
        self.assertGreaterEqual(mod.saved, 1)

    def test_only_tags_checklist_and_scope_are_deleted(self):
        mod = FakeMod(); f = feature(mod); self.say(f)
        self.assertEqual({t for op, t in mod.ops if op == 'delete_tagged'}, {'checklist', 'scope'})

    def test_other_users_and_other_words_do_nothing(self):
        mod = FakeMod(); f = feature(mod)
        self.say(f, uid='U9'); self.say(f, text='hello')
        self.assertEqual(mod.ops, [])
        self.assertEqual(f.pend['id'], 'oldq')

    def test_failed_delete_does_not_stop_the_question(self):
        mod = FakeMod(fail_delete=True); f = feature(mod); self.say(f)
        self.assertIn(('post', 'scope'), mod.ops)


if __name__ == '__main__':
    unittest.main()
