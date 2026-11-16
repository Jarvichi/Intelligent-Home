import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import layouts


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.db = layouts.connect(':memory:')
        self.addCleanup(self.db.close)
        self.doc = {'stores': {'default':'shop-one', 'stores':[{'id':'shop-one','name':'Example — Town','order':['Fruit','Other'],'note':'Example note'}, {'id':'shop-two','name':'Example — Village','order':['Other','Fruit']}]},
                    'aisles': {'order':['Fruit','Other'],'match':[['Fruit','apple|pear']],'rank':{'example':0.25}}}

    def test_import_preserves_all_data_and_refuses_second_import(self):
        rev = layouts.save(self.db, self.doc)
        self.assertEqual(layouts.read(self.db), (self.doc, rev))
        with self.assertRaises(layouts.LayoutConflict): layouts.save(self.db, self.doc)
        self.assertEqual(layouts.read(self.db), (self.doc, rev))

    def test_compare_and_swap_preserves_intervening_update(self):
        first = layouts.save(self.db, self.doc)
        changed = copy.deepcopy(self.doc)
        changed['stores']['stores'][0]['note'] = 'Updated'
        second = layouts.save(self.db, changed, first)
        stale = copy.deepcopy(self.doc)
        stale['stores']['stores'][0]['name'] = 'Stale edit'
        with self.assertRaises(layouts.LayoutConflict): layouts.save(self.db, stale, first)
        self.assertEqual(layouts.read(self.db), (changed, second))

    def test_bad_import_never_writes_partial_state(self):
        for mutate in ('default', 'duplicate', 'regex'):
            bad = copy.deepcopy(self.doc)
            if mutate == 'default': bad['stores']['default'] = 'missing'
            elif mutate == 'duplicate': bad['stores']['stores'][1]['id'] = 'shop-one'
            else: bad['aisles']['match'][0][1] = '['
            with self.assertRaises(layouts.LayoutError): layouts.save(self.db, bad)
            self.assertEqual(layouts.read(self.db), (None, None))


if __name__ == '__main__': unittest.main()
