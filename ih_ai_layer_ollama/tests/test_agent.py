import json, os, sys, tempfile, unittest, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import agent as A

PNG = 'iVBORw0KGgo='


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status = json.dumps(data).encode(), status
    def read(self, n=-1): return self.data
    def __enter__(self): return self
    def __exit__(self, *a): return False


def mk(url='http://192.0.2.9:11434', opener=None, **kw):
    o = A.load_options('/nonexistent')
    o.update(ollama_url=url, shadow=False, **kw)
    calls = []
    def op(req, timeout=0):
        calls.append((req.full_url, req.data))
        return opener(req, timeout) if opener else Resp({'message': {'content': ' hello '}})
    return A.Agent(o, data=tempfile.mkdtemp(), opener=op), calls


class T(unittest.TestCase):
    def test_empty_url_is_off(self):
        a, calls = mk(url='')
        r = a.ask({'text': 'hi', 'capability': 'general_qa'})
        self.assertFalse(r['ok']); self.assertEqual(r['error'], 'unavailable'); self.assertEqual(calls, [])

    def test_bad_url_is_off(self):
        self.assertEqual(A.clean_url('http://u:p@host/x'), ''); self.assertEqual(A.clean_url('ftp://h'), '')
        self.assertEqual(A.clean_url('http://nas.lan:11434/'), 'http://nas.lan:11434')

    def test_success(self):
        a, calls = mk()
        r = a.ask({'text': 'hi', 'capability': 'general_qa', 'request_id': '1'})
        self.assertTrue(r['ok']); self.assertEqual(r['answer'], 'hello'); self.assertTrue(calls[0][0].endswith('/api/chat'))
        self.assertEqual(json.loads(calls[0][1])['model'], 'llama3.2:3b')

    def test_vision_uses_vision_model_and_image(self):
        a, calls = mk()
        r = a.ask({'text': 'parcel?', 'capability': 'vision', 'image': PNG})
        self.assertTrue(r['ok']); b = json.loads(calls[0][1])
        self.assertEqual(b['model'], 'llava:7b'); self.assertEqual(b['messages'][1]['images'], [PNG])

    def test_vision_without_image(self):
        a, calls = mk()
        self.assertEqual(a.ask({'text': 'x', 'capability': 'vision'})['why'], 'bad_request'); self.assertEqual(calls, [])

    def test_http_500(self):
        def op(req, t): raise urllib.error.HTTPError(req.full_url, 500, 'err', {}, None)
        a, _ = mk(opener=op)
        r = a.ask({'text': 'hi', 'capability': 'general_qa'})
        self.assertFalse(r['ok']); self.assertEqual(r['why'], 'upstream_error')

    def test_timeout_and_host_off(self):
        def op(req, t): raise TimeoutError('timed out')
        a, _ = mk(opener=op)
        self.assertEqual(a.ask({'text': 'hi', 'capability': 'general_qa'})['why'], 'timeout')
        def op2(req, t): raise ConnectionRefusedError()
        a, _ = mk(opener=op2)
        self.assertEqual(a.ask({'text': 'hi', 'capability': 'general_qa'})['why'], 'unavailable')

    def test_model_missing(self):
        def op(req, t): return Resp({'error': 'model not found'})
        a, _ = mk(opener=op)
        self.assertEqual(a.ask({'text': 'hi', 'capability': 'general_qa'})['why'], 'empty')

    def test_shadow_sends_nothing(self):
        a, calls = mk(); a.opts['shadow'] = True
        r = a.ask({'text': 'hi', 'capability': 'general_qa'})
        self.assertTrue(r['ok']); self.assertEqual(r['mode'], 'dry'); self.assertEqual(calls, [])

    def test_daily_cap(self):
        a, _ = mk(daily_cap=1)
        self.assertTrue(a.ask({'text': 'a', 'capability': 'general_qa'})['ok'])
        self.assertEqual(a.ask({'text': 'b', 'capability': 'general_qa'})['why'], 'cap')

    def test_unknown_capability_and_model(self):
        a, _ = mk()
        self.assertEqual(a.ask({'text': 'x', 'capability': 'game_art_search'})['why'], 'bad_request')
        self.assertEqual(a.ask({'text': 'x', 'capability': 'general_qa', 'model': 'gpt'})['why'], 'model_not_available')

    def test_register_unhealthy_when_host_off(self):
        sent = []
        def op(req, t):
            if req.full_url.endswith('/api/tags'): raise ConnectionRefusedError()
            sent.append(json.loads(req.data)); return Resp({}, 200)
        a, _ = mk(opener=op); a.opts['register_key'] = 'k'
        self.assertTrue(a.register()); self.assertFalse(sent[0]['healthy']); self.assertIn('vision', sent[0]['capabilities'])


if __name__ == '__main__':
    unittest.main()
