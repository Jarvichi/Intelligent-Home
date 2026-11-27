"""Background jobs remain independent and failed snapshots are retried."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import main


class Background(unittest.TestCase):
    def test_snapshot_retries_and_runs_when_other_jobs_fail(self):
        store = mock.Mock(path='/tmp/ih.db')
        opts = {'mirror': 'on', 'mirror_entity': 'todo.test'}
        snapshot = mock.Mock(side_effect=[OSError('disk full'), 'backup'])
        with mock.patch.object(main.MIRROR, 'run', side_effect=RuntimeError()), mock.patch.object(main.RMD, 'run', side_effect=RuntimeError()) as reminders:
            args = (store, opts, None, {}, None, '2026-11-27', 9, snapshot, lambda *a: None)
            day = main.background_pass(*args)
            self.assertIsNone(day)
            day = main.background_pass(store, opts, None, {}, day, '2026-11-27', 9, snapshot, lambda *a: None)
            self.assertEqual(day, '2026-11-27')
            main.background_pass(store, opts, None, {}, day, '2026-11-27', 9, snapshot, lambda *a: None)
        self.assertEqual(snapshot.call_count, 2)
        self.assertEqual(reminders.call_count, 3)
