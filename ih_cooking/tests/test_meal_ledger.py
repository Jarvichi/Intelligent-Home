"""Meal-item ledger must never claim a failed or skipped write succeeded."""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import recipeadmin


class MealLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=pathlib.Path(self.temp.name);(self.data/'recipes').mkdir()
        (self.data/'recipes'/'example.json').write_text(json.dumps({'title':'Example','shopping':['Example item']}))
        (self.data/'staples.json').write_text(json.dumps({'groups':{'usual':{'name':'Usual','items':['Example staple']}}}))
        self.ledger=self.data/'meal_items.json'

    def test_preview_does_not_write_and_commit_is_truthful(self):
        preview=recipeadmin.merge(str(self.data),['example'],['usual'],[],commit=False,now=lambda:1000)
        self.assertFalse(preview['ledger_written']);self.assertFalse(self.ledger.exists())
        result=recipeadmin.merge(str(self.data),['example'],['usual'],[],commit=True,now=lambda:1000)
        self.assertTrue(result['ledger_written'])
        self.assertEqual(set(json.loads(self.ledger.read_text())),{'example item','example staple'})

    def test_write_failure_reports_false(self):
        with mock.patch.object(recipeadmin,'atomic',side_effect=OSError('fixture')):
            result=recipeadmin.merge(str(self.data),['example'],[],[],commit=True,now=lambda:1000)
        self.assertFalse(result['ledger_written']);self.assertFalse(self.ledger.exists())

    def test_invalid_ledger_is_preserved(self):
        self.ledger.write_text('{invalid')
        result=recipeadmin.merge(str(self.data),['example'],[],[],commit=True,now=lambda:1000)
        self.assertFalse(result['ledger_written']);self.assertEqual(self.ledger.read_text(),'{invalid')


if __name__=='__main__':unittest.main()
