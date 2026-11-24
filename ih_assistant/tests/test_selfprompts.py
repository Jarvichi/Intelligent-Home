import datetime, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import selfprompts as sp
from store import PersonaStore
from main import Assistant, load_options

T0 = 1793955600.0   # fixed clock


class Pure(unittest.TestCase):
    def test_week_cap_and_chance(self):
        self.assertEqual(sp.week_cap(0), 0); self.assertEqual(sp.week_cap(3), 1)
        self.assertEqual(sp.week_cap(5), 3); self.assertEqual(sp.week_cap(10), 7)
        self.assertEqual(sp.week_cap('x'), 0); self.assertEqual(sp.daily_chance(0), 0)
        self.assertAlmostEqual(sp.daily_chance(10), 1.0)

    def test_catalogue_loads(self):
        rules, prompts = sp.load()
        ids = [p['id'] for p in prompts]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({'tweak_style', 'rename_check', 'avatar_check', 'memory_tidy'} <= set(ids))
        self.assertIn('INTERNAL', rules)

    def test_cooldown_and_order(self):
        _, prompts = sp.load()
        log = [{'at': T0 - 86400, 'prompt_id': 'tweak_style'}]
        pool = sp.eligible(prompts, 10, log, T0)
        self.assertNotIn('tweak_style', [p['id'] for p in pool])   # inside its cooldown
        pool = sp.eligible(prompts, 10, log, T0 + 8 * 86400)
        self.assertEqual(pool[-1]['id'], 'tweak_style')            # asked most recently goes last
        self.assertNotIn('rename_check', [p['id'] for p in sp.eligible(prompts, 2, [], T0)])   # min_level 3

    def test_option_default_and_clamp(self):
        d = tempfile.mkdtemp(); f = os.path.join(d, 'o.json')
        base = {'user_id': '1', 'bot_token': 'x'}
        json.dump({'personas': [dict(base, key='aa'), dict(base, key='bb', autonomy_level=0), dict(base, key='cc', autonomy_level=99)]}, open(f, 'w'))
        o, _ = load_options(f)
        self.assertEqual([p['autonomy_level'] for p in o['personas']], [3, 0, 10])


