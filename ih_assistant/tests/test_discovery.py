import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import selfprompts as sp
from store import PersonaStore
from main import Assistant

T0 = 1793955600.0


def make(level=5):
    a = Assistant.__new__(Assistant)
    a.tmp = tempfile.TemporaryDirectory()
    a.clock = lambda: T0
    a.log = lambda *x: None
    a.stores = {'aa': PersonaStore(a.tmp.name, 'aa', lambda: T0)}
    return a


class Catalogue(unittest.TestCase):
    def test_prompts_present_and_safe(self):
        _, prompts = sp.load()
        by = {p['id']: p for p in prompts}
        for pid in ('new_releases', 'taste_followup', 'taste_use'):
            self.assertEqual(by[pid]['group'], 'discovery')
        t = by['new_releases']['text'].lower()
        self.assertIn('at most one', t)
        self.assertIn('never give a purchase link', t)
        self.assertIn('say nothing', t)
        self.assertEqual(by['new_releases']['cooldown_days'], 7)


class Asked(unittest.TestCase):
    def test_no_duplicates(self):
        a = make()
        self.assertEqual(a.asked_add('aa', {'kind': 'film', 'creator': 'A B', 'title': 'Dune', 'date': '2026-11-20'}), (200, {'ok': True, 'duplicate': False}))
        self.assertEqual(a.asked_add('aa', {'kind': 'film', 'creator': 'a b', 'title': ' dune '})[1]['duplicate'], True)
        self.assertEqual(len(a.stores['aa'].state()['asked']), 1)
        self.assertEqual(a.asked_add('aa', {'kind': 'album', 'title': 'Dune'})[1]['duplicate'], False)   # other kind

    def test_answer_updates(self):
        a = make()
        a.asked_add('aa', {'kind': 'game', 'title': 'Hades'})
        a.asked_add('aa', {'kind': 'game', 'title': 'Hades', 'answer': 'liked'})
        e = a.stores['aa'].state()['asked'][0]
        self.assertEqual(e['answer'], 'liked')
        self.assertEqual(set(e), {'kind', 'creator', 'title', 'date', 'answer', 'answered_at'})

    def test_validation(self):
        a = make()
        self.assertEqual(a.asked_add('aa', {'kind': 'book', 'title': 'x'})[0], 400)
        self.assertEqual(a.asked_add('aa', {'kind': 'film'})[0], 400)
        self.assertEqual(a.asked_add('aa', {'kind': 'film', 'title': 'x', 'answer': 'maybe'})[0], 400)
        self.assertEqual(a.asked_add('aa', {'kind': 'film', 'title': 'x', 'date': 'tomorrow'})[0], 400)


class Tastes(unittest.TestCase):
    def test_save_and_clear(self):
        a = make()
        self.assertEqual(a.taste_set('aa', {'key': 'film:sci-fi', 'verdict': 'liked'})[0], 200)
        self.assertEqual(a.stores['aa'].state()['tastes']['film:sci-fi']['verdict'], 'liked')
        a.taste_set('aa', {'key': 'film:sci-fi', 'verdict': 'disliked'})
        self.assertEqual(a.stores['aa'].state()['tastes']['film:sci-fi']['verdict'], 'disliked')
        a.taste_set('aa', {'key': 'film:sci-fi', 'verdict': 'clear'})
        self.assertEqual(a.stores['aa'].state()['tastes'], {})
        self.assertEqual(a.taste_set('aa', {'key': 'x' * 80, 'verdict': 'liked'})[0], 400)
        self.assertEqual(a.taste_set('aa', {'key': 'ok', 'verdict': 'meh'})[0], 400)


class Usable(unittest.TestCase):
    P = {'group': 'discovery'}

    def test_discovery_off(self):
        self.assertFalse(sp.usable(self.P, {'discovery_off': True}))
        self.assertTrue(sp.usable(self.P, {}))
        self.assertTrue(sp.usable({'group': 'identity'}, {'discovery_off': True}))

    def test_needs(self):
        self.assertFalse(sp.usable({'group': 'discovery', 'needs': 'tastes'}, {}))
        self.assertTrue(sp.usable({'group': 'discovery', 'needs': 'tastes'}, {'tastes': {'a': {'verdict': 'liked'}}}))
        self.assertFalse(sp.usable({'group': 'discovery', 'needs': 'tried'}, {'asked': [{'kind': 'film', 'title': 'x', 'answer': 'no'}]}))
        self.assertTrue(sp.usable({'group': 'discovery', 'needs': 'tried'}, {'asked': [{'kind': 'film', 'title': 'x', 'answer': 'yes'}]}))

    def test_discovery_endpoint(self):
        a = make()
        self.assertEqual(a.discovery_set('aa', {'allowed': 'no'})[0], 400)
        a.discovery_set('aa', {'allowed': False})
        self.assertTrue(a.stores['aa'].state()['discovery_off'])
        a.discovery_set('aa', {'allowed': True})
        self.assertNotIn('discovery_off', a.stores['aa'].state())


class Summaries(unittest.TestCase):
    def test_text(self):
        self.assertEqual(sp.summary_tastes({}), 'none saved yet')
        self.assertIn('film:sci-fi=liked', sp.summary_tastes({'film:sci-fi': {'verdict': 'liked'}}))
        self.assertIn('"Dune"', sp.summary_asked([{'kind': 'film', 'title': 'Dune', 'creator': 'X', 'answer': ''}]))


class Tick(unittest.TestCase):
    def test_prompt_text_filled_and_off_switch(self):
        import datetime
        a = make()
        a.now = lambda: datetime.datetime(2026, 11, 7, 14, 5)
        a.rng = lambda: 0.0
        a.is_live = lambda k: True
        a.cfg = {'aa': {'autonomy_level': 10, 'quiet_start': '22:00', 'quiet_end': '08:00', 'household': False, 'fixed_rule': ''}}
        a.asked_add('aa', {'kind': 'film', 'title': 'Dune', 'creator': 'DV'})
        import selfprompts
        orig = selfprompts.load
        rules, prompts = orig()
        selfprompts.load = lambda: (rules, [p for p in prompts if p['id'] == 'new_releases'])
        try:
            self.assertEqual(a.tick_self('aa'), 'new_releases')
            ev = a.stores['aa'].read_inbox(0, 10)[0]
            self.assertIn('"Dune"', ev['text'])
            self.assertNotIn('{asked}', ev['text'])
            a.stores['aa'].update_state(lambda x: (x.pop('self_day'), x.__setitem__('self_log', []), x.__setitem__('discovery_off', True)))
            self.assertIsNone(a.tick_self('aa'))   # the person said no: nothing queued
        finally:
            selfprompts.load = orig


if __name__ == '__main__':
    unittest.main()
