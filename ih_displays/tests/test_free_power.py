import datetime
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import free_power

UTC=datetime.timezone.utc
NOW=datetime.datetime(2026,11,17,12,tzinfo=UTC)
ENTITY='event.example'
def event(start,end):return {'start':start.isoformat(),'end':end.isoformat()}
def service(events,now=NOW):
    value={'state':'ready','attributes':{'events':events}}
    return free_power.FreePower(ENTITY,lambda entity:value,clock=lambda:now,localize=lambda d:d.astimezone(UTC))

class Tests(unittest.TestCase):
    def test_adjacent_intervals_are_merged_and_urgent_before_two_hours(self):
        a=NOW+datetime.timedelta(minutes=30);b=a+datetime.timedelta(hours=1);c=b+datetime.timedelta(hours=1)
        row=service([event(b,c),event(a,b)]).read()['items'];self.assertEqual(len(row),1)
        self.assertEqual(row[0]['title'],'⚡ Free electricity today 12:30–14:30');self.assertTrue(row[0]['urgent']);self.assertEqual(row[0]['priority'],'normal')
    def test_current_future_window_and_exact_boundaries(self):
        span=lambda start,end:event(NOW+datetime.timedelta(hours=start),NOW+datetime.timedelta(hours=end))
        result=service([span(-1,1),span(2,3),span(48,49),span(-3,-2)]).read();self.assertTrue(result['complete']);self.assertEqual(len(result['items']),2)
        self.assertIn('NOW',result['items'][0]['title']);self.assertTrue(result['items'][0]['urgent']);self.assertFalse(result['items'][1]['urgent'])
    def test_tomorrow_uses_weekday_and_empty_events(self):
        start=NOW+datetime.timedelta(hours=20);row=service([event(start,start+datetime.timedelta(hours=1))]).read()['items'][0]
        self.assertIn('Wed',row['title']);self.assertFalse(row['urgent'])
        self.assertEqual(service([]).read()['items'],[])
    def test_invalid_events_are_partial_and_valid_events_remain(self):
        start=NOW+datetime.timedelta(minutes=10);result=service([{'start':'bad'},event(start,start+datetime.timedelta(hours=1))]).read()
        self.assertFalse(result['complete']);self.assertEqual(len(result['items']),1)
        result=service([event(start,start)]).read();self.assertFalse(result['complete']);self.assertEqual(result['items'],[])
    def test_unavailable_and_bad_configuration(self):
        self.assertFalse(free_power.FreePower('',lambda _:None).read()['configured'])
        self.assertFalse(free_power.FreePower('bad/path',lambda _:None).read()['complete'])
        self.assertFalse(free_power.FreePower(ENTITY,lambda _:None).read()['complete'])
        self.assertFalse(free_power.FreePower(ENTITY,lambda _:{'state':'unknown','attributes':{'events':[]}}).read()['complete'])

if __name__=='__main__':unittest.main()
