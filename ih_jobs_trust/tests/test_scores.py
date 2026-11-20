"""GET /scores payload and auth: fixed fields only, no answers or notes, bearer check. No network."""
import json, os, sys, tempfile, unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import scores, trustdb

SECRET = 'sk-SECRET-123'


def res(agent, score=10):
    return {'ok': True, 'agent': agent, 'chars': 5, 'took_ms': 9, 'score': score, 'note': 'NOTE ' + SECRET, 'answer': 'ANSWER ' + SECRET}


RESULT = {'month': '2026-11', 'dry': True, 'finished': '2026-11-01T03:05:00+00:00',
          'results': {'a': res('ih_ai_layer_groq', 5), 'b': res('ih_ai_layer_groq', 10), 'c': res('ih_ai_layer_gemini', 10), 'd': res('ih_ai_layer_gemini', 10)}}


class T(unittest.TestCase):
    def test_build(self):
        out = scores.build(RESULT)
        self.assertTrue(out['ok'])
        self.assertEqual((out['month'], out['date'], out['dry']), ('2026-11', '2026-11-01', True))
        self.assertEqual([r['agent'] for r in out['rows']][0], 'gemini')
        self.assertEqual({r['agent'] for r in out['rows']}, {'gemini', 'groq'})
        self.assertNotIn(SECRET, json.dumps(out))
        for r in out['rows']:
            self.assertEqual(set(r), {'agent', 'name', 'test', 'scored', 'unscored', 'invented'})

    def test_none(self):
        self.assertEqual(scores.build(None)['rows'], [])

    def test_auth(self):
        self.assertTrue(scores.authorised('k', 'Bearer k'))
        self.assertFalse(scores.authorised('k', 'Bearer x'))
        self.assertFalse(scores.authorised('', 'Bearer '))
        self.assertFalse(scores.authorised('k', None))
        self.assertFalse(scores.authorised('k', 'k'))

    def test_payload_empty_dir_and_bad_dir(self):
        d = tempfile.mkdtemp()
        self.assertEqual(scores.payload(d)['rows'], [])

    def test_payload_from_plain_file(self):
        d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, 'trust_runs'))
        json.dump(RESULT, open(os.path.join(d, 'trust_runs', '2026-11.json'), 'w'))
        out = scores.payload(d)
        self.assertEqual(len(out['rows']), 2)


if __name__ == '__main__':
    unittest.main()


class Wired(unittest.TestCase):
    def test_scores_route_answers_over_http(self):
        """0.3.0 never switched the route on (404), 0.3.2 forgot an import (500). Go through main's own wiring and a real socket."""
        import threading, urllib.request, urllib.error, importlib
        from http.server import ThreadingHTTPServer
        import jobs_child
        orig = jobs_child.make_handler
        main = importlib.import_module('main')
        d = tempfile.mkdtemp(); main.DATA = d
        try:
            main._with_scores()
            child = type('C', (), {'opts': {'register_key': 'K1'}, 'healthy': lambda s: True, 'status': lambda s: {}})()
            srv = ThreadingHTTPServer(('127.0.0.1', 0), jobs_child.make_handler(child)); threading.Thread(target=srv.serve_forever, daemon=True).start()
            url = 'http://127.0.0.1:%d/scores' % srv.server_address[1]
            with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(urllib.request.Request(url, headers={'Authorization': 'Bearer nope'}))
            self.assertEqual(e.exception.code, 401)
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'Authorization': 'Bearer K1'})))
            self.assertIn('rows', r); srv.shutdown()
        finally:
            jobs_child.make_handler = orig
