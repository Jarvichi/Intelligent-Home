import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import levels
from store import PersonaStore
from main import Assistant

T0 = 1793955600.0


class OfferTests(unittest.TestCase):
    def test_due_rules(self):
        self.assertTrue(levels.offer_due(4, {}, T0))
        self.assertFalse(levels.offer_due(10, {}, T0))
        self.assertFalse(levels.offer_due(4, {'last': T0 - 3600}, T0))
        self.assertTrue(levels.offer_due(4, {'last': T0 - 8 * 86400}, T0))
        self.assertFalse(levels.offer_due(4, {'last': 1, 'declined': True}, T0))
        self.assertFalse(levels.offer_due(4, {'last': 1, 'yes_at': T0 - 5}, T0))

    def test_flow(self):
        a = Assistant.__new__(Assistant)
        tmp = tempfile.TemporaryDirectory()
        a.clock = lambda: T0; a.tz = None; a.log = lambda m: None
        a.stores = {'aa': PersonaStore(tmp.name, 'aa', a.clock)}
        lv = {'checkin_level': 4}
        p = a._offer_part('aa', lv)
        self.assertTrue(p['checkin_offer']); self.assertIn('check in more often', p['offer_prompt'])
        b = a._with_offer('aa', {'prompt': 'X', **p})
        self.assertTrue(b['prompt'].startswith('X\n- Check-in offer'))
        self.assertEqual(a.checkin_offer('aa', {'answer': 'asked'})[0], 200)
        self.assertFalse(a._offer_part('aa', lv)['checkin_offer'])
        self.assertEqual(a.checkin_offer('aa', {'answer': 'maybe'})[0], 400)
        a.checkin_offer('aa', {'answer': 'no'})
        a.clock = lambda: T0 + 30 * 86400
        self.assertFalse(a._offer_part('aa', lv)['checkin_offer'])


if __name__ == '__main__':
    unittest.main()
