"""Read-only display tasks for configured free-electricity events."""
import datetime
import re

EVENT=re.compile(r'^event\.[a-z0-9_]+$')
DAYS=('Mon','Tue','Wed','Thu','Fri','Sat','Sun')
WINDOW=datetime.timedelta(hours=48)
SOON=datetime.timedelta(hours=2)

def parse(value):
    if not isinstance(value,str):raise ValueError('invalid event')
    d=datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
    return d.astimezone()

class FreePower:
    def __init__(self,entity,get,clock=None,localize=None):
        self.entity=entity if isinstance(entity,str) else ''
        self.get=get
        self.clock=clock or (lambda:datetime.datetime.now().astimezone())
        self.localize=localize or (lambda value:value.astimezone())
    def read(self):
        if not self.entity:return {'configured':False,'complete':True,'items':[]}
        if not EVENT.fullmatch(self.entity):return {'configured':True,'complete':False,'items':[]}
        data=self.get(self.entity)
        if not isinstance(data,dict) or not isinstance(data.get('state'),str) or data['state'] in ('unknown','unavailable') or not isinstance(data.get('attributes'),dict):return {'configured':True,'complete':False,'items':[]}
        events=data['attributes'].get('events')
        if not isinstance(events,list) or len(events)>1000:return {'configured':True,'complete':False,'items':[]}
        now=self.clock();valid=[];complete=True
        for event in events:
            try:
                if not isinstance(event,dict):raise ValueError('invalid event')
                start,end=parse(event.get('start')),parse(event.get('end'))
                if end<=start:raise ValueError('invalid interval')
                if end>now and start-now<WINDOW:valid.append((start,end))
            except (ValueError,TypeError,OverflowError):complete=False
        valid.sort(key=lambda x:x[0]);merged=[]
        for start,end in valid:
            if merged and merged[-1][1]==start:merged[-1]=(merged[-1][0],end)
            else:merged.append((start,end))
        rows=[];local_now=self.localize(now)
        for start,end in merged:
            local_start,local_end=self.localize(start),self.localize(end)
            live=start<=now
            if live:title='⚡ Free electricity NOW · until '+local_end.strftime('%H:%M');detail='Use it!'
            else:
                day='today' if local_start.date()==local_now.date() else DAYS[local_start.weekday()]
                title='⚡ Free electricity '+day+' '+local_start.strftime('%H:%M')+'–'+local_end.strftime('%H:%M')
                detail='Save laundry · dishwasher · oven'
            rows.append({'title':title,'detail':detail,'priority':'normal','urgent':live or start-now<SOON})
        return {'configured':True,'complete':complete,'items':rows}
