"""Merge of duplicate checklist lines and the shelf-life hint. Fakes only: no network, no Home Assistant, placeholder item names.
run: python3 -m unittest discover -s tests"""
import datetime
import json
import os
import pathlib
import sys
import tempfile
import unittest

APP = pathlib.Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import factsview as FV
import listmerge as LM
import main as M
from features import shopping as S

LIST = 'todo.example'


class FakeHA:
    """A list with open and ticked items. Records every write."""
    dry = False

    def __init__(self, rows):
        self.rows = [{'uid': 'u%d' % n, 'summary': s, 'status': st} for n, (s, st) in enumerate(rows)]
        self.calls = []

    def get_items(self, entity, status='needs_action'):
        return [dict(r) for r in self.rows if r['status'] == status]

    def call(self, domain, service, data):
        self.calls.append((domain, service, dict(data)))
        if service == 'update_item':
            for r in self.rows:
                if r['uid'] == data['item']:
                    r['status'] = data.get('status', r['status'])

    def fire(self, event, data):
        pass

    def status_of(self):
        return [(r['summary'], r['status']) for r in self.rows]


class FakeClient:
    """Stands in for the bus client: every op succeeds and is recorded."""
    dry = False
    active = True
    last_hello = None
    rejected = {}

    def __init__(self):
        self.posts, self.edits, self.n = [], [], 0

    def post(self, ck, text, reply_to=None, tag=None):
        self.n += 1
        self.posts.append((tag, text))
        return {'ok': True, 'discord_id': 'm%d' % self.n}

    def edit(self, ck, mid, text):
        self.edits.append((mid, text))
        return {'ok': True}

    def react(self, *a, **k): return {'ok': True}
    def watch(self, *a, **k): return {'ok': True}
    def delete(self, *a, **k): return {'ok': True}
    def delete_tagged(self, *a, **k): return {'ok': True}


def module(rows, facts=None, **over):
    """A live (not shadow) module on a temporary data folder, with a fake list and a fake bus client."""
    tmp = tempfile.TemporaryDirectory()
    opts_file = os.path.join(tmp.name, 'options.json')
    o = dict({'signing_key': 'FAKE-KEY-abcdefghijklmnop', 'shadow': False, 'shopping_list_entity': LIST, 'mqtt_password': 'x'}, **over)
    if facts is not None:
        o.update(facts_url='http://facts.invalid', facts_key='FAKE-FACTS-KEY')
    with open(opts_file, 'w') as fh:
        json.dump(o, fh)
    ha = FakeHA(rows)
    m = M.Module(M.load_options(opts_file), bus=M.bus_client.FakeBus(), ha=ha, data=tmp.name, clock=lambda: 1000.0, threaded=False)
    assert m.build()
    m.client = FakeClient()
    m._tmp = tmp
    if facts is not None:
        class R:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return self.body

        def opener(req, timeout=0):
            ref = req.full_url.split('/facts/', 1)[1]
            if ref not in facts:
                raise OSError('no such fact')
            return R(json.dumps({'value': facts[ref]}).encode())
        m.facts_opener = opener
    return m, ha


def lines_of(m):
    return [ln for tag, text in m.client.posts if tag == 'checklist' for ln in text.split('\n')[1:]]


def tick(m, label):
    """Tap the number beside the line that starts with `label`."""
    for mid, lst in m.lists.items():
        for n, ln in enumerate(lst['lines']):
            if n and ln.split(' ', 1)[1].startswith(label):
                return m.tick_item(mid, ln.split(' ', 1)[0])
    raise AssertionError('no line ' + label)


class Split(unittest.TestCase):
    def test_count_forms(self):
        self.assertEqual(LM.split('Widgets x3'), {'key': 'widgets', 'name': 'Widgets', 'count': 3, 'notes': []})
        self.assertEqual(LM.split('Widgets ×3 (for the test)')['notes'], ['for the test'])
        self.assertEqual(LM.split('2 x Widgets')['count'], 2)
        self.assertEqual(LM.split('Staples: Widgets x2')['key'], 'widgets')
        self.assertIsNone(LM.split('Widgets')['count'])

    def test_weights_units_and_bare_numbers_are_left_alone(self):
        for s in ('500 g sample mince', '2 sample onions', '1 kg widgets', '', '(note only)'):
            self.assertIsNone(LM.split(s), s)


