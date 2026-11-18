import io
import json
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
from cooking_client import CookingClient

class Response(io.BytesIO):status=200
class Opener:
    def __init__(self,value):self.value=value;self.requests=[]
    def open(self,request,timeout):
        self.requests.append((request.full_url,request.get_method(),request.get_header('Authorization')))
        if isinstance(self.value,Exception):raise self.value
        return Response(json.dumps(self.value).encode())

class Tests(unittest.TestCase):
    def test_cooking_status_is_read_only_and_handles_inactive_and_failure(self):
        opener=Opener({'running':True,'title':'Example dinner','eat':'18:30'});client=CookingClient('http://cooking.example','example-token',opener)
        result=client.read();self.assertTrue(result['available']);self.assertEqual(result['label'],'Example dinner');self.assertIn('18:30',result['detail'])
        self.assertEqual(opener.requests,[('http://cooking.example/cook','GET','Bearer example-token')]);self.assertNotIn('example-token',str(result))
        opener.value={'running':False};self.assertFalse(client.read()['active'])
        for invalid in ({'running':'yes'},{'running':True,'title':None},OSError()):
            opener.value=invalid;self.assertFalse(client.read()['available'])
    def test_rejects_credentials_and_query_in_url(self):
        opener=Opener({'running':False})
        for url in ('file:///etc/passwd','http://name:password'+'@'+'example.test','http://example.test/?x=1'):
            self.assertFalse(CookingClient(url,'token',opener).read()['available'])
        self.assertEqual(opener.requests,[])

if __name__=='__main__':unittest.main()
