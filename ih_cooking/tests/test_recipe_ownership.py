import copy
import importlib.util
import json
import pathlib
import sys
import tempfile
import threading
import unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
import recipeadmin as api
spec=importlib.util.spec_from_file_location('recipe_owner',ROOT/'compat/recipe_owner.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class Ownership(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name);self.data=self.root/'data';(self.data/'recipes').mkdir(parents=True)
        self.original={'title':'Sample','steps':[{'before':0,'text':'Serve','short':'Serve'}]}
        self.file=self.data/'recipes/sample.json';self.file.write_text(json.dumps(self.original))
        self.calls=[]
        def client(method,path,body=None):
            self.calls.append((method,path))
            if method=='GET' and path=='/recipe-snapshot':return api.recipe_snapshot(str(self.data))
            if method=='GET' and path=='/recipes':return api.listing(str(self.data))
            if method=='GET':
                recipe=json.loads((self.data/'recipes'/(path.split('/')[-1]+'.json')).read_text());recipe['_revision']=api.revision(recipe);return recipe
            if method=='POST' and path=='/recipes':
                ok,result=api.create_recipe(str(self.data),body)
                if not ok:raise ValueError('Canonical create failed')
                return {'slug':result}
            ok,result=api.save_recipe(str(self.data),path.split('/')[-1],body)
            if not ok:raise ValueError('Canonical edit rejected')
            return {'ok':True}
        self.client=client
        self.owner=module.RecipeOwner(client,self.root/'cache',self.root/'www',self.root/'snapshots')
    def test_import_conflict_preflights_whole_batch(self):
        incoming=copy.deepcopy(self.original);incoming['title']='Stale host copy'
        result=api.do_import(str(self.data),{'overwrite':True,'recipes':{'sample':incoming,'new':self.original}})
        self.assertEqual(result['conflicts'],['sample']);self.assertEqual(json.loads(self.file.read_text()),self.original);self.assertFalse((self.data/'recipes/new.json').exists())
    def test_explicit_current_revision_import_keeps_backup(self):
        changed=dict(self.original,title='Intentional update')
        result=api.do_import(str(self.data),{'overwrite':True,'expected_revisions':{'sample':api.revision(self.original)},'recipes':{'sample':changed}})
        self.assertEqual(result['recipes'],1);backups=list((self.data/'recipe_backups').glob('*.json'));self.assertEqual(len(backups),1);self.assertEqual(json.loads(backups[0].read_text()),self.original)
    def test_identical_old_mirror_is_noop(self):
        result=api.do_import(str(self.data),{'overwrite':True,'recipes':{'sample':self.original}})
        self.assertEqual(result['unchanged'],1);self.assertEqual(result['conflicts'],[]);self.assertFalse((self.data/'recipe_backups').exists())
    def test_same_revision_race_only_one_write_wins(self):
        barrier=threading.Barrier(2);out=[];revision=api.revision(self.original)
        def save(title):
            barrier.wait();out.append(api.save_recipe(str(self.data),'sample',{'title':title,'_revision':revision})[0])
        threads=[threading.Thread(target=save,args=(title,)) for title in ('First','Second')]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(sorted(out),[False,True]);self.assertEqual(len(list((self.data/'recipe_backups').glob('*.json'))),1)
    def test_invalid_import_does_not_write_valid_neighbor(self):
        result=api.do_import(str(self.data),{'recipes':{'new':self.original,'bad':{'title':'Missing steps'}}})
        self.assertTrue(result['errors']);self.assertFalse((self.data/'recipes/new.json').exists())
    def test_adapter_canonical_save_and_cache_snapshot(self):
        self.owner.cache.mkdir();(self.owner.cache/'sample.json').write_text(json.dumps({'title':'Preserve old host copy'}))
        payload=self.owner.get('sample');payload.update(id='sample',title='Canonical update')
        self.assertEqual(self.owner.save(payload),'sample');self.assertEqual(json.loads(self.file.read_text())['title'],'Canonical update')
        cached=json.loads((self.owner.cache/'sample.json').read_text());self.assertTrue(cached['_revision']);self.assertEqual(cached['steps'][0]['_original_index'],0)
        old=list((self.root/'snapshots').glob('*/host/sample.json'));self.assertEqual(len(old),1);self.assertEqual(json.loads(old[0].read_text())['title'],'Preserve old host copy')
    def test_adapter_stale_save_fails_without_local_fallback(self):
        payload=self.owner.get('sample');payload.update(id='sample',title='Stale update')
        api.save_recipe(str(self.data),'sample',{'title':'Newer canonical edit'})
        with self.assertRaises(ValueError):self.owner.save(payload)
        self.assertEqual(json.loads(self.file.read_text())['title'],'Newer canonical edit');self.assertFalse(self.owner.cache.exists())
    def test_adapter_outage_does_not_touch_local_data(self):
        self.owner.cache.mkdir();p=self.owner.cache/'sample.json';p.write_text('{"keep":true}')
        self.owner.client=lambda *args: (_ for _ in ()).throw(OSError('unavailable'))
        with self.assertRaises(OSError):self.owner.refresh()
        self.assertEqual(p.read_text(),'{"keep":true}');self.assertFalse(self.owner.www.exists())

if __name__=='__main__':unittest.main()