class Merge(unittest.TestCase):
    def pairs(self, *names):
        return [(n, [n]) for n in names]

    def test_counts_are_added_and_note_kept(self):
        out = LM.merge(self.pairs('Widgets x3', 'Gadget', 'Widgets x3 (for the test bowl)'))
        self.assertEqual(out, [('Widgets x6 (for the test bowl)', ['Widgets x3', 'Widgets x3 (for the test bowl)']), ('Gadget', ['Gadget'])])

    def test_no_counts_keep_one_line(self):
        out = LM.merge(self.pairs('Gadget', 'gadget (blue)', 'Meals: Gadget'))
        self.assertEqual(out, [('Gadget (blue)', ['Gadget', 'gadget (blue)', 'Meals: Gadget'])])

    def test_exact_twins_keep_both_names(self):
        self.assertEqual(LM.merge(self.pairs('Widgets x2', 'Widgets x2')), [('Widgets x4', ['Widgets x2', 'Widgets x2'])])

    def test_mixed_counted_and_uncounted_not_merged(self):
        p = self.pairs('Gadget', 'Gadget x2')
        self.assertEqual(LM.merge(p), p)

    def test_units_not_merged(self):
        p = self.pairs('500 g sample mince', '250 g sample mince', 'Sample mince')
        self.assertEqual(LM.merge(p), p)

    def test_same_note_once_and_names_never_lost(self):
        p = self.pairs('Widgets x1 (red)', 'Sprocket', 'widgets x2 (Red)', 'Widgets x4 (blue)', 'Gadget x1')
        out = LM.merge(p)
        self.assertEqual(out[0][0], 'Widgets x7 (red, blue)')
        self.assertEqual(sorted(n for _, names in out for n in names), sorted(n for n, _ in p))

    def test_battery_line_keeps_its_items(self):
        out = LM.merge(S.merge_batteries(['Batteries: 2 × AA (remote)', 'Batteries: 1 × AA (clock)', 'Gadget']))
        self.assertIn(('AA batteries × 3', ['Batteries: 2 × AA (remote)', 'Batteries: 1 × AA (clock)']), out)


class Checklist(unittest.TestCase):
    def test_off_by_default(self):
        m, ha = module([('Widgets x3', 'needs_action'), ('Widgets x3 (for the test bowl)', 'needs_action')])
        self.assertFalse(m.opts['merge_duplicates'])
        m.feature.post_checklist()
        self.assertEqual(len(lines_of(m)), 2)

    def test_one_line_and_a_tick_ticks_every_item_behind_it(self):
        m, ha = module([('Widgets x3', 'needs_action'), ('Gadget', 'needs_action'), ('Widgets x3 (for the test bowl)', 'needs_action')], merge_duplicates=True)
        m.feature.post_checklist()
        self.assertEqual(sorted(ln.split(' ', 1)[1] for ln in lines_of(m)), ['Gadget', 'Widgets x6 (for the test bowl)'])
        self.assertEqual(ha.calls, [])                                  # showing the list writes nothing
        tick(m, 'Widgets')
        self.assertEqual(ha.status_of(), [('Widgets x3', 'completed'), ('Gadget', 'needs_action'), ('Widgets x3 (for the test bowl)', 'completed')])
        self.assertTrue(all(c[1] == 'update_item' and c[2]['status'] == 'completed' and 'rename' not in c[2] for c in ha.calls))   # never renamed or removed
        self.assertIn('~~', m.client.edits[-1][1])

    def test_same_name_twins_and_an_already_ticked_twin(self):
        """The duplicate-name case that lost ticks mid-shop: two open items and one ticked item share one name."""
        m, ha = module([('Widgets x2', 'completed'), ('Widgets x2', 'needs_action'), ('Widgets x2', 'needs_action'), ('Gadget', 'completed')], merge_duplicates=True)
        m.feature.post_checklist()
        self.assertEqual([ln.split(' ', 1)[1] for ln in lines_of(m)], ['Widgets x4'])      # ticked items are not on the checklist
        tick(m, 'Widgets')
        self.assertEqual([st for _, st in ha.status_of()], ['completed'] * 4)
        self.assertEqual(sorted(c[2]['item'] for c in ha.calls), ['u1', 'u2'])             # the two OPEN uids, never the ticked twin
        tick(m, 'Widgets')                                                                 # a second tap changes nothing
        self.assertEqual(len(ha.calls), 2)

    def test_ticked_item_is_never_merged_with_an_open_one(self):
        m, ha = module([('Widgets x3', 'completed'), ('Widgets x3 (for the test bowl)', 'needs_action')], merge_duplicates=True)
        m.feature.post_checklist()
        self.assertEqual([ln.split(' ', 1)[1] for ln in lines_of(m)], ['Widgets x3 (for the test bowl)'])
        tick(m, 'Widgets')
        self.assertEqual([st for _, st in ha.status_of()], ['completed', 'completed'])
        self.assertEqual([c[2]['item'] for c in ha.calls], ['u1'])

    def test_state_file_keeps_the_items_behind_a_line(self):
        m, ha = module([('Widgets x1', 'needs_action'), ('Widgets x2', 'needs_action')], merge_duplicates=True)
        m.feature.post_checklist()
        saved = json.loads(pathlib.Path(m.state_path()).read_text())['lists']
        self.assertEqual([l['names'] for l in saved.values()], [{'1': ['Widgets x1', 'Widgets x2']}])

    def test_old_state_without_names_still_ticks(self):
        m, ha = module([('Gadget', 'needs_action')], merge_duplicates=True)
        m.lists['m9'] = {'lines': ['head', S.KEYS[0] + ' Gadget'], 't': 1000.0, 'renewed': 1000.0}
        m.tick_item('m9', S.KEYS[0])
        self.assertEqual(ha.status_of(), [('Gadget', 'completed')])

    def test_house_text_list_is_merged_too(self):
        m, ha = module([('Widgets x3', 'needs_action'), ('Widgets x1', 'needs_action')], merge_duplicates=True)
        self.assertIn('Widgets x4', m.house_reply('shop'))


