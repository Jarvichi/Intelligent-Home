import io
import json
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import messages

class Response(io.BytesIO):status=200
class Opener:
    def __init__(self,states):self.states=states;self.calls=[]
    def open(self,req,timeout):
        self.calls.append((req.full_url,req.get_method()));entity=req.full_url.rsplit('/',1)[-1]
        state=self.states.get(entity)
        if isinstance(state,Exception):raise state
        return Response(json.dumps({'state':state}).encode())
class Tests(unittest.TestCase):
    def test_empty_and_populated_messages(self):
        opener=Opener({'input_text.one':'Example title | Example detail','input_text.two':''})
        service=messages.Messages(['input_text.one','input_text.two'],'token',opener);result=service.read()
        self.assertEqual(result,{'configured':True,'complete':True,'items':[{'title':'Example title','detail':'Example detail','priority':'high'}]})
        self.assertTrue(all(method=='GET' for _,method in opener.calls))
    def test_missing_and_invalid_sources_mark_incomplete(self):
        opener=Opener({'input_text.one':'unknown','input_text.two':OSError()})
        result=messages.Messages(['input_text.one','input_text.two','sensor.bad'],'token',opener).read()
        self.assertFalse(result['complete']);self.assertEqual(result['items'],[]);self.assertEqual(len(opener.calls),2)
        self.assertEqual(messages.Messages([],'',opener).read(),{'configured':False,'complete':True,'items':[]})
    def test_valid_messages_remain_when_another_source_fails(self):
        opener=Opener({'input_text.one':'Example','input_text.two':OSError()})
        result=messages.Messages(['input_text.one','input_text.two'],'token',opener).read()
        self.assertFalse(result['complete']);self.assertEqual(result['items'][0]['title'],'Example')

if __name__=='__main__':unittest.main()
