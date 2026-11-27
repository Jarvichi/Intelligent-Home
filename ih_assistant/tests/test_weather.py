import io, json, os, sys, unittest, urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from main import Assistant


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def make(token='sekrit-token', url='http://x:8799', opener=None):
    a = Assistant.__new__(Assistant)
    a.opts = {'weather_token': token, 'weather_url': url}
    a.logs = []; a.log = lambda *x: a.logs.append(' '.join(str(i) for i in x))
    a.weather_opener = opener
    return a


class WeatherTests(unittest.TestCase):
    def test_ok_and_token_sent(self):
        seen = {}
        def op(req, timeout=None):
            seen['url'] = req.full_url; seen['auth'] = req.get_header('Authorization'); seen['t'] = timeout
            return Resp(json.dumps({'now': {'condition': 'sunny', 'temp': 9}}).encode())
        c, r = make(opener=op).weather_view('aa')
        self.assertEqual(c, 200); self.assertTrue(r['ok'])
        self.assertEqual(r['summary']['now']['condition'], 'sunny')
        self.assertEqual(seen['url'], 'http://x:8799/summary?format=json')
        self.assertEqual(seen['auth'], 'Bearer sekrit-token')
        self.assertLessEqual(seen['t'], 10)

    def test_off_without_token(self):
        def op(*a, **k): raise AssertionError('must not call')
        c, r = make(token='', opener=op).weather_view('aa')
        self.assertFalse(r['ok']); self.assertIn('not set up', r['text'])

    def test_refused_and_unreachable_are_plain(self):
        def refuse(req, timeout=None): raise urllib.error.HTTPError(req.full_url, 401, 'no', {}, None)
        a = make(opener=refuse); c, r = a.weather_view('aa')
        self.assertFalse(r['ok']); self.assertIn('refused', r['text'])
        def down(req, timeout=None): raise OSError('sekrit-token connection refused')
        a = make(opener=down); c, r = a.weather_view('aa')
        self.assertFalse(r['ok']); self.assertIn('could not reach', r['text'])
        self.assertFalse(any('sekrit-token' in l for l in a.logs))

    def test_bad_json(self):
        c, r = make(opener=lambda req, timeout=None: Resp(b'not json')).weather_view('aa')
        self.assertFalse(r['ok'])


if __name__ == '__main__':
    unittest.main()
