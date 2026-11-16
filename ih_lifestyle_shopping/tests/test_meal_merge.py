import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import meal_merge


class Cooking:
    def __init__(self):
        self.data={'example':{'title':'Example meal','shopping':['Example item']}}
        self.calls=[];self.ready=True;self.ledger_ok=True;self.week={'mon':'Example meal','tue':'Eating out'}
    def supports_verified_ledger(self):return self.ready
    def plan(self):return dict(self.week)
    def recipes(self):return self.data
    def staples(self):return {'groups':{'usual':{'items':['Example staple']}}}
    def merge(self,picks,groups,have,commit):
        self.calls.append((picks,groups,have,commit))
        items=['Example item']+(['Example staple'] if groups else [])
        return {'add':[x for x in items if x.lower() not in {h.lower() for h in have}], 'skipped':[], 'ledger_written':self.ledger_ok if commit else False}


class Shopping:
    def __init__(self): self.reads=0;self.changed=False;self.added=[];self.fail=False
    def items(self):
        self.reads+=1
        return {'open':[{'uid':'1','summary':'Example item'}]+([{'uid':'2','summary':'Change'}] if self.changed else [])+[{'uid':'new-%s'%i,'summary':x} for i,x in enumerate(self.added)],
                'completed':[{'uid':'3','summary':'Example staple'}]}
    def add(self,name):
        if self.fail:raise TimeoutError()
        self.added.append(name)


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

    def test_apply_requires_verified_ledger_and_reviewed_revision(self):
        preview=self.m.preview(['usual'],False)
        self.c.ready=False
        with self.assertRaises(meal_merge.MealError):self.m.apply(['usual'],False,preview['revision'])
        self.assertEqual(self.s.added,[])
        self.c.ready=True
        self.s.changed=True
        with self.assertRaises(meal_merge.MealError):self.m.apply(['usual'],False,preview['revision'])
        self.assertEqual(self.s.added,[])

    def test_apply_adds_only_reviewed_items_then_records_ledger(self):
        preview=self.m.preview(['usual'],False)
        result=self.m.apply(['usual'],False,preview['revision'])
        self.assertEqual(result,{'ok':True,'added':1,'uncertain':False,'ledger_recorded':True,'reason':None})
        self.assertEqual(self.s.added,['Example staple'])
        self.assertIs(self.c.calls[-1][3],True)

    def test_unconfirmed_add_stops_without_ledger_write_or_retry(self):
        preview=self.m.preview(['usual'],False)
        self.s.fail=True
        result=self.m.apply(['usual'],False,preview['revision'])
        self.assertEqual(result['reason'],'add_unconfirmed')
        self.assertTrue(result['uncertain'])
        self.assertFalse(any(call[3] for call in self.c.calls))

    def test_plan_change_during_apply_stops_before_ledger(self):
        preview=self.m.preview(['usual'],False)
        original_add=self.s.add
        def add_and_change(name):
            original_add(name)
            self.c.week['tue']='Takeaway'
        self.s.add=add_and_change
        result=self.m.apply(['usual'],False,preview['revision'])
        self.assertEqual((result['reason'],result['added']),('plan_changed',1))
        self.assertFalse(any(call[3] for call in self.c.calls))


if __name__=='__main__':unittest.main()
