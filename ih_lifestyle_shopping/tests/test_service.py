import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import layouts
import list_bridge
import service
from test_list_bridge import FakeHA


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.db=layouts.connect(':memory:');self.addCleanup(self.db.close)
        self.ha=FakeHA();self.bridge=list_bridge.ShoppingList(self.ha, 'todo.fixture')
        self.app=service.App(self.db,self.bridge)
        self.doc={'stores':{'default':'a','stores':[{'id':'a','name':'Example','order':['Other']}]},
                  'aisles':{'match':[],'order':['Other'],'rank':{}}}

    def test_list_writes_disabled_by_default(self):
        self.assertEqual(self.app.route('GET','/list')[0],200)
        self.assertEqual(self.app.route('POST','/list/add',{'name':'Example'})[0],403)
        self.assertEqual(self.ha.calls,[])

    def test_layout_import_and_stale_update(self):
        status, result=self.app.route('POST','/layouts/import',{'layouts':self.doc})
        self.assertEqual(status,200)
        self.assertEqual(self.app.route('POST','/layouts/import',{'layouts':self.doc})[0],409)
        self.assertEqual(self.app.route('PUT','/layouts',{'layouts':self.doc,'revision':'stale'})[0],409)
        self.assertEqual(self.app.route('PUT','/layouts',{'layouts':self.doc,'revision':result['revision']})[0],200)

    def test_write_requires_explicit_enable(self):
        self.app.list_writes_enabled=True
        self.assertEqual(self.app.route('POST','/list/add',{'name':'Example'})[0],200)
        self.assertEqual(self.ha.calls[0][1],'add_item')


if __name__ == '__main__': unittest.main()
