import json, pathlib, sys, tempfile, unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import recipeadmin as api

class RecipeFields(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.data=self.tmp.name
        self.recipe={'title':'Sample meal','steps':[{'before':20,'text':'Original instruction','short':'Warm up','oven':{'temp':180},'note':'Keep this'}],
                     'equipment':['Tray'],api.RK+'note':'Original note','shopping':['Ingredient']}
        folder=pathlib.Path(self.data)/'recipes';folder.mkdir();self.path=folder/'sample.json';self.path.write_text(json.dumps(self.recipe))
    def read(self):return json.loads(self.path.read_text())
    def test_edit_wording_preserves_metadata_and_changes_label_fields(self):
        patch={'steps':[{'before':25,'text':'New wording','short':'New label','_original_index':0}],
               'equipment':['Bowl'],api.RK+'note':'Updated note'}
        self.assertEqual(api.save_recipe(self.data,'sample',patch),(True,[]))
        out=self.read();self.assertEqual(out['steps'][0]['oven'],{'temp':180});self.assertEqual(out['steps'][0]['note'],'Keep this')
        self.assertEqual(out['steps'][0]['short'],'New label');self.assertNotIn('_original_index',out['steps'][0]);self.assertEqual(out['equipment'],['Bowl']);self.assertEqual(out[api.RK+'note'],'Updated note')
        api.save_recipe(self.data,'sample',patch);self.assertEqual(len(list((pathlib.Path(self.data)/'recipe_backups').glob('*.json'))),2)
    def test_legacy_partial_edit_keeps_unsubmitted_fields(self):
        self.assertTrue(api.save_recipe(self.data,'sample',{'title':'Changed'})[0]);self.assertEqual(self.read()['steps'],self.recipe['steps']);self.assertEqual(self.read()['equipment'],['Tray'])
        self.assertTrue(api.save_recipe(self.data,'sample',{'steps':[{'before':30,'text':'Original instruction'}]})[0]);self.assertEqual(self.read()['steps'][0]['short'],'Warm up')
    def test_bad_indices_or_values_do_not_mutate_or_make_backup(self):
        for patch in ({'steps':[{'before':2,'text':'x','_original_index':4}]},
                      {'steps':[{'before':2,'text':'x','_original_index':0},{'before':3,'text':'y','_original_index':0}]},
                      {'steps':[{'before':2,'text':'x','short':'x'*35}]},{api.RK+'note':3},{'equipment':'not a list'}):
            self.assertFalse(api.save_recipe(self.data,'sample',patch)[0]);self.assertEqual(self.read(),self.recipe)
        self.assertFalse((pathlib.Path(self.data)/'recipe_backups').exists())
    def test_create_preserves_accepted_long_fields_and_rejects_invalid_input(self):
        recipe={'title':'Long recipe','steps':[{'before':1,'text':'x'*500,'short':'Full label'}],'equipment':['e'*200],api.RK+'note':'n'*500}
        ok,slug=api.create_recipe(self.data,recipe);self.assertTrue(ok)
        with open(pathlib.Path(self.data)/'recipes'/(slug+'.json')) as f:out=json.load(f)
        self.assertEqual(out['steps'][0]['text'],'x'*500);self.assertEqual(out['equipment'],['e'*200]);self.assertEqual(out[api.RK+'note'],'n'*500)
        for bad in ({'title':'Missing steps'}, {'steps':[{'before':0,'text':'x'}]}, {'title':'Bad','steps':[{'before':'bad','text':'x'}]}):self.assertFalse(api.create_recipe(self.data,bad)[0])
    def test_reordered_steps_keep_correct_original_metadata(self):
        original=self.read();original['steps'].append({'before':0,'text':'Serve','short':'Serve','anchor':True});self.path.write_text(json.dumps(original))
        patch={'steps':[{'before':0,'text':'Serve meal','_original_index':1},{'before':20,'text':'Heat meal','_original_index':0}]}
        self.assertTrue(api.save_recipe(self.data,'sample',patch)[0]);out=self.read()['steps'];self.assertTrue(out[0]['anchor']);self.assertEqual(out[1]['oven'],{'temp':180})

    def test_new_duplicate_text_does_not_inherit_oven_and_stale_reference_rejects(self):
        patch={'steps':[{'before':5,'text':'Original instruction','short':'New','_new_step':True}]}
        self.assertTrue(api.save_recipe(self.data,'sample',patch)[0]);self.assertNotIn('oven',self.read()['steps'][0])
        original=self.read();patch={'steps':[{'before':5,'text':'Changed','_original_index':0,'_original_text':'Outdated wording'}]}
        self.assertFalse(api.save_recipe(self.data,'sample',patch)[0]);self.assertEqual(self.read(),original)

if __name__=='__main__':unittest.main()
