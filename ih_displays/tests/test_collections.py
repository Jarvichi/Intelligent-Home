import datetime
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import collection_tasks

SOURCES=[{'entity':'sensor.one','label':'Example A'},{'entity':'sensor.two','label':'Example B'}]
CAL='calendar.example'
def sensor(*dates,state='ok'):
    return {'state':state,'attributes':{day:'event' for day in dates}}
def calendar(state='off',start=''):
    return {'state':state,'attributes':{'start_time':start}}
def make(values,hour=6,sources=SOURCES,cal=CAL):
    now=datetime.datetime(2026,11,17,hour,0,tzinfo=datetime.timezone.utc)
    return collection_tasks.Collections(sources,cal,values.get,lambda:now)

class Tests(unittest.TestCase):
    def test_today_tomorrow_groups_and_early_detail(self):
        feed=make({'sensor.one':sensor('2026-11-17'),'sensor.two':sensor('2026-11-18'),CAL:calendar()}).read()
        self.assertTrue(feed['complete']);self.assertEqual(len(feed['items']),2)
        self.assertIn('TODAY',feed['items'][0]['title']);self.assertIn('by 7am',feed['items'][0]['detail']);self.assertIn('TOMORROW',feed['items'][1]['title'])
    def test_same_day_groups_and_afternoon_reordering(self):
        values={'sensor.one':sensor('2026-11-17'),'sensor.two':sensor('2026-11-17'),CAL:calendar()}
        feed=make(values,13).read();self.assertEqual(len(feed['items']),1);self.assertIn('Collected today',feed['items'][0]['title']);self.assertIn('Example A · Example B',feed['items'][0]['title'])
        values['sensor.two']=sensor('2026-11-18');feed=make(values,13).read();self.assertIn('TOMORROW',feed['items'][0]['title']);self.assertIn('Collected today',feed['items'][1]['title'])
    def test_calendar_fallback_and_source_failure(self):
        values={'sensor.one':sensor(),'sensor.two':sensor(state='unknown'),CAL:calendar('on','2026-11-17 08:00:00')}
        feed=make(values,9).read();self.assertTrue(feed['complete']);self.assertIn('Today’s bins',feed['items'][0]['title'])
        values['sensor.one']=None;feed=make(values,9).read();self.assertFalse(feed['complete']);self.assertTrue(feed['items'])
        values['sensor.one']=sensor();values[CAL]=calendar('on','');self.assertFalse(make(values,9).read()['complete'])
    def test_dates_after_tomorrow_and_invalid_config(self):
        feed=make({'sensor.one':sensor('2026-11-20'),'sensor.two':sensor(),CAL:calendar()}).read()
        self.assertIn('Fri 20 Nov',feed['items'][0]['title']);self.assertEqual(feed['items'][0]['detail'],'In 3 days')
        self.assertFalse(make({},sources=[{'entity':'bad/path','label':'X'}]).read()['complete'])
        self.assertFalse(make({},sources=SOURCES+[SOURCES[0]]).read()['complete'])
        self.assertFalse(collection_tasks.Collections([],'',lambda e:None).read()['configured'])
    def test_missing_dates_are_not_fabricated(self):
        values={'sensor.one':sensor(),'sensor.two':sensor(),CAL:calendar()}
        feed=make(values).read();self.assertTrue(feed['complete']);self.assertEqual(feed['items'],[])
        values['sensor.one']=sensor('2026-99-99');feed=make(values).read();self.assertFalse(feed['complete'])

if __name__=='__main__':unittest.main()
