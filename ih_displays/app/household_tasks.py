"""Read-only household reminders projected into the shared display feed."""
import datetime
import re
import time
from concurrent.futures import ThreadPoolExecutor
import battery_tasks
import free_power
import robot_tasks

PREFIX={'booking':'input_text.','wheel':'input_text.','oven':'input_boolean.','event':'event.','tomorrow':'sensor.'}
DAYS=('Mon','Tue','Wed','Thu','Fri','Sat','Sun')

def valid(entity,prefix):return isinstance(entity,str) and bool(robot_tasks.ENTITY.fullmatch(entity)) and entity.startswith(prefix)
def row(title,detail=''):return {'title':title,'detail':detail,'priority':'normal'}
def same_day(a,b):return (a.tm_year,a.tm_yday)==(b.tm_year,b.tm_yday)

class EnergyReminders:
    def __init__(self,booking,wheel,oven,event,get,clock=None,localtime=None,provider='Energy'):
        self.ids={'booking':booking or '','wheel':wheel or '','oven':oven or '','event':event or ''}
        self.get=get;self.clock=clock or time.time;self.localtime=localtime or time.localtime
        self.provider=provider
    def read(self):
        active={k:v for k,v in self.ids.items() if v}
        if not any(k in active for k in ('booking','wheel','oven')):return {'configured':False,'complete':True,'items':[]}
        if any(not valid(v,PREFIX[k]) for k,v in active.items()) or not isinstance(self.provider,str) or not self.provider.strip() or len(self.provider)>40 or any(ord(c)<32 for c in self.provider):return {'configured':True,'complete':False,'items':[]}
        keys=[k for k in ('booking','wheel','oven') if k in active]
        with ThreadPoolExecutor(max_workers=3) as pool:values=dict(zip(keys,pool.map(self.get,[active[k] for k in keys])))
        now=self.clock();items=[];complete=True
        def get_state(key):
            nonlocal complete
            if key not in active:return None
            value=values.get(key)
            if not isinstance(value,dict) or not isinstance(value.get('state'),str) or value['state'] in ('unknown','unavailable'):
                complete=False;return None
            return value
        booking=get_state('booking')
        if booking and booking['state'].startswith('failed'):
            changed=robot_tasks.epoch(booking.get('last_changed'))
            if changed is None:complete=False
            elif now-changed<3*86400:items.append(row('⚡ Free-hour booking failed','Book in the '+self.provider.strip()+' app'))
        wheel=get_state('wheel')
        if wheel and wheel['state'].startswith('spun'):
            changed=robot_tasks.epoch(wheel.get('last_changed'))
            if changed is None:complete=False
            elif same_day(self.localtime(changed),self.localtime(now)):
                match=re.match(r'^\s*([+-]?\d+)',wheel['state'][5:]);count=int(match.group(1)) if match else 0
                if count>0:items.append(row('🎡 Spun the '+self.provider.strip()+' wheel ×'+str(count),'Prizes in the '+self.provider.strip()+' app'))
        oven=get_state('oven')
        if oven and oven['state'] not in ('on','off'):complete=False
        if oven and oven['state']=='on':
            earliest=None
            if 'event' in active:
                event=self.get(active['event'])
                if not isinstance(event,dict) or event.get('state') in ('unknown','unavailable') or not isinstance(event.get('attributes'),dict) or not isinstance(event['attributes'].get('events'),list):complete=False
                else:
                    for entry in event['attributes']['events']:
                        try:
                            if not isinstance(entry,dict):raise ValueError()
                            start,end=free_power.parse(entry.get('start')).timestamp(),free_power.parse(entry.get('end')).timestamp()
                            if end<=start:raise ValueError()
                            if end>now and (earliest is None or start<earliest):earliest=start
                        except (ValueError,TypeError,OverflowError,OSError):complete=False
            detail='Run it in the next free hour'
            if earliest is not None:
                local=self.localtime(earliest);day='today' if same_day(local,self.localtime(now)) else DAYS[local.tm_wday]
                detail='Run it in the free hour · '+day+' '+time.strftime('%H:%M',local)
            items.append(row('🔥 Oven needs a clean',detail))
        return {'configured':True,'complete':complete,'items':items}

class Tomorrow:
    def __init__(self,entity,get,clock=None,localtime=None):
        self.entity=entity or '';self.get=get;self.clock=clock or time.time;self.localtime=localtime or time.localtime
    def read(self):
        if not self.entity:return {'configured':False,'complete':True,'items':[]}
        if not valid(self.entity,PREFIX['tomorrow']):return {'configured':True,'complete':False,'items':[]}
        if self.localtime(self.clock()).tm_hour<18:return {'configured':True,'complete':True,'items':[]}
        value=self.get(self.entity)
        if not isinstance(value,dict) or not isinstance(value.get('state'),str) or value['state'] in ('unknown','unavailable'):return {'configured':True,'complete':False,'items':[]}
        first,separator,event=value['state'].partition('|')
        if not separator or not first:return {'configured':True,'complete':True,'items':[]}
        title='📅 Tomorrow '+('' if first=='All day' else first+' · ')+event
        return {'configured':True,'complete':True,'items':[row(battery_tasks.trim_title(title))]}
