import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import facts
import main
import consumer_progress


class ConsumerProgressIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_facts, self.old_progress = main.FACTS, main.PROGRESS
        main.FACTS = facts.Facts(self.tmp.name, [{'module': 'assistant', 'key': 'test', 'write': True}], log=lambda _: None)
        main.PROGRESS = consumer_progress.Monitor(os.path.join(self.tmp.name, 'progress.json'))

    def tearDown(self):
        main.FACTS, main.PROGRESS = self.old_facts, self.old_progress
        self.tmp.cleanup()

    def test_health_rows_are_content_free_and_detect_stalled_consumer(self):
        writer = {'module': 'assistant', 'key': 'test', 'write': True}
        value = {'epoch': 'queue-v1', 'produced_seq': 3, 'acked_seq': 0,
                 'oldest_pending_at': 1000, 'observed_at': 1000, 'heartbeat_at': 1000,
                 'consistent': True, 'enabled': True}
        code, _ = main.FACTS.put(writer, 'assistant', 'progress_sample', {'value': value})
        self.assertEqual(code, 200)
        self.assertEqual(main.consumer_progress_rows(1000)[0]['status'], 'waiting')
        value.update(observed_at=1400, heartbeat_at=1400)
        main.FACTS.put(writer, 'assistant', 'progress_sample', {'value': value})
        rows = main.consumer_progress_rows(1400)
        self.assertEqual(rows[0]['status'], 'stalled')
        self.assertEqual(rows[0]['pending'], 3)
        self.assertNotIn('value', rows[0])
        self.assertNotIn('text', rows[0])

    def test_expired_or_missing_fact_reports_unknown_after_checkpoint(self):
        writer = {'module': 'assistant', 'key': 'test', 'write': True}
        value = {'epoch': 'queue-v1', 'produced_seq': 1, 'acked_seq': 0,
                 'oldest_pending_at': 1000, 'observed_at': 1010, 'heartbeat_at': 1010,
                 'consistent': True, 'enabled': True}
        main.FACTS.put(writer, 'assistant', 'progress_sample', {'value': value})
        main.consumer_progress_rows(1010)
        main.FACTS.delete(writer, 'assistant', 'progress_sample')
        rows = main.consumer_progress_rows(1020)
        self.assertEqual(rows[0]['status'], 'unknown')
        self.assertEqual(rows[0]['reason'], 'snapshot_missing')


if __name__ == '__main__':
    unittest.main()
