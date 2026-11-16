import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import layouts
import list_bridge
import main
import service
from test_list_bridge import FakeHA


class ImportTests(unittest.TestCase):
    def test_validated_atomic_import_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)
            (path/'stores.json').write_text(json.dumps({'default':'a','stores':[{'id':'a','name':'Example','order':['Other']}]}))
            (path/'aisles.json').write_text(json.dumps({'order':['Other'],'match':[],'rank':{}}))
            db=layouts.connect(':memory:')
            app=service.App(db,list_bridge.ShoppingList(FakeHA(),'todo.fixture'))
            server=HTTPServer(('127.0.0.1',0),main.make_handler(app,'fixture'))
            threading.Thread(target=server.serve_forever,daemon=True).start()
            try:
                args=[sys.executable,str(ROOT/'tools/import_layouts.py'),'--stores',str(path/'stores.json'),'--aisles',str(path/'aisles.json')]
                dry=subprocess.run(args,capture_output=True,text=True,timeout=5)
                self.assertEqual(dry.returncode,0)
                self.assertIsNone(layouts.read(db)[0])
                env=dict(os.environ,SHOPPING_API_TOKEN='fixture')
                live=subprocess.run(args+['--apply','--url','http://127.0.0.1:%d'%server.server_port],env=env,capture_output=True,text=True,timeout=5)
                self.assertEqual(live.returncode,0)
                before=layouts.read(db)
                second=subprocess.run(args+['--apply','--url','http://127.0.0.1:%d'%server.server_port],env=env,capture_output=True,text=True,timeout=5)
                self.assertEqual(second.returncode,1)
                self.assertEqual(layouts.read(db),before)
                self.assertNotIn('Example',live.stdout+second.stderr)
            finally:
                server.shutdown();server.server_close();db.close()


if __name__ == '__main__': unittest.main()
