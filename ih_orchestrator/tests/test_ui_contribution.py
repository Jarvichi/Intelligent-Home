"""The Manager accepts only bounded add-on-owned status data."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import main
import manager
import ui_contribution


class UIContributionTests(unittest.TestCase):
    def setUp(self):
        self.saved_state, self.saved_save = main.STATE, main.save_state
        main.STATE = {"manifests": [], "status": [], "received_at": None, "extra": {}}
        main.save_state = lambda: None
        self.panel = {"version": 1, "state": "ok", "summary": "dry-run mode; 2 built-in runs",
                      "next_step": "Check the Jobs view", "last_activity_at": 1000,
                      "metrics": [{"label": "Built-in runs", "value": 2}]}

    def tearDown(self):
        main.STATE, main.save_state = self.saved_state, self.saved_save

    def test_ingest_keeps_only_allowed_fields_and_marks_old_report_stale(self):
        panel = dict(self.panel, secret="must not appear", metrics=[{"label": "Built-in runs", "value": 2, "option": "private"}])
        body = {"source": "ih_jobs", "manifests": [{"name": "ih_jobs", "health": []}],
                "status": [{"name": "ih_jobs"}], "ui_contribution": panel}
        code, _ = main.ingest(json.dumps(body).encode(), now=1100)
        self.assertEqual(code, 200)
        rows = main.contribution_rows(main.STATE["extra"], now=1101)
        self.assertEqual(rows[0]["metrics"], [{"label": "Built-in runs", "value": 2}])
        self.assertNotIn("secret", rows[0])
        self.assertFalse(rows[0]["stale"])
        self.assertTrue(main.contribution_rows(main.STATE["extra"], now=1221)[0]["stale"])
        self.assertEqual(main.contribution_rows(main.STATE["extra"], now=1100 + main.EXTRA_MAX_AGE + 1), [])
        code, report = manager.Manager([], health=lambda: {"ui_contributions": rows}).health()
        self.assertEqual(code, 200)
        self.assertEqual(report["ui_contributions"], rows)

    def test_rejects_mismatched_source_and_unsafe_data(self):
        body = {"source": "ih_jobs", "manifests": [{"name": "ih_games", "health": []}],
                "status": [{"name": "ih_games"}], "ui_contribution": self.panel}
        self.assertEqual(main.ingest(json.dumps(body).encode(), now=1100)[0], 400)
        self.assertEqual(main.STATE["extra"], {})
        for bad in (dict(self.panel, summary="<script>"), dict(self.panel, version=2),
                    dict(self.panel, metrics=[{"label": "Token", "value": "secret"}])):
            with self.assertRaises(ValueError):
                ui_contribution.validate(bad, "ih_jobs")


if __name__ == '__main__':
    unittest.main()
