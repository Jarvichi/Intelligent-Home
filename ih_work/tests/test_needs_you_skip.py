"""0.5.3: the feeder skips items with an open [needs-you #N] marker; [done-you #N] clears it."""
import os, sys, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import feed

NOW = datetime(2026, 11, 13, 1, 0, tzinfo=timezone.utc)


def item(uid, desc):
    return {'uid': uid, 'summary': 'Task ' + uid, 'description': desc, 'due': '2026-11-14'}


class NeedsYouSkip(unittest.TestCase):
    def pick(self, *items):
        return feed.pick(list(items), [], {}, NOW, any_item=True)[0]

    def test_open_marker_skipped(self):
        self.assertIsNone(self.pick(item('a', 'x\n[needs-you #5] call the bank')))

    def test_done_marker_clears(self):
        self.assertEqual(self.pick(item('a', 'x\n[done-you #5 13 Nov]'))['uid'], 'a')
        self.assertEqual(self.pick(item('a', '[needs-you #5] call\n[done-you #5 13 Nov]'))['uid'], 'a')

    def test_other_number_does_not_clear(self):
        self.assertIsNone(self.pick(item('a', '[needs-you #5] call\n[done-you #6 13 Nov]')))

    def test_others_still_picked(self):
        self.assertEqual(self.pick(item('a', '[needs-you #5] call'), item('b', 'plain'))['uid'], 'b')

    def test_helper(self):
        self.assertTrue(feed.open_needs_you('[needs-you #1] x'))
        self.assertFalse(feed.open_needs_you('[needs-ok #1] x'))
        self.assertFalse(feed.open_needs_you(None))


if __name__ == '__main__':
    unittest.main()
