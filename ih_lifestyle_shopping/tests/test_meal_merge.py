import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import meal_merge


class Cooking:
    def __init__(self):
        self.data={'example':{'title':'Example meal','shopping':['Example item']}}
        self.calls=[]
    def plan(self):return {'mon':'Example meal','tue':'Eating out'}
    def recipes(self):return self.data
    def staples(self):return {'groups':{'usual':{'items':['Example staple']}}}
    def merge(self,picks,groups,have,commit):
        self.calls.append((picks,groups,have,commit))
        items=['Example item']+(['Example staple'] if groups else [])
        return {'add':[x for x in items if x.lower() not in {h.lower() for h in have}], 'skipped':[], 'ledger_written':False}


class Shopping:
    def __init__(self): self.reads=0;self.changed=False
    def items(self):
        self.reads+=1
        return {'open':[{'uid':'1','summary':'Example item'}]+([{'uid':'2','summary':'Change'}] if self.changed else []),
                'completed':[{'uid':'3','summary':'Example staple'}]}


class MealPreviewTests(unittest.TestCase):
    def setUp(self):
        self.c=Cooking();self.s=Shopping();self.m=meal_merge.MealShopping(self.c,self.s)

    def test_preview_skips_completed_when_requested_without_writes(self):
        p=self.m.preview(['usual'],True)
        self.assertEqual(p['add'],[])
        self.assertEqual(len(p['revision']),64)
        self.assertEqual(self.c.calls[0][0],['example'])
        self.assertIs(self.c.calls[0][3],False)
        self.assertEqual(self.s.reads,2)

    def test_preview_can_include_completed_items_for_readding(self):
        p=self.m.preview(['usual'],False)
        self.assertEqual(p['add'],['Example staple'])
        self.assertIs(self.c.calls[0][3],False)

    def test_changed_list_and_ambiguous_plan_refuse_preview(self):
        original=self.s.items
        def change_after_first():
            result=original()
            if self.s.reads == 1:self.s.changed=True
            return result
        self.s.items=change_after_first
        with self.assertRaises(meal_merge.MealError):self.m.preview([],False)
        self.s.changed=False;self.s.reads=0;self.s.items=original
        self.c.data['other']={'title':'Example meal','shopping':['Something else']}
        with self.assertRaises(meal_merge.MealError):self.m.preview([],False)

    def test_unknown_staple_group_does_not_call_merge(self):
        with self.assertRaises(meal_merge.MealError):self.m.preview(['missing'],False)
        self.assertEqual(self.c.calls,[])


if __name__=='__main__':unittest.main()
