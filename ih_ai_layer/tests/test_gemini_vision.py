"""Gemini 'vision': one JPEG/PNG + question, fake opener, no network."""
import base64, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from providers.gemini import Gemini
from providers.groq import Groq

JPG = base64.b64encode(b'\xff\xd8\xff\xe0 fake').decode()


class R:
    def __enter__(s): return s
    def __exit__(s, *a): return False
    def read(s, n=-1): return json.dumps({'candidates': [{'content': {'parts': [{'text': 'No parcel.'}]}}]}).encode()


def make(seen):
    def opener(req, timeout=None):
        seen.append(json.loads(req.data))
        return R()
    o = Gemini.make_opts({'name': 'gemini', 'enabled': True, 'key': 'k', 'settings': {'shadow': False, 'spend_cap': 5}})
    return Gemini(o, tempfile.mkdtemp(), opener=opener)


class T(unittest.TestCase):
    def test_vision_sends_inline_picture(self):
        seen = []
        r = make(seen).ask({'request_id': 'a', 'capability': 'vision', 'text': 'Is there a parcel?', 'image': JPG})
        self.assertTrue(r['ok'], r)
        parts = seen[0]['contents'][0]['parts']
        self.assertEqual(parts[0]['inline_data'], {'mime_type': 'image/jpeg', 'data': JPG})
        self.assertEqual(parts[1]['text'], 'Is there a parcel?')

    def test_not_a_jpeg_or_png(self):
        seen = []
        r = make(seen).ask({'request_id': 'a', 'capability': 'vision', 'text': 'q', 'image': base64.b64encode(b'hello world!!!').decode()})
        self.assertFalse(r['ok'])
        self.assertEqual(seen, [])

    def test_text_unchanged_and_others_have_no_vision(self):
        seen = []
        r = make(seen).ask({'request_id': 'a', 'capability': 'general_qa', 'text': 'hi'})
        self.assertTrue(r['ok'])
        self.assertEqual(seen[0]['contents'][0]['parts'], [{'text': 'hi'}])
        self.assertNotIn('vision', Groq.CAPABILITIES)


if __name__ == '__main__':
    unittest.main()
