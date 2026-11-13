"""A cooking 404 no_such_recipe is a normal answer: counted as a call, not as failed. Other reasons stay failures."""
import os, sys, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import manager


class BenignWhy(unittest.TestCase):
    def run_lines(self, *lines):
        now = time.time()
        return [r[2] for r in manager.parse_usage("ih_cooking", "\n".join(lines), now)]

    def test_no_such_recipe_is_ok(self):
        self.assertEqual(self.run_lines("12:00:01 invoke id=a ok=False why=no_such_recipe http=404"), [True])

    def test_not_found_is_ok(self):
        self.assertEqual(self.run_lines("12:00:02 invoke id=b ok=False why=not_found http=404"), [True])

    def test_rejected_stays_failed(self):
        self.assertEqual(self.run_lines("12:00:03 invoke id=c ok=False why=rejected http=400"), [False])

    def test_plain_lines_unchanged(self):
        self.assertEqual(self.run_lines("12:00:04 invoke id=d ok=True", "12:00:05 invoke id=e ok=False"), [True, False])


if __name__ == "__main__":
    unittest.main()
