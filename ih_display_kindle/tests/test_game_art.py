"""Game and music art reaches the picture even when entity_picture is a full https address (a PlayStation's is)."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import render

PNG = b'\x89PNG\r\n\x1a\n' + b'x' * 64

class Response:
    status = 200
    def __init__(self, body): self.body = body
    def read(self, n=-1): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False

class Opener:
    def __init__(self): self.urls = []
    def open(self, request, timeout=0):
        self.urls.append(request.full_url)
        return Response(PNG)

class GameArtTests(unittest.TestCase):
    def panel(self, kind, attrs, state='playing'):
        opener = Opener()
        ha = render.HA('x', opener)
        ha.request = lambda path, body=None: {'state': state, 'attributes': dict({'friendly_name': 'PlayStation 5', 'media_title': 'GTA'}, **attrs)}
        return ha.active_panel('media_player.playstation_5', kind), opener

    def test_the_local_proxy_picture_is_used_when_entity_picture_is_an_https_address(self):
        panel, opener = self.panel('game', {
            'entity_picture': 'https://image.api.playstation.com/vulcan/a.png',
            'entity_picture_local': '/api/media_player_proxy/media_player.playstation_5?token=t&cache=c'})
        self.assertTrue(panel['art'].startswith('data:image/png;base64,'))
        self.assertEqual(opener.urls, ['http://supervisor/core/api/media_player_proxy/media_player.playstation_5?token=t&cache=c'])

    def test_a_plain_proxy_entity_picture_still_works(self):
        panel, _ = self.panel('media', {'entity_picture': '/api/media_player_proxy/media_player.playstation_5?token=t'})
        self.assertIn('art', panel)

    def test_an_https_only_picture_is_still_refused_and_the_panel_has_no_art(self):
        panel, opener = self.panel('game', {'entity_picture': 'https://image.api.playstation.com/vulcan/a.png'})
        self.assertNotIn('art', panel)
        self.assertEqual(opener.urls, [])

if __name__ == '__main__':
    unittest.main()
