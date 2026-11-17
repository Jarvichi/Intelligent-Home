import io
import json
import pathlib
import sys
import unittest
import urllib.error

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import cooking_client


class Result(io.BytesIO):
    def __init__(self, data): super().__init__(json.dumps(data).encode())
    def __enter__(self): return self
    def __exit__(self,*args): self.close()


class CookingClientTests(unittest.TestCase):
    def setUp(self): self.calls=[]
    def open(self, req, timeout):
        self.calls.append((req.method,req.full_url,json.loads(req.data) if req.data else None,req.headers.get('Authorization')))
        if req.full_url.endswith('/capabilities'):return Result({'meal_ledger_write_verified':True})
        if req.full_url.endswith('/plan'):return Result({'plan':{'mon':'Example'}})
        if req.full_url.endswith('/shop/merge'):return Result({'add':[],'ledger_written':False})
        return Result({})
    def test_narrow_calls_and_capability(self):
        c=cooking_client.Cooking('http://example.invalid','fixture',self.open)
        self.assertTrue(c.supports_verified_ledger());self.assertEqual(c.plan(),{'mon':'Example'})
        c.merge(['one'],['usual'],['Example'],False)
        self.assertEqual(self.calls[-1][2]['commit'],False)
        self.assertTrue(all(call[3]=='Bearer fixture' for call in self.calls))
    def test_missing_or_unsupported_capability_fails_closed(self):
        c=cooking_client.Cooking('http://example.invalid','fixture',lambda *a,**k:Result({}))
        self.assertFalse(c.supports_verified_ledger())
        c=cooking_client.Cooking('','',self.open)
        self.assertFalse(c.supports_verified_ledger())


if __name__=='__main__':unittest.main()
