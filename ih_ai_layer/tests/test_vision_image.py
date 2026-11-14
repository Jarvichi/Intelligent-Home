"""An image request is routed only to a 'vision' agent and never logged. No network."""
import base64, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import ailayer


class Vision:
    CAPABILITIES = ['vision']
    VERSION = '1'
    seen = None

    def ask(self, obj, timeout=None):
        Vision.seen = obj
        return {'ok': True, 'request_id': obj['request_id'], 'answer': 'parcel: yes', 'model': 'm'}


def make(with_agent=True):
    layer = ailayer.Layer({'shadow': False, 'rate_per_min': 100, 'max_tries': 2, 'agent_timeout_s': 5, 'providers': [], 'provider_keys': {}}, data=tempfile.mkdtemp())
    if with_agent:
        layer.agents['v'] = {'models': [], 'url': None, 'capabilities': ['vision'], 'version': '1', 'healthy': True, 'seen': layer.clock(), 'local': Vision()}
    return layer


IMG = base64.b64encode(b'\xff\xd8\xff fake jpeg').decode()


class T(unittest.TestCase):
    def test_routed_to_vision_agent(self):
        code, r = make().request({'text': 'is there a parcel?', 'request_id': 'r1', 'image': IMG})
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['capability'], 'vision')
        self.assertEqual(Vision.seen['image'], IMG)

    def test_no_agent(self):
        code, r = make(False).request({'text': 'x', 'request_id': 'r2', 'image': IMG})
        self.assertFalse(r['ok'])
        self.assertIn("'vision'", r['answer'])

    def test_bad_image(self):
        for bad in ('', 'not base64!!', 5, 'A' * (ailayer.MAX_IMAGE_B64 + 1)):
            code, r = make().request({'text': 'x', 'request_id': 'r3', 'image': bad})
            self.assertEqual(code, 400, bad if isinstance(bad, int) else bad[:10])

    def test_wrong_capability_or_model(self):
        code, r = make().request({'text': 'x', 'request_id': 'r4', 'image': IMG, 'capability': 'general_qa'})
        self.assertEqual(code, 400)
        code, r = make().request({'text': 'x', 'request_id': 'r5', 'image': IMG, 'model': 'abc'})
        self.assertEqual(code, 400)


if __name__ == '__main__':
    unittest.main()
