"""The other Kindle screens as templates: wording, the positions the touch zones rely on, and the fallbacks."""
import importlib.util
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import device_surface
import kindle_html

HAVE_JINJA = importlib.util.find_spec('jinja2') is not None
needs_jinja = unittest.skipUnless(HAVE_JINJA, 'jinja2 is installed in the add-on image, not on every test machine')
CSS = (pathlib.Path(__file__).resolve().parents[1] / 'app' / 'templates' / 'kindle.css').read_text()
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 5000
MENU = {'items': [{'title': 'Pasta <bake>', 'eat': '18:30', 'lead': 45, 'tonight': True}, {'title': 'Soup', 'eat': '12:30', 'lead': 20}], 'pages': 2, 'page': 0, 'selected_eat': '12:30'}

def body(page): return page.split('</style>')[1]

@needs_jinja
class PageTests(unittest.TestCase):
    def setUp(self):
        import screens_html
        self.S = screens_html

    def test_dinner_menu(self):
        page = body(self.S.dinner(MENU))
        for text in ("What's for dinner?", 'As soon as ready', '12:30 lunch', 'TONIGHT’S PLAN · ready from 18:30 · 45 min', 'COOK ›', '1/2', '‹ PREV', 'NEXT ›', 'BACK'):
            self.assertIn(text, page)
        self.assertIn('Pasta &lt;bake&gt;', page)
        self.assertEqual(page.count('chip on'), 1)   # the chosen eating time
        self.assertIn('btn-mid', page)
        self.assertIn('dinner-row ink', page)   # tonight's plan is the inverted row
        for rule in ('.dinner-row{top:calc(200px + var(--i) * 128px)}', '.btn-mid{left:312px;width:448px}', '.btn-left{left:48px;width:240px}', '.btn-right{left:784px;width:240px}', '.btn{', 'top:1240px;height:104px'):
            self.assertIn(rule, CSS)
        self.assertIn('Cooking menu unavailable', body(self.S.dinner(None)))
        self.assertIn('No recipes available', body(self.S.dinner({'items': []})))
        self.assertIn('btn-full', body(self.S.dinner({'items': []})))

    def test_home_birthdays_menu(self):
        home = body(self.S.home({'members': [{'name': 'Sam', 'home': False}, {'name': 'Alex', 'home': True, 'transition': '2000-01-01T00:00:00+00:00'}]}))
        self.assertIn("Who's home", home)
        self.assertLess(home.index('Alex'), home.index('Sam'))   # people at home first
        for text in ('arrived', 'Out', 'DONE', 'home-name out'):
            self.assertIn(text, home)
        self.assertIn('.home-done{left:140px;top:1010px;width:792px;height:104px', CSS)
        self.assertIn('class="stripes"', self.S.home({'members': []}))
        birthdays = body(self.S.birthdays({'items': [{'name': 'Kim', 'date': '1 Jan', 'countdown': '3 days', 'today': True}]}))
        self.assertIn('Birthdays', birthdays); self.assertIn('3 days', birthdays); self.assertIn('bday-row ink', birthdays)
        self.assertIn('Birthdays unavailable', body(self.S.birthdays(None)))
        menu = body(self.S.menu())
        for label in self.S.MENU_ROWS:
            self.assertIn(label, menu)
        self.assertIn('.menu-row{left:140px;top:calc(300px + var(--i) * 140px);width:792px;height:116px', CSS)
        self.assertEqual(menu.count('menu-row'), 7)

    def test_cooking_screen(self):
        cook = {'title': 'Roast', 'eat': '18:30', 'oven_target': 180, 'next': [{'at': '17:00', 'text': 'Oven on'}], 'steps': [{'at': '17:00', 'text': 'Prep', 'done': True}], 'prep': [{'text': 'Peel'}]}
        page = body(self.S.cooking(cook, {'configured': True, 'complete': True, 'alerts': [{'kind': 'smoke', 'title': 'Smoke alarm'}]}))
        for text in ('Roast', 'Eating at 18:30', 'STOP COOKING', 'OVEN', '180° target', 'NEXT', '17:00 Oven on', 'DONE', 'DID IT', 'ON IT', '−5', '+5', '✓ Prep', 'PREPARATION', 'Peel', 'Smoke alarm'):
            self.assertIn(text, page)
        self.assertIn('--x:596px;--w:92px', page)   # first DONE button, as in the SVG
        self.assertIn('.ck-stop{left:360px;top:122px;width:232px;height:46px', CSS)
        self.assertIn('No upcoming steps', body(self.S.cooking({'title': 'x'})))

@needs_jinja
class RendererScreenTests(unittest.TestCase):
    def renderer(self, run=None):
        calls = []
        def fake_run(cmd, **kw):
            calls.append(cmd); return subprocess.CompletedProcess(cmd, 0)
        def grey(path):
            out = path + '.g.png'; open(out, 'wb').write(PNG); return out
        return kindle_html.Renderer('chromium', run or fake_run, grey), calls

    def test_each_screen_renders_and_failures_return_none(self):
        renderer, calls = self.renderer()
        for name, payload in (('dinner', MENU), ('home', {'members': []}), ('birthdays', {'items': []}), ('menu', None), ('cooking', {'title': 'x'})):
            self.assertEqual(renderer.screen(name, payload), PNG, name)
        self.assertEqual(len(calls), 5)
        self.assertIsNone(renderer.screen('nonsense', None))
        self.assertIsNone(renderer.screen('cooking', None))   # nothing to draw
        def boom(cmd, **kw): raise subprocess.CalledProcessError(1, cmd)
        self.assertIsNone(self.renderer(boom)[0].screen('menu', None))

class SurfaceScreenTests(unittest.TestCase):
    def surface(self, html):
        return device_surface.DeviceSurface(lambda: {}, lambda: None, lambda *a: None, None, None, lambda svg: b'SVG:' + svg[:5], None, lambda: {}, html=html)

    def test_popups_use_html_then_fall_back_to_svg(self):
        class Html:
            def __init__(self, value): self.value = value
            def screen(self, name, payload, alerts=None, now=None): return self.value
        self.assertEqual(self.surface(Html(PNG)).screen_picture('menu', None), PNG)
        for name, payload in (('menu', None), ('dinner', MENU), ('home', {'members': []}), ('birthdays', {'items': []})):
            fallback = self.surface(Html(None)).screen_picture(name, payload)
            self.assertTrue(fallback.startswith(b'<svg'), name)
            self.assertTrue(self.surface(None).screen_picture(name, payload).startswith(b'<svg'), name)

if __name__ == '__main__': unittest.main()
