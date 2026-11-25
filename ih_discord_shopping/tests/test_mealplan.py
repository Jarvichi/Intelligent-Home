"""Meal plan from the cooking add-on: fakes only, placeholder names. run: python3 -m unittest discover -s tests"""
import io
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import mealplan as MP
import settings as ST
from features import shopping as S

LIST = 'todo.example'


class FakeHA:
    dry = False

    def __init__(self, rows, states):
        self.rows = [{'uid': 'u%d' % n, 'summary': s, 'status': st} for n, (s, st) in enumerate(rows)]
        self.states = states
        self.calls = []

    def state(self, e):
        return self.states.get(e)

    def get_items(self, entity, status='needs_action'):
        return [dict(r) for r in self.rows if r['status'] == status]

    def call(self, domain, service, data):
        self.calls.append((domain, service, dict(data)))
        if service == 'add_item':
            self.rows.append({'uid': 'n%d' % len(self.rows), 'summary': data['item'], 'status': 'needs_action'})


STATES = {'sensor.recipe_meta': {'state': 'x', 'attributes': {'slugs': {'Pizza night': 'pizza-night'}}},
          'input_select.meal_mon': {'state': 'Pizza night'}, 'input_select.meal_tue': {'state': 'Freestyle'},
          'input_boolean.staples_fruit': {'state': 'on'}, 'input_boolean.staples_cats': {'state': 'off'}}


class Opener:
    def __init__(self, add):
        self.add, self.seen = add, []

    def __call__(self, req, timeout=0):
        self.seen.append((req.full_url, req.headers.get('Authorization'), json.loads(req.data)))
        return io.BytesIO(json.dumps({'add': self.add}).encode())


class T(unittest.TestCase):
    def test_week(self):
        self.assertEqual(MP.week(FakeHA([], STATES)), (['pizza-night'], ['fruit']))

    def test_adds_by_name_and_sends_have_with_ticked(self):
        ha = FakeHA([('Milk', 'needs_action'), ('Bread', 'completed')], STATES)
        op = Opener(['Meals: Flour', 'Staples: Honey'])
        res = MP.add_from_cooking(ha, LIST, 'http://h:1/', 'tok', True, op)
        self.assertEqual(res['added'], 2)
        self.assertEqual([c[2]['item'] for c in ha.calls], ['Meals: Flour', 'Staples: Honey'])
        url, auth, body = op.seen[0]
        self.assertEqual((url, auth), ('http://h:1/shop/merge', 'Bearer tok'))
        self.assertEqual(sorted(body['have']), ['Bread', 'Milk'])
        self.assertEqual((body['recipes'], body['groups'], body['commit']), (['pizza-night'], ['fruit'], True))

    def test_not_quiet_ignores_ticked(self):
        ha = FakeHA([('Milk', 'needs_action'), ('Bread', 'completed')], STATES)
        op = Opener([])
        MP.add_from_cooking(ha, LIST, 'http://h:1', 'tok', False, op)
        self.assertEqual(op.seen[0][2]['have'], ['Milk'])

    def test_nothing_picked(self):
        ha = FakeHA([], {})
        self.assertIsNone(MP.add_from_cooking(ha, LIST, 'http://h:1', 'tok', True, Opener([])))

    def test_no_url_raises(self):
        with self.assertRaises(RuntimeError):
            MP.add_from_cooking(FakeHA([], STATES), LIST, '', '', True)

    def test_no_duplicate_when_twice(self):
        """Second run: the add-on (fake) now skips what is on the list, nothing is added twice."""
        ha = FakeHA([], STATES)
        MP.add_from_cooking(ha, LIST, 'http://h:1', 'tok', True, Opener(['Staples: Honey']))
        MP.add_from_cooking(ha, LIST, 'http://h:1', 'tok', True, Opener([]))
        self.assertEqual(len(ha.calls), 1)


class Ctx:
    def __init__(self, ha):
        self.ha = ha


def feature(ha, **cfg):
    f = S.Feature.__new__(S.Feature)
    f.c = Ctx(ha)
    f.cfg = ST.merge(cfg)
    f.list = LIST
    return f


class Flow(unittest.TestCase):
    def test_cooking_source_used(self):
        ha = FakeHA([], STATES)
        orig = MP.ask_cooking
        MP.ask_cooking = lambda *a, **k: {'add': ['Meals: Flour']}
        try:
            feature(ha, meal_plan_source='cooking', cooking_url='http://h:1', cooking_token='t', meal_plan_script='plan').add_meal_plan()
        finally:
            MP.ask_cooking = orig
        self.assertEqual([c[:2] for c in ha.calls], [('todo', 'add_item')])

    def test_fallback_to_script_when_unreachable(self):
        ha = FakeHA([], STATES)
        orig = MP.ask_cooking

        def boom(*a, **k):
            raise OSError('unreachable')
        MP.ask_cooking = boom
        try:
            feature(ha, meal_plan_source='cooking', cooking_url='http://h:1', cooking_token='t', meal_plan_script='plan').add_meal_plan()
        finally:
            MP.ask_cooking = orig
        self.assertEqual(ha.calls, [('script', 'plan', {'quiet': True})])

    def test_default_is_script(self):
        ha = FakeHA([], STATES)
        feature(ha, meal_plan_script='plan').add_meal_plan()
        self.assertEqual(ha.calls, [('script', 'plan', {'quiet': True})])

    def test_options_mapping(self):
        import main as M
        c = M.settings_from_options({'meal_plan_source': 'cooking', 'cooking_url': ' http://h ', 'cooking_token': 't'})
        self.assertEqual((c['meal_plan_source'], c['cooking_url']), ('cooking', 'http://h'))
        self.assertEqual(M.settings_from_options({'meal_plan_source': 'x'})['meal_plan_source'], 'script')


if __name__ == '__main__':
    unittest.main()
