"""The HTML main screen: what it draws, when it steps aside for the SVG, and how the Chromium step fails safely."""
import pathlib
import subprocess
import sys
import importlib.util
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import device_surface
import kindle_html
import kindle_render

HAVE_JINJA = importlib.util.find_spec('jinja2') is not None
CSS = (pathlib.Path(__file__).resolve().parents[1] / 'app' / 'templates' / 'kindle.css').read_text()
needs_jinja = unittest.skipUnless(HAVE_JINJA, 'jinja2 is installed in the add-on image, not on every test machine')
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

@needs_jinja
class PageTests(unittest.TestCase):
    def tearDown(self): kindle_html.configure(None)

    def test_normal_screen_has_the_original_wording_and_escapes_text(self):
        page = kindle_html.page(data(), {'configured': True, 'complete': True, 'alerts': []}, now=0)
        for text in ('Showers', '2–8° 1 mm rain', '1–10° dry', '22:00 tmrw', 'Locked', 'Idle', 'all locked', '⚡ 31.9p now (peak)', 'Today ⚡ £7.68', 'Ground', 'Outside', 'LIGHTS ON', 'TO DO'):
            self.assertIn(text, page)
        self.assertIn('✉︎ Message &lt;b&gt;row&lt;/b&gt;', page)
        self.assertIn('in 5 days', page)
        self.assertNotIn('<b>row</b>', page)
        self.assertIn('<style>', page); self.assertIn('--i:', page)   # the stylesheet is inlined; repeated items use --i

    def test_buttons_only_while_charging(self):
        off = kindle_html.page(data(), now=0)
        on = kindle_html.page(data(device={'charging': True, 'fresh': True, 'battery': 90}), now=0)
        for cls in ('btn-alloff', 'btn-menu', 'refresh'):
            self.assertNotIn('class="a pill ' + cls, off); self.assertIn(cls, on.split('</style>')[1])
        self.assertIn('⚡ Plugged in', on)
        self.assertIn('🔋 Kindle 90%', kindle_html.page(data(device={'charging': False, 'fresh': True, 'battery': 90}), now=0))

    def test_warning_banner_replaces_the_hourly_strip(self):
        alerts = {'configured': True, 'complete': True, 'alerts': [{'kind': 'smoke', 'title': 'Smoke alarm: kitchen'}]}
        body = kindle_html.page(data(), alerts, now=0).split('</style>')[1]
        self.assertIn('Smoke alarm: kitchen', body); self.assertIn('band-dash', body); self.assertNotIn('23:00', body)
        self.assertIn('23:00', kindle_html.page(data(), {'configured': True, 'complete': True, 'alerts': []}, now=0).split('</style>')[1])

    def test_button_positions_live_in_the_stylesheet_where_the_touch_zones_expect_them(self):
        for rule in ('.btn-alloff{left:252px;top:884px;width:149.5px}', '.btn-menu{left:903.3px;top:884px;width:153.7px}', '.btn-prev{left:817.8px;top:1106px;width:115.3px}',
                     '.btn-next{left:941.7px;top:1106px;width:115.3px}', '.refresh{left:1016.4px;top:36px}', '.tile{left:calc(15px + var(--i) * 264.7px);top:726px;width:247.7px;height:140px}',
                     '.rows-5{--pitch:59px;--h:50px;--font:24px}', '.dismiss{left:894.7px;top:612px;width:151.6px;height:52px'):
            self.assertIn(rule, CSS)
        page = kindle_html.page(data(device={'charging': True, 'fresh': True, 'battery': 90}, tasks=[{'title': str(i), 'display_group': 'task'} for i in range(7)]), now=0)
        self.assertIn('btn-prev', page); self.assertIn('btn-next', page)

    def test_your_own_templates_are_used_before_the_built_in_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            pathlib.Path(folder, 'main.html').write_text('{% extends "base.html" %}{% block body %}MINE {{ day }}{% endblock %}')
            pathlib.Path(folder, 'kindle.css').write_text('/* my css */body{background:#eee}')
            kindle_html.configure(folder)
            page = kindle_html.page(data(), now=0)
            self.assertIn('MINE', page); self.assertIn('my css', page)
            self.assertIn('<meta charset', page)   # base.html still comes from the built-in set
            self.assertIn('Menu', kindle_html.render('menu.html', rows=({'label': 'A', 'icon': ''},)))   # other templates still the built-in ones

    def test_a_broken_template_falls_back_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as folder:
            pathlib.Path(folder, 'main.html').write_text('{{ no_such_value }}')
            kindle_html.configure(folder)
            calls = []
            renderer = kindle_html.Renderer('chromium', lambda cmd, **kw: calls.append(cmd), lambda p: None)
            self.assertIsNone(renderer.png(data(), now=0))
            self.assertEqual(calls, [])   # never reached Chromium
            self.assertIn('no_such_value', renderer.last_error)

class SupportsTests(unittest.TestCase):
    def test_states_the_page_does_not_draw_yet_go_to_the_svg(self):
        self.assertTrue(kindle_html.supports(data(), now=0))
        self.assertFalse(kindle_html.supports(data(cooking={'running': True}), now=0))
        self.assertFalse(kindle_html.supports(data(halloween_mode='on'), now=0))
        event = {'label': 'Occasion', 'active': True, 'slot': 'occasion', 'event_today': True, 'theme': 'birthday', 'detail': 'Happy birthday'}
        self.assertFalse(kindle_html.supports(data(panels=data()['panels'] + [event]), now=0))

@needs_jinja
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

    def test_a_busy_slot_gives_up_after_the_wait_instead_of_blocking_forever(self):
        renderer, calls = self.renderer()
        renderer.queue_wait = 0.05
        renderer.busy.acquire()
        try:
            self.assertIsNone(renderer.png(data(), now=0))
        finally:
            renderer.busy.release()
        self.assertEqual(calls, [])
        self.assertEqual(renderer.png(data(), now=0), PNG)   # the slot is usable again

    def test_the_slot_is_released_after_a_failed_run(self):
        def boom(cmd, **kw): raise subprocess.TimeoutExpired(cmd, 1)
        renderer, _ = self.renderer(run=boom)
        renderer.queue_wait = 0.05
        self.assertIsNone(renderer.png(data(), now=0))
        self.assertTrue(renderer.busy.acquire(timeout=0.05))
        renderer.busy.release()

class RunGroupTests(unittest.TestCase):
    def test_a_timeout_kills_the_whole_process_group_including_children(self):
        import os, tempfile, time
        pidfile = tempfile.mktemp()
        cmd = ['sh', '-c', 'sleep 60 & echo $! > %s; wait' % pidfile]
        with self.assertRaises(subprocess.TimeoutExpired):
            kindle_html.run_group(cmd, timeout=1)
        child = int(open(pidfile).read())
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)
        os.unlink(pidfile)

    def test_a_failing_command_raises_and_a_good_one_returns(self):
        with self.assertRaises(subprocess.CalledProcessError):
            kindle_html.run_group(['sh', '-c', 'exit 3'], timeout=5)
        self.assertEqual(kindle_html.run_group(['true'], timeout=5).returncode, 0)

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
