"""0.6.1: an open [replan] marker means no plan; REPLAN instruction; the [replan] marker is not skipped by the [plan...] skip tag."""
import os, sys, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import feed

NOW = datetime(2026, 11, 13, 1, 0, tzinfo=timezone.utc)
RP = '[replan] the API it names does not exist (old plan: projects/x/plan) [job 20261101-000001]\nrest of notes'


class Replan(unittest.TestCase):
    def test_has_plan_false_with_marker(self):
        self.assertTrue(feed.has_plan('PLAN (unreviewed): brain projects/x/plan, AI job 20261101-000001.'))
        self.assertFalse(feed.has_plan(RP))
        self.assertFalse(feed.has_plan('plain notes'))

    def test_instruction_replan(self):
        t = feed.instruction({'summary': 'T', 'description': RP})
        self.assertIn('REPLAN: an earlier plan for this item was graded wrong', t)
        self.assertIn('PLAN (Claude, replanned): <note path>', t)
        self.assertIn('already_done', t)
        self.assertNotIn('NO PLAN YET', t)

    def test_instruction_no_plan_and_plan_unchanged(self):
        self.assertIn('NO PLAN YET', feed.instruction({'summary': 'T', 'description': 'plain'}))
        t = feed.instruction({'summary': 'T', 'description': 'PLAN (Claude): brain projects/x/plan'})
        self.assertNotIn('NO PLAN YET', t); self.assertNotIn('REPLAN', t)

    def test_skip_tag_does_not_match_replan(self):
        self.assertIsNone(feed.SKIP_TAG.search(RP))
        self.assertTrue(feed.SKIP_TAG.search('[plan] x'))
        it = {'uid': 'a', 'summary': 'Task', 'description': RP, 'due': '2026-11-14'}
        self.assertEqual(feed.pick([it], [], {}, NOW, any_item=True)[0]['uid'], 'a')


if __name__ == '__main__':
    unittest.main()
