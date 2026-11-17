import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import health, ailayer, today


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.h = health.ProviderHealth(self.c, threshold=3, cooldown_s=60)

    def test_opens_at_threshold(self):
        for _ in range(2): self.h.record('p', False, 'HTTPError')
        self.assertTrue(self.h.available('p'))
        self.h.record('p', False, 'HTTPError')
        self.assertFalse(self.h.available('p'))

    def test_trial_after_cooldown_and_failed_trial_reopens(self):
        for _ in range(3): self.h.record('p', False, 'HTTPError')
        self.c.t += 61
        self.assertTrue(self.h.available('p'))
        self.h.record('p', False, 'HTTPError')
        self.assertFalse(self.h.available('p'))
        self.c.t += 61
        self.h.record('p', True)
        self.assertTrue(self.h.available('p'))
        self.assertEqual(self.h.status()['p']['streak'], 0)

    def test_success_resets_streak(self):
        for _ in range(2): self.h.record('p', False, 'x')
        self.h.record('p', True)
        for _ in range(2): self.h.record('p', False, 'x')
        self.assertTrue(self.h.available('p'))

    def test_status_shape_and_window(self):
        for _ in range(60): self.h.record('p', True)
        for _ in range(3): self.h.record('p', False, 'bad type!\nsecret text')
        s = self.h.status()['p']
        self.assertEqual(set(s), {'state', 'until', 'last_error', 'streak', 'calls', 'ok', 'failed'})
        self.assertEqual((s['calls'], s['ok'], s['failed'], s['state']), (50, 47, 3, 'cooling_down'))
        self.assertEqual(s['until'], 1060.0)
        self.assertNotIn('\n', s['last_error']); self.assertNotIn(' ', s['last_error'])


class RoutingTest(unittest.TestCase):
    def make(self):
        c = Clock()
        l = ailayer.Layer({'shadow': False, 'rate_per_min': 100, 'max_tries': 2, 'agent_timeout_s': 5, 'providers': [], 'provider_keys': {},
                           'fail_threshold': 2, 'cooldown_minutes': 1}, data=tempfile.mkdtemp(), clock=c)
        l.agents = {n: {'url': 'http://x', 'capabilities': ['general_qa'], 'version': '1', 'healthy': True, 'seen': 10 ** 9, 'models': []} for n in ('a', 'b')}
        c.t = 10 ** 9
        return l, c

    def test_cooling_provider_is_skipped_unless_all_cool(self):
        l, c = self.make()
        for _ in range(2): l.health.record('a', False, 'HTTPError')
        self.assertEqual(l.ranked('general_qa'), ['b'])
        for _ in range(2): l.health.record('b', False, 'HTTPError')
        self.assertEqual(sorted(l.ranked('general_qa')), ['a', 'b'])
        c.t += 61
        self.assertEqual(sorted(l.ranked('general_qa')), ['a', 'b'])

    def test_options_defaults_and_today_row(self):
        o = ailayer.load_options('/nonexistent')
        self.assertEqual((o['fail_threshold'], o['cooldown_minutes']), (5, 30))
        l, c = self.make()
        for _ in range(2): l.health.record('a', False, 'HTTPError')
        st = {'mode': 'live', 'providers': [{'name': 'a', 'provider': 'groq', 'configured': True}], 'health': l.health.status()}
        row = today.build(st)['rows'][0]
        self.assertEqual(row['health']['state'], 'cooling_down')
        self.assertEqual(row['health']['last_error'], 'HTTPError')


if __name__ == '__main__':
    unittest.main()