class ShelfLife(unittest.TestCase):
    TODAY = datetime.date(2030, 1, 7)

    def test_find_days(self):
        days = {'sample ham': 5, 'sample fish': 1, 'bad': 'x', 'old': 400, 'flag': True}
        self.assertEqual(FV.find_days('Sample ham x2 (sliced)', days), 5)
        self.assertEqual(FV.find_days('Staples: sample fish', days), 1)
        self.assertIsNone(FV.find_days('Gadget', days))
        self.assertIsNone(FV.find_days('old', days))
        self.assertIsNone(FV.find_days('flag', days))
        self.assertEqual(FV.fmt_days(1), 'use within 1 day')

    def test_date_wins_over_days_and_only_fresh_aisles(self):
        msgs = ['%s **Meat & fish**\nA sample ham\nB sample chicken\nC sample pork' % S.MARK, '%s **Cupboard**\nA sample ham' % S.MARK]
        out, sufs = FV.decorate(msgs, S.MARK, {'Meat & fish'}, {'sample chicken': '2030-01-09'}, self.TODAY, days={'sample ham': 5, 'sample chicken': 2})
        self.assertEqual(out[0].split('\n')[1:], ['A sample ham (use within 5 days)', 'B sample chicken (Wed 9 Jan)', 'C sample pork'])
        self.assertEqual(out[1], msgs[1])
        self.assertEqual(sufs, [{'1': ' (use within 5 days)', '2': ' (Wed 9 Jan)'}, {}])

    def test_no_days_fact_is_the_old_output(self):
        msgs = ['%s **Meat & fish**\nA sample ham' % S.MARK]
        self.assertEqual(FV.decorate(msgs, S.MARK, {'Meat & fish'}, {}, self.TODAY), (msgs, [{}]))
        self.assertEqual(FV.decorate(msgs, S.MARK, {'Meat & fish'}, {}, self.TODAY, days={}), (msgs, [{}]))

    def test_checklist_hint_and_tick_through_the_module(self):
        m, ha = module([('Sample ham x1', 'needs_action'), ('Sample ham x1 (sliced)', 'needs_action'), ('Sample beans', 'needs_action')],
                       facts={'items/fresh_days': {'sample ham': 5}}, merge_duplicates=True)
        m.feature.post_checklist()
        self.assertIn('Sample ham x2 (sliced) (use within 5 days)', [ln.split(' ', 1)[1] for ln in lines_of(m)])
        tick(m, 'Sample ham')
        self.assertEqual([st for _, st in ha.status_of()], ['completed', 'completed', 'needs_action'])

    def test_hint_without_merge_ticks_the_plain_item(self):
        m, ha = module([('Sample ham', 'needs_action')], facts={'items/fresh_days': {'sample ham': 5}})
        m.feature.post_checklist()
        self.assertEqual([ln.split(' ', 1)[1] for ln in lines_of(m)], ['Sample ham (use within 5 days)'])
        tick(m, 'Sample ham')
        self.assertEqual(ha.status_of(), [('Sample ham', 'completed')])


if __name__ == '__main__':
    unittest.main()
