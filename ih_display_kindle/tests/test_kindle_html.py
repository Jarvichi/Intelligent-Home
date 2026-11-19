"""The HTML main screen: what it draws, when it steps aside for the SVG, and how the Chromium step fails safely."""
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import device_surface
import kindle_html
import kindle_render

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 5000

def data(**kw):
    base = {'weather': {'current': {'condition': 'rainy', 'temperature': 7},
                        'daily': [{'label': 'Today', 'low': 8, 'high': 10},
                                  {'label': 'Tomorrow', 'low': 2, 'high': 8, 'precipitation': 1.04, 'condition': 'rainy'},
                                  {'label': 'Saturday', 'low': 1, 'high': 10, 'precipitation': 0, 'condition': 'sunny'}],
                        'hours': [{'time': '23:00', 'temperature': 8, 'precipitation': 1.0, 'condition': 'rainy'}],
                        'facts': {'wind': '16 km/h', 'bearing': 245, 'next_rain': '22:00 next day', 'sky': 'Cloud 55%'}},
            'sun': 'Sunrise 07:34',
            'panels': [{'label': 'Alarm', 'active': True, 'detail': 'Off'}, {'label': 'Doors locked', 'active': True, 'detail': ''},
                       {'label': 'Heat idle', 'active': True, 'detail': 'Hall 20.7° → 19°'},
                       {'label': 'Energy', 'active': True, 'detail': 'x', 'kindle_left': '⚡ 31.9p now (peak)', 'kindle_cost': 'Today ⚡ £7.68'},
                       {'label': 'Lights on', 'active': True, 'detail': 'Garden', 'floors': [{'floor': 'Ground', 'rooms': []}, {'floor': 'Outside', 'rooms': ['Garden']}]}],
            'tasks': [{'title': 'Message <b>row</b>', 'detail': 'In 5 days', 'display_group': 'message'}, {'title': 'Urgent', 'urgent': True, 'display_group': 'task'}, {'title': 'Third', 'display_group': 'task'}],
            'tasks_configured': True}
    base.update(kw)
    return base

class PageTests(unittest.TestCase):
    def test_normal_screen_has_the_original_wording_and_escapes_text(self):
        page = kindle_html.page(data(), {'configured': True, 'complete': True, 'alerts': []}, now=0)
        for text in ('Showers', '2–8° 1 mm rain', '1–10° dry', '22:00 tmrw', 'Locked', 'Idle', 'all locked', '⚡ 31.9p now (peak)', 'Today ⚡ £7.68', 'Ground', 'Outside', 'LIGHTS ON', 'TO DO'):
            self.assertIn(text, page)
        self.assertIn('✉︎ Message &lt;b&gt;row&lt;/b&gt;', page)
        self.assertIn('in 5 days', page)
        self.assertNotIn('<b>row</b>', page)

    def test_buttons_only_while_charging(self):
        off = kindle_html.page(data(), now=0)
        on = kindle_html.page(data(device={'charging': True, 'fresh': True, 'battery': 90}), now=0)
        for label in ('ALL OFF', 'MENU'):
            self.assertNotIn(label, off); self.assertIn(label, on)
        self.assertIn('⚡ Plugged in', on)
        self.assertIn('🔋 Kindle 90%', kindle_html.page(data(device={'charging': False, 'fresh': True, 'battery': 90}), now=0))

    def test_warning_banner_replaces_the_hourly_strip(self):
        alerts = {'configured': True, 'complete': True, 'alerts': [{'kind': 'smoke', 'title': 'Smoke alarm: kitchen'}]}
        page = kindle_html.page(data(), alerts, now=0)
        self.assertIn('Smoke alarm: kitchen', page)
        self.assertIn('dashed', page)
        self.assertNotIn('23:00', page)
        self.assertIn('23:00', kindle_html.page(data(), {'configured': True, 'complete': True, 'alerts': []}, now=0))

    def test_every_zone_button_sits_at_its_svg_position(self):
        page = kindle_html.page(data(device={'charging': True, 'fresh': True, 'battery': 90}, tasks=[{'title': str(i), 'display_group': 'task'} for i in range(7)]), now=0)
        for box in ('left:270px;top:884px;width:140px', 'left:880px;top:884px;width:144px', 'left:800px;top:1106px;width:108px', 'left:916px;top:1106px;width:108px'):
            self.assertIn(box, page)

