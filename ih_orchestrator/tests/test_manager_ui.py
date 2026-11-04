"""Add-on-owned Manager metadata stays bounded and plain text."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import manager


class ManagerUI(unittest.TestCase):
    def test_contribution_filters_unrelated_fields(self):
        catalogue = [{"slug": "ih_jobs_backup", "name": "Backup", "requires": [],
                      "ui": {"title": "Backup jobs", "purpose": "Reports backup status",
                             "secret": "do not return", "description": "not part of the contract"}}]

        def supervisor(method, path, body=None):
            if path == "/addons/self/info":
                return {"slug": "abc123_ih_orchestrator", "repository": "repo"}
            if path == "/addons":
                return {"addons": []}
            raise AssertionError(path)

        instance = manager.Manager(catalogue, sup=supervisor, usage_path="/nonexistent/usage.json")
        rows = instance._rows()
        self.assertEqual(rows[0]["ui"], {"title": "Backup jobs", "purpose": "Reports backup status"})


if __name__ == "__main__":
    unittest.main()
