"""Read configured waste-collection dates for the shared display feed."""
import datetime
import re
from concurrent.futures import ThreadPoolExecutor

SENSOR=re.compile(r'^sensor\.[a-z0-9_]+$')
CALENDAR=re.compile(r'^calendar\.[a-z0-9_]+$')
DATE=re.compile(r'^\d{4}-\d{2}-\d{2}$')
DAYS=('Mon','Tue','Wed','Thu','Fri','Sat','Sun')
MONTHS=('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')

class Collections:
    def __init__(self,sources,calendar,get,clock=None):
        self.sources=sources if isinstance(sources,list) else []
        self.calendar=calendar if isinstance(calendar,str) else ''
        self.get=get
        self.clock=clock or (lambda:datetime.datetime.now().astimezone())
    def read(self):
        if not self.sources and not self.calendar:return {'configured':False,'complete':True,'items':[]}
        if len(self.sources)>12:return {'configured':True,'complete':False,'items':[]}
        ids=[]
        for row in self.sources:
            if not isinstance(row,dict) or set(row)!={'entity','label'}:return {'configured':True,'complete':False,'items':[]}
            entity,label=row['entity'],row['label']
            if not isinstance(entity,str) or not SENSOR.fullmatch(entity) or not isinstance(label,str) or not label.strip() or len(label)>80 or entity in ids:return {'configured':True,'complete':False,'items':[]}
            ids.append(entity)
        if self.calendar and not CALENDAR.fullmatch(self.calendar):return {'configured':True,'complete':False,'items':[]}
        with ThreadPoolExecutor(max_workers=13) as pool:
            values=list(pool.map(self.get,ids+([self.calendar] if self.calendar else [])))
        now=self.clock();today=now.date();groups={};complete=True
        for row,value in zip(self.sources,values):
            if not isinstance(value,dict) or not isinstance(value.get('state'),str) or value.get('state')=='unavailable' or not isinstance(value.get('attributes'),dict):complete=False;continue
            dates=[]
            for key in value['attributes']:
                if isinstance(key,str) and DATE.fullmatch(key):
                    try:dates.append(datetime.date.fromisoformat(key))
                    except ValueError:complete=False
            if dates:groups.setdefault(min(dates),[]).append(row['label'].strip())
        if self.calendar:
            cal=values[-1]
            if not isinstance(cal,dict) or not isinstance(cal.get('state'),str) or cal.get('state') in ('unknown','unavailable') or not isinstance(cal.get('attributes'),dict):complete=False
            elif cal.get('state')=='on':
                start=cal['attributes'].get('start_time')
                if not isinstance(start,str) or not DATE.fullmatch(start[:10]):complete=False
                elif start[:10]==today.isoformat() and today not in groups:groups[today]=["Today’s bins"]
        ordered=sorted(groups,key=lambda d:(d==today and now.hour>=12,d))[:2]
        items=[]
        for day in ordered:
            name=' · '.join(groups[day]);delta=(day-today).days
            if day==today and now.hour>=12:title='✓ Collected today · '+name;detail='Next dates arrive overnight'
            elif delta==0:title='🗑 TODAY · '+name;detail='Put out now — by 7am' if now.hour<7 else 'Collection this morning'
            elif delta==1:title='🗑 TOMORROW · '+name;detail='Put out tonight'
            else:title='🗑 '+DAYS[day.weekday()]+' '+str(day.day)+' '+MONTHS[day.month-1]+' · '+name;detail='In '+str(delta)+' days'
            items.append({'title':title,'detail':detail,'priority':'normal'})
        return {'configured':True,'complete':complete,'items':items}
