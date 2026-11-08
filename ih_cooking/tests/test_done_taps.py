"""DONE taps on the wall display: several rows tapped in a row all register; a repeat tap on a finished step does nothing."""
import json, pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import cookengine as C


class Taps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.t = [1000.0]
        self.e = C.Engine({'mode': 'dry'}, self.tmp.name, ts=lambda: self.t[0])
        steps = [{'at': '17:10', 'text': 'Chicken out', 'short': 'a'}, {'at': '17:10', 'text': 'Toss sprouts', 'short': 'b'}, {'at': '17:10', 'text': 'Turn carrots', 'short': 'c'},
                 {'at': '17:20', 'text': 'Later step', 'short': 'd'}]
        self.keys = [s['at'] + ' ' + s['text'] for s in steps]
        self.e.dump('plan.json', {'title': 'T', 'eat': '17:30', 'steps': steps})
        self.e.dump('state.json', {'done': [], 'told': self.keys[:3]})            # the first three were announced, the 17:20 one was not
        self.e.dump('shownext.json', self.keys[:3])

    def test_three_rows_tapped_one_after_another(self):
        for row in (0, 1, 2):
            self.t[0] += 2
            self.e.dump('shownext.json', [k for k in self.keys[:3] if k not in self.e.load('state.json', {})['done']])   # the screen re-orders after each DONE
            r = self.e.done(row); self.assertTrue(r['ok'], (row, r)); self.assertEqual(r['key'], self.keys[row])
        self.assertEqual(self.e.load('state.json', {})['done'], self.keys[:3])

    def test_repeat_tap_on_the_same_row_does_nothing_and_never_clears_the_next_step(self):
        self.assertTrue(self.e.done(0)['ok']); self.t[0] += 2
        self.e.dump('shownext.json', self.keys[1:3] + [self.keys[3]])
        r = self.e.done(0); self.assertFalse(r['ok']); self.assertEqual(r['why'], 'already done')
        self.assertEqual(self.e.load('state.json', {})['done'], [self.keys[0]])

    def test_step_not_announced_yet_is_still_blocked(self):
        self.e.dump('shownext.json', [self.keys[3]])
        r = self.e.done(0); self.assertFalse(r['ok']); self.assertEqual(r['why'], 'not announced yet')

    def test_after_the_window_taps_use_the_current_screen(self):
        self.assertTrue(self.e.done(0)['ok']); self.t[0] += 200
        self.e.dump('shownext.json', self.keys[1:3])
        r = self.e.done(0); self.assertTrue(r['ok']); self.assertEqual(r['key'], self.keys[1])


if __name__ == '__main__': unittest.main()