class SupportsTests(unittest.TestCase):
    def test_states_the_page_does_not_draw_yet_go_to_the_svg(self):
        self.assertTrue(kindle_html.supports(data(), now=0))
        self.assertFalse(kindle_html.supports(data(cooking={'running': True}), now=0))
        self.assertFalse(kindle_html.supports(data(halloween_mode='on'), now=0))
        event = {'label': 'Occasion', 'active': True, 'slot': 'occasion', 'event_today': True, 'theme': 'birthday', 'detail': 'Happy birthday'}
        self.assertFalse(kindle_html.supports(data(panels=data()['panels'] + [event]), now=0))

class RendererTests(unittest.TestCase):
    def renderer(self, run=None, grey=None):
        calls = []
        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        written = []
        def fake_grey(path):
            out = path + '.grey.png'
            open(out, 'wb').write(PNG); written.append(out)
            return out
        return kindle_html.Renderer('chromium', run or fake_run, grey or fake_grey), calls

    def test_renders_with_chromium_and_returns_the_greyscale_png(self):
        renderer, calls = self.renderer()
        self.assertEqual(renderer.png(data(), now=0), PNG)
        cmd = calls[0]
        self.assertEqual(cmd[0], 'chromium')
        self.assertIn('--window-size=1072,1448', cmd)
        self.assertIn('--no-sandbox', cmd)
        self.assertTrue(any(c.startswith('--screenshot=') for c in cmd))
        self.assertTrue(cmd[-1].startswith('file://'))

    def test_unsupported_states_and_failures_return_none_so_the_svg_is_used(self):
        renderer, calls = self.renderer()
        self.assertIsNone(renderer.png(data(cooking={'running': True}), now=0))
        self.assertEqual(calls, [])
        def boom(cmd, **kw): raise subprocess.CalledProcessError(1, cmd)
        self.assertIsNone(self.renderer(run=boom)[0].png(data(), now=0))
        def timeout(cmd, **kw): raise subprocess.TimeoutExpired(cmd, 1)
        self.assertIsNone(self.renderer(run=timeout)[0].png(data(), now=0))
        self.assertIsNone(self.renderer(grey=lambda path: None)[0].png(data(), now=0))   # Pillow missing
        def tiny(path):
            out = path + '.t.png'; open(out, 'wb').write(b'not a png'); return out
        self.assertIsNone(self.renderer(grey=tiny)[0].png(data(), now=0))

class SurfaceTests(unittest.TestCase):
    def surface(self, html):
        return device_surface.DeviceSurface(lambda: data(), lambda: None, lambda *a: None, None, None, lambda svg: b'SVG:' + svg[:5], None, lambda: {}, html=html)

    def test_surface_uses_the_html_picture_then_falls_back(self):
        class Html:
            def __init__(self, value): self.value = value
            def png(self, d, a, now): return self.value
        self.assertEqual(self.surface(Html(PNG)).encode(self.surface(Html(PNG)).main_picture(data(), None, 0)), PNG)
        fallback = self.surface(Html(None))
        picture = fallback.main_picture(data(), None, 0)
        self.assertIsInstance(picture, bytes)   # the SVG picture
        self.assertTrue(picture.startswith(b'<svg'))
        self.assertTrue(fallback.encode(picture).startswith(b'SVG:'))
        plain = self.surface(None).main_picture(data(), None, 0)
        self.assertTrue(plain.startswith(b'<svg'))

if __name__ == '__main__': unittest.main()
