import io
import json
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import groceries_client

class Result(io.BytesIO):
    def __init__(self, obj):super().__init__(json.dumps(obj).encode())
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

class GroceriesTests(unittest.TestCase):
    def test_returns_existing_weeks_without_inventing_missing_ones(self):
        def open_(url,timeout):
            if url.endswith('/stats/weeks'):return Result({'weeks':[{'week':'2026-01-05','total_p':1234}]})
            if url.endswith('/stats/due'):return Result({'due':[]})
            if url.endswith('/stats/prices'):return Result({'changes':[]})
            raise ValueError()
        result=groceries_client.Groceries('http://example.invalid',open_).stats()
        self.assertEqual(len(result['weeks']),1)
        self.assertEqual(result['weeks'][0]['total_p'],1234)
        self.assertEqual(result['due'],[])

if __name__=='__main__':unittest.main()
