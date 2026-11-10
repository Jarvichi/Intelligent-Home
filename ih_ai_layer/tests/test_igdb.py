"""igdb provider: fake opener, no real network."""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from providers.igdb import Igdb, build_query, build_rows, TOKEN_URL, GAMES_URL

ITEMS = [{'id': 1, 'name': 'Cyberpunk 2077', 'url': 'https://www.igdb.com/games/cyberpunk-2077', 'cover': {'image_id': 'co1', 'width': 600, 'height': 800}},
         {'id': 2, 'name': 'No Cover Game', 'url': 'https://www.igdb.com/games/x'}]


class R:
    def __init__(s, d): s.d = d
    def __enter__(s): return s
    def __exit__(s, *a): return False
    def read(s, n=-1): return json.dumps(s.d).encode()


class T(unittest.TestCase):
    def make(self, seen, clock=None):
        def opener(req, timeout=None):
            seen.append((req.full_url, dict(req.header_items())))
            return R({'access_token': 'tok', 'expires_in': 5000000} if req.full_url.startswith(TOKEN_URL) else ITEMS)
        o = Igdb.make_opts({'name': 'igdb', 'enabled': True, 'key': 'secret', 'settings': {'shadow': False, 'client_id': 'cid'}})
        return Igdb(o, tempfile.mkdtemp(), opener=opener, **({'clock': clock} if clock else {}))

    def test_query_escapes_quotes(self):
        self.assertIn('search "a b"', build_query('a"b'))

    def test_rows(self):
        rows = build_rows('Cyberpunk 2077 [psn]', ITEMS)
        self.assertEqual(rows[0]['image_url'], 'https://images.igdb.com/igdb/image/upload/t_cover_big_2x/co1.jpg')
        self.assertEqual((rows[0]['width'], rows[0]['height']), (600, 800))
        self.assertEqual(build_rows('No Cover Game', ITEMS), [])

    def test_ask_token_reused_and_cached(self):
        seen = []
        p = self.make(seen)
        o = {'request_id': 'a', 'capability': 'game_art_search', 'text': 'Cyberpunk 2077', 'context': {'title': 'Cyberpunk 2077', 'domains': ['igdb.com']}}
        r = p.ask(o)
        self.assertTrue(r['ok'], r)
        self.assertEqual(json.loads(r['answer'])[0]['source'], 'IGDB')
        p.ask(dict(o, request_id='b'))      # cached: no new calls
        self.assertEqual([u for u, _ in seen if u.startswith(TOKEN_URL)].__len__(), 1)
        self.assertEqual([u for u, _ in seen if u == GAMES_URL].__len__(), 1)
        headers = [h for u, h in seen if u == GAMES_URL][0]
        self.assertEqual(headers.get('Client-id'), 'cid')
        self.assertEqual(headers.get('Authorization'), 'Bearer tok')

    def test_not_configured_and_filtered(self):
        o = Igdb.make_opts({'name': 'igdb', 'enabled': True, 'key': '', 'settings': {'shadow': False}})
        p = Igdb(o, tempfile.mkdtemp(), opener=lambda *a, **k: None)
        self.assertEqual(p.ask({'request_id': 'a', 'capability': 'game_art_search', 'text': 'X'})['why'], 'not_configured')
        q = self.make([])
        r = q.ask({'request_id': 'a', 'capability': 'game_art_search', 'text': 'Cyberpunk 2077', 'context': {'domains': ['store.playstation.com']}})
        self.assertEqual(r['why'], 'empty')


if __name__ == '__main__':
    unittest.main()
