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


    def test_relay_uses_registered_sample_time_and_only_safe_counters(self):
        class Module:
            def children_view(self, now):
                return {'backup': {'name':'ih_jobs_backup','sampled_at':900,'mode':'dry','healthy':True,
                    'status':{'runs':4,'errors':1,'would_set_total':2,'last_ok':True,'last_run_s':10,
                              'last_error':'PRIVATE DETAIL', 'url':'PRIVATE URL'}},
                    'invalid': {'name':'ih_assistant','sampled_at':900,'status':{}},
                    'malformed': {'name':'ih_jobs_bad','sampled_at':900,'status':{'runs':True,'errors':0,'would_set_total':0}}}
        rows=main.child_contributions(Module(),1000)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['sampled_at'],900)
        self.assertEqual(rows[0]['last_activity_at'],890)
        self.assertEqual(rows[0]['state'],'ok')
        self.assertNotIn('PRIVATE',json.dumps(rows))
        self.assertEqual([m['value'] for m in rows[0]['metrics']],[4,1,2])


if __name__ == '__main__':
    unittest.main()
