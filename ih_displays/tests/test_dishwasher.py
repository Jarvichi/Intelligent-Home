import datetime
import io
import json
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import dishwasher

class Response(io.BytesIO):status=200
class Opener:
    def __init__(self,values):self.values=values;self.calls=[]
    def open(self,req,timeout):
        self.calls.append(req.get_method());entity=req.full_url.rsplit('/',1)[-1];value=self.values.get(entity)
        if isinstance(value,Exception):raise value
        return Response(json.dumps(value).encode())
FLAG='input_boolean.example';FINISH='input_datetime.example';NOW=2000000000
class Tests(unittest.TestCase):
    def service(self,flag,finish,now=NOW):
        opener=Opener({FLAG:flag,FINISH:finish});return dishwasher.Dishwasher(FLAG,FINISH,'token',opener,clock=lambda:now,format_time=lambda t:str(int(t))),opener
    def test_cooling_before_four_hours_and_ready_at_boundary(self):
        finish={'state':'2033-05-18 01:00:00','attributes':{'timestamp':NOW-14399}}
        service,opener=self.service({'state':'on'},finish);result=service.read()
        self.assertEqual(result['items'][0]['title'],'Dishwasher finished · cooling');self.assertEqual(result['items'][0]['detail'],'Ready to unload at '+str(NOW+1));self.assertTrue(result['complete'])
        finish['attributes']['timestamp']=NOW-14400
        self.assertEqual(service.read()['items'][0]['title'],'Dishwasher ready to unload');self.assertTrue(all(m=='GET' for m in opener.calls))
    def test_off_unconfigured_and_unavailable(self):
        service,opener=self.service({'state':'off'},OSError());self.assertEqual(service.read(),{'configured':True,'complete':True,'items':[]});self.assertEqual(len(opener.calls),1)
        self.assertFalse(dishwasher.Dishwasher('','','').read()['configured'])
        self.assertFalse(dishwasher.Dishwasher(FLAG,'','').read()['complete'])
        service,_=self.service({'state':'unavailable'},{});self.assertFalse(service.read()['complete'])
    def test_missing_finish_uses_last_changed_but_marks_incomplete(self):
        changed=datetime.datetime.fromtimestamp(NOW-100,datetime.timezone.utc).isoformat();service,_=self.service({'state':'on','last_changed':changed},OSError())
        result=service.read();self.assertFalse(result['complete']);self.assertEqual(result['items'][0]['title'],'Dishwasher finished · cooling')
        service,_=self.service({'state':'on','last_changed':'invalid'},OSError());self.assertEqual(service.read()['items'],[]);self.assertFalse(service.read()['complete'])
    def test_shared_feed_preserves_order_and_partial_failure(self):
        class Feed:
            def __init__(self,data):self.data=data
            def read(self):return self.data
        left=Feed({'configured':True,'complete':True,'items':[{'title':'Message'}]});right=Feed({'configured':True,'complete':False,'items':[{'title':'Dishwasher'}]})
        combined=dishwasher.Combined(left,right).read();self.assertFalse(combined['complete']);self.assertEqual([x['title'] for x in combined['items']],['Message','Dishwasher'])

if __name__=='__main__':unittest.main()
