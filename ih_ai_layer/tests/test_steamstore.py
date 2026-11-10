"""steamstore provider + art query context: fake opener, no real network."""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from providers.steamstore import SteamStore, pick, build_rows
from providers.art import art_query, clean_context, keep_accepted, prefer_portrait, make_row

ITEMS = [{'id': 1091500, 'name': 'Cyberpunk 2077'}, {'id': 5, 'name': 'Cyberpunk 2077: Phantom Liberty'}, {'id': 'x', 'name': 'bad'}]


class R:
    def __init__(s, d): s.d = d
    def __enter__(s): return s
    def __exit__(s, *a): return False
    def read(s, n=-1): return json.dumps(s.d).encode()


class T(unittest.TestCase):
    def test_pick(self):
        self.assertEqual(pick('Cyberpunk 2077 [psn]', ITEMS), (1091500, 'Cyberpunk 2077'))
        self.assertIsNone(pick('Totally Different Game', ITEMS))
        self.assertIsNone(pick('x', None))

    def test_rows(self):
        rows = build_rows('Cyberpunk 2077', ITEMS)
        self.assertEqual(rows[0]['source_url'], 'https://store.steampowered.com/app/1091500/')
        self.assertTrue(rows[0]['image_url'].endswith('/1091500/library_600x900.jpg'))
        self.assertTrue(rows[1]['image_url'].endswith('header.jpg'))

    def make(self, data, seen):
        def opener(req, timeout=None):
            seen.append(req.full_url); return R(data)
        o = SteamStore.make_opts({'name': 'steamstore', 'enabled': True, 'settings': {'shadow': False}})
        return SteamStore(o, tempfile.mkdtemp(), opener=opener)

    def test_ask_and_cache(self):
        seen = []
        p = self.make({'items': ITEMS}, seen)
        o = {'request_id': 'a', 'capability': 'game_art_search', 'text': 'Cyberpunk 2077', 'context': {'title': 'Cyberpunk 2077', 'platform': 'psn'}}
        r = p.ask(o)
        self.assertTrue(r['ok'], r)
        self.assertEqual(json.loads(r['answer'])[0]['source'], 'Steam')
        p.ask(o)
        self.assertEqual(len(seen), 1)      # second call cached

    def test_no_match_and_other_cap(self):
        p = self.make({'items': []}, [])
        r = p.ask({'request_id': 'a', 'capability': 'game_art_search', 'text': 'Nothing'})
        self.assertFalse(r['ok'])
        self.assertEqual(r['why'], 'empty')
        self.assertFalse(p.ask({'request_id': 'b', 'capability': 'web_search', 'text': 'x'})['ok'])

    def test_art_query(self):
        self.assertEqual(art_query('long sentence', {'title': 'Doom', 'platform': 'xbox'}), 'Doom xbox')
        self.assertEqual(art_query('plain', {}), 'plain')

    def test_clean_context(self):
        c = clean_context({'title': ' Doom ', 'platform': 'pc', 'domains': ['Store.Example', 'bad domain!', 5], 'evil': 'x'})
        self.assertEqual(c, {'title': 'Doom', 'platform': 'pc', 'domains': ['store.example']})
        self.assertEqual(clean_context(None), {})

    def test_keep_accepted(self):
        rows = [{'source_url': 'https://www.store.example/a'}, {'source_url': 'https://other.example/a'}, {'source_url': 'http://store.example/a'}]
        self.assertEqual(keep_accepted(rows, {'domains': ['store.example']}), rows[:1])
        self.assertEqual(keep_accepted(rows, {}), rows)

    def test_steam_filtered_out_when_not_accepted(self):
        p = self.make({'items': ITEMS}, [])
        o = {'request_id': 'a', 'capability': 'game_art_search', 'text': 'Cyberpunk 2077', 'context': {'title': 'Cyberpunk 2077', 'domains': ['store.playstation.com']}}
        self.assertEqual(p.ask(o)['why'], 'empty')

    def test_prefer_portrait(self):
        land = make_row('l', 'https://i/1', 'https://s/1', width=1920, height=1080)
        tall = make_row('t', 'https://i/2', 'https://s/2', width=600, height=900)
        near = make_row('n', 'https://i/3', 'https://s/3', width=720, height=1000)
        unk = make_row('u', 'https://i/4', 'https://s/4')
        self.assertEqual([r['title'] for r in prefer_portrait([unk, land, tall, near])], ['n', 't', 'l', 'u'])

    def test_steam_rows_have_sizes(self):
        rows = build_rows('Cyberpunk 2077', ITEMS)
        self.assertEqual((rows[0]['width'], rows[0]['height']), (600, 900))


if __name__ == '__main__':
    unittest.main()