class Tick(unittest.TestCase):
    def make(self, hour=14, level=10, roll=0.0, live=True):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory()
        self.now = datetime.datetime(2026, 11, 7, hour, 5)
        self.t = T0
        a.now = lambda: self.now; a.clock = lambda: self.t; a.log = lambda *x: None; a.rng = lambda: roll
        a.is_live = lambda k: live
        a.cfg = {'aa': {'autonomy_level': level, 'quiet_start': '22:00', 'quiet_end': '08:00', 'household': False, 'fixed_rule': 'Stay supportive.'}}
        a.stores = {'aa': PersonaStore(self.tmp.name, 'aa', lambda: self.t)}
        return a

    def test_queues_one_silent_event_and_logs(self):
        a = self.make()
        pid = a.tick_self('aa')
        self.assertTrue(pid)
        ev = a.stores['aa'].read_inbox(0, 10)
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]['type'], ev[0]['silent'], ev[0]['prompt_id']), ('self_prompt', True, pid))
        self.assertIn('INTERNAL', ev[0]['text']); self.assertIn('Stay supportive.', ev[0]['text'])
        self.assertEqual(a.stores['aa'].outbox_count(), 0)   # silent: nothing sent
        self.assertIsNone(a.tick_self('aa'))                  # never twice in a day
        self.assertEqual(len(a.stores['aa'].read_inbox(0, 10)), 1)

    def test_level_zero_is_off(self):
        a = self.make(level=0)
        self.assertIsNone(a.tick_self('aa')); self.assertEqual(a.stores['aa'].read_inbox(0, 10), [])

    def test_quiet_hours_do_not_use_up_the_day(self):
        a = self.make(hour=23)
        self.assertIsNone(a.tick_self('aa')); self.assertEqual(a.stores['aa'].read_inbox(0, 10), [])
        self.now = datetime.datetime(2026, 11, 7, 14, 5)
        self.assertTrue(a.tick_self('aa'))

    def test_dry_and_household_get_nothing(self):
        self.assertIsNone(self.make(live=False).tick_self('aa'))
        a = self.make(); a.cfg['aa']['household'] = True
        self.assertIsNone(a.tick_self('aa'))

    def test_failed_roll_drops_the_day(self):
        a = self.make(level=3, roll=0.9)   # chance 1/7
        self.assertIsNone(a.tick_self('aa'))
        a.rng = lambda: 0.0
        self.assertIsNone(a.tick_self('aa'))   # the day is used up: dropped, not fired later
        self.now = datetime.datetime(2026, 11, 8, 14, 5)
        self.assertTrue(a.tick_self('aa'))

    def test_not_within_two_hours_of_a_checkin(self):
        a = self.make()
        a.stores['aa'].log_outbox({'kind': 'checkin', 'chars': 5, 'mode': 'live'})
        self.assertEqual(a.self_prompt_blocked('aa'), 'recent check-in or reminder')
        self.t += 3 * 3600
        self.assertIsNone(a.self_prompt_blocked('aa'))
        a.stores['aa'].append_event({'type': 'reminder_due', 'source': 'x', 'facts': {}})
        self.assertEqual(a.self_prompt_blocked('aa'), 'recent check-in or reminder')

    def test_weekly_cap_and_gap(self):
        a = self.make(level=3)
        a.stores['aa'].update_state(lambda s: s.__setitem__('self_log', [{'at': T0 - 3 * 86400, 'prompt_id': 'tweak_style', 'status': 'done'}]))
        self.assertEqual(a.self_prompt_blocked('aa'), 'weekly cap reached')
        a.stores['aa'].update_state(lambda s: s.__setitem__('self_log', [{'at': T0 - 3600, 'prompt_id': 'tweak_style', 'status': 'done'}]))
        self.assertEqual(a.self_prompt_blocked('aa'), 'too soon after the last prompt')

    def test_selfdone_audit_and_close(self):
        a = self.make()
        pid = a.tick_self('aa')
        self.assertTrue(a.self_open('aa'))
        self.assertEqual(a.selfdone('aa', {'prompt_id': pid, 'action': 'bogus'})[0], 400)
        code, _ = a.selfdone('aa', {'prompt_id': pid, 'action': 'memory', 'outcome': 'tidied  two\nitems'})
        self.assertEqual(code, 200)
        self.assertFalse(a.self_open('aa'))
        code, v = a.self_log_view('aa')
        self.assertEqual((v['log'][0]['status'], v['log'][0]['action'], v['log'][0]['outcome']), ('done', 'memory', 'tidied two items'))
        self.assertEqual(a.selfdone('aa', {'prompt_id': pid, 'action': 'none'})[0], 404)   # already closed

    def test_identity_monthly_cap_inside_a_prompt(self):
        a = self.make()
        a.tick_self('aa')
        a.discord = {}
        a.stores['aa'].update_state(lambda s: s.__setitem__('identity_changes', {'nickname': [T0 - 86400]}))
        code, body = a.identity('aa', {'nickname': 'Sunny'})
        self.assertEqual(code, 429)
        code, body = a.identity('aa', {'avatar': 'aGVsbG8='})
        self.assertEqual(code, 403)   # a picture inside a prompt must come from the approved folder
        code, body = a.identity('aa', {'avatar_file': '../x.png'})
        self.assertEqual(code, 400)

    def test_approved_avatar_folder(self):
        a = self.make()
        d = tempfile.mkdtemp(); os.environ['IHP_AVATARS'] = d
        try:
            open(os.path.join(d, 'a.png'), 'wb').write(b'\x89PNG\r\n\x1a\nxx')
            os.symlink('/etc/hostname', os.path.join(d, 'l.png'))
            self.assertEqual(a.approved_avatar('a.png')[0][:4], b'\x89PNG')
            self.assertIsNone(a.approved_avatar('l.png')[0]); self.assertIsNone(a.approved_avatar('nope.png')[0])
            self.assertIsNone(a.approved_avatar('a/b.png')[0])
        finally:
            del os.environ['IHP_AVATARS']


if __name__ == '__main__':
    unittest.main()
