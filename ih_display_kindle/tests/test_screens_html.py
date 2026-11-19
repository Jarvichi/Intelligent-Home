"""The other Kindle screens as HTML pages: wording, positions that the touch zones rely on, and the fallbacks."""
import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import device_surface
import kindle_html
import screens_html as S

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 5000
MENU = {'items': [{'title': 'Pasta <bake>', 'eat': '18:30', 'lead': 45, 'tonight': True}, {'title': 'Soup', 'eat': '12:30', 'lead': 20}], 'pages': 2, 'page': 0, 'selected_eat': '12:30'}

class PageTests(unittest.TestCase):
    def test_dinner_menu(self):
        page = S.dinner(MENU)
        for text in ('What&#x27;s for dinner?', 'As soon as ready', '12:30 lunch', 'TONIGHT’S PLAN · ready from 18:30 · 45 min', 'COOK ›', '1/2', '‹ PREV', 'NEXT ›', 'BACK'):
            self.assertIn(text, page)
        self.assertIn('Pasta &lt;bake&gt;', page)
        self.assertIn('chip on', page)   # the chosen eating time
        for box in ('left:48px;top:200px;width:976px', 'left:48px;top:328px;width:976px', 'left:312px;top:1240px;width:448px'):
            self.assertIn(box, page)
        self.assertIn('Cooking menu unavailable', S.dinner(None))
        self.assertIn('No recipes available', S.dinner({'items': []}))
        self.assertIn('left:48px;top:1240px;width:976px', S.dinner({'items': []}))

    def test_home_birthdays_menu(self):
        home = S.home({'members': [{'name': 'Sam', 'home': False}, {'name': 'Alex', 'home': True, 'transition': '2000-01-01T00:00:00+00:00'}]})
        self.assertIn('Who&#x27;s home', home)
        self.assertLess(home.index('Alex'), home.index('Sam'))   # people at home first
        self.assertIn('arrived', home); self.assertIn('Out', home); self.assertIn('DONE', home)
        self.assertIn('left:140px;top:1010px;width:792px', home)
        birthdays = S.birthdays({'items': [{'name': 'Kim', 'date': '1 Jan', 'countdown': '3 days', 'today': True}]})
        self.assertIn('Birthdays', birthdays); self.assertIn('3 days', birthdays); self.assertIn('card ink', birthdays)
        self.assertIn('Birthdays unavailable', S.birthdays(None))
        menu = S.menu()
        for i, label in enumerate(S.MENU_ROWS):
            self.assertIn(label, menu); self.assertIn(f'top:{300 + i * 140}px;width:792px;height:116px', menu)

    def test_cooking_screen(self):
        cook = {'title': 'Roast', 'eat': '18:30', 'oven_target': 180, 'next': [{'at': '17:00', 'text': 'Oven on'}], 'steps': [{'at': '17:00', 'text': 'Prep', 'done': True}], 'prep': [{'text': 'Peel'}]}
        page = S.cooking(cook, {'configured': True, 'complete': True, 'alerts': [{'kind': 'smoke', 'title': 'Smoke alarm'}]})
        for text in ('Roast', 'Eating at 18:30', 'STOP COOKING', 'OVEN', '180° target', 'NEXT', '17:00 Oven on', 'DONE', 'DID IT', 'ON IT', '−5', '+5', '✓ Prep', 'PREPARATION', 'Peel', 'Smoke alarm'):
            self.assertIn(text, page)
        self.assertIn('left:596px;top:286px;width:92px', page)   # first DONE button, as in the SVG
        self.assertIn('No upcoming steps', S.cooking({'title': 'x'}))

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
