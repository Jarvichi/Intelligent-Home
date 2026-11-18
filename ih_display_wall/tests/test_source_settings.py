import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import source_settings

class Tests(unittest.TestCase):
    def test_import_merges_sources_and_never_accepts_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'sources.json';store=source_settings.SourceSettings(path)
            store.save({'render_alarm':'alarm_control_panel.example','render_energy':{'current':'sensor.rate'}})
            store.save({'render_presence':[{'name':'Example','entity':'switch.example'}],'render_energy':{'gas':'sensor.cost'}})
            store.save({'render_background_path':'/local/example.jpg','render_presence':[{'name':'Example','entity':'switch.example','picture':'/local/example.png'}]})
            result=store.read();self.assertEqual(result['render_alarm'],'alarm_control_panel.example');self.assertEqual(set(result['render_energy']),{'current','gas'});self.assertEqual(os.stat(path).st_mode & 0o777,0o600)
            before=path.read_bytes()
            for invalid in ({'cooking_token':'secret'},{'render_alarm':'sensor.wrong'},{'render_lights':[{'name':'Example','entities':['http://example.test']}]},{'render_presence':[{'name':'Example','entity':'switch.ok'}]*11},{'render_birthdays_fixed':[{'name':'Example','month':2,'day':30}]},{'render_background_path':'/local/../secrets.yaml'},{'render_presence':[{'name':'Example','entity':'switch.ok','picture':'https://example.test/picture'}]}):
                with self.assertRaises(ValueError):store.save(invalid)
                self.assertEqual(path.read_bytes(),before)
            path.write_text('{invalid');self.assertEqual(store.read(),{})

if __name__=='__main__':unittest.main()
