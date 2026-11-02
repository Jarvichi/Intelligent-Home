import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import manager


class ManagerProgressTests(unittest.TestCase):
    def test_health_exposes_only_validated_progress_rows(self):
        m = manager.Manager.__new__(manager.Manager)
        m.health_src = lambda: {
            'services': [], 'counts': {}, 'total': 0, 'stale': False, 'age_s': 1,
            'consumer_progress': [{'consumer': 'sample', 'status': 'stalled', 'pending': 2,
                                  'oldest_pending_age_s': 400, 'no_progress_s': 350,
                                  'reason': 'pending_without_outcome_progress'}]
        }
        code, body = m.health()
        self.assertEqual(code, 200)
        self.assertEqual(body['consumer_progress'][0]['status'], 'stalled')
        self.assertNotIn('text', body)


if __name__ == '__main__':
    unittest.main()
