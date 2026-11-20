import io
import pathlib
import sys
import unittest
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import cooking_client


class Opener:
    def __init__(self, error): self.error = error
    def open(self, request, timeout=0): raise self.error


class RefusalTests(unittest.TestCase):
    def post(self, error):
        client = cooking_client.CookingClient('http://example.test', 'fixture', Opener(error))
        return client.post('/cook/display-action', {'revision': 'a' * 64})

    def test_cooking_reason_is_passed_through_briefly(self):
        err = urllib.error.HTTPError('http://example.test', 409, 'Conflict', {}, io.BytesIO(b'{"ok": false, "why": "stale display"}'))
        self.assertEqual(self.post(err), {'ok': False, 'why': 'stale display'})

    def test_unparseable_refusal_and_unreachable(self):
        err = urllib.error.HTTPError('http://example.test', 500, 'x', {}, io.BytesIO(b'<html>'))
        self.assertEqual(self.post(err), {'ok': False, 'why': 'refused'})
        self.assertEqual(self.post(urllib.error.URLError('down')), {'ok': False, 'why': 'cooking unreachable'})


if __name__ == '__main__': unittest.main()
