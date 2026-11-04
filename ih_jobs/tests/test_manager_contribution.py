"""Jobs sends real operational counts without sending its options or job details."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import main


class PushTests(unittest.TestCase):
    def test_push_has_bounded_manager_summary(self):
        class Module:
            opts = {"ingest_token": "private-token", "ingest_url": "http://orchestrator"}

            def status(self):
                return {"healthy": True, "mode": "dry-run", "would_set_total": 7,
                        "children": {"backup": {}},
                        "jobs": {"backupstore": {"runs": 3, "errors": 1, "last_ok": False, "last_run_s": 15},
                                 "metoffice": {"runs": 2, "errors": 0, "last_ok": True, "last_run_s": 60}},
                        "last_would": ["private job detail"]}

        captured = {}

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False

        def opener(request, timeout):
            captured.update(json.loads(request.data))
            return Response()

        self.assertTrue(main.push(Module(), {"name": "ih_jobs", "health": []}, now=1000, opener=opener))
        panel = captured["ui_contribution"]
        self.assertEqual(panel["version"], 1)
        self.assertEqual(panel["state"], "attention")
        self.assertEqual(panel["last_activity_at"], 985)
        self.assertEqual([m["value"] for m in panel["metrics"]], [5, 1, 1])
        self.assertNotIn("private job detail", json.dumps(panel))
        self.assertNotIn("private-token", json.dumps(panel))


if __name__ == '__main__':
    unittest.main()
