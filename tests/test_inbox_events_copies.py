"""inbox_events.py lives in ih_discord and ih_assistant (separate images): the two copies must stay identical."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Copies(unittest.TestCase):
    def test_identical(self):
        a = (ROOT / 'ih_discord/app/inbox_events.py').read_bytes()
        b = (ROOT / 'ih_assistant/app/inbox_events.py').read_bytes()
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
