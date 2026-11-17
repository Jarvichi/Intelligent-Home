"""Read-only preview of configured urgent-alert sources."""
import datetime
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
import free_power
import robot_tasks

DAYS=('Mon','Tue','Wed','Thu','Fri','Sat','Sun')
MAX_SOURCES=20

def flag(kind,title,short=None):
    return {'kind':kind,'title':title,'short':short or title,'dismissible':kind in ('free','saving')}

def iso(value):return robot_tasks.epoch(value)

def describe(code,description):
    if code is None or code==0:return None
    if not description and code!=520:return None
    text=str(description or 'stuck')
    if re.search('water',text,re.I):return 'water tank empty'
    if re.search(r'dust ?bag',text,re.I):return 'dust bag full'
    if re.search('stuck|trapped',text,re.I):return 'stuck'
    text=re.sub(r'^[A-Za-z]+Error:\s*','',text)
    encoded=text.encode('utf-16-le')
    return encoded[:42].decode('utf-16-le',errors='ignore')+'…' if len(encoded)>44 else text.lower()

class Alerts:
    def __init__(self,enabled,smoke,co,robots,stored,saving,power,dismissed,get,clock=None,localtime=None):
        self.enabled=enabled is True;self.smoke=smoke if isinstance(smoke,list) else []
        self.co=co if isinstance(co,str) else '';self.robots=robots if isinstance(robots,list) else []
        self.stored=stored if isinstance(stored,str) else '';self.saving=saving if isinstance(saving,str) else ''
        self.power=power if isinstance(power,str) else '';self.dismissed=dismissed if isinstance(dismissed,str) else ''
        self.get=get;self.clock=clock or time.time;self.localtime=localtime or time.localtime
    def read(self):
        if not self.enabled:return {'configured':False,'complete':False,'alerts':[]}
        robot_validator=robot_tasks.Robots(self.robots,'','','','',self.get)
        complete=bool(self.smoke and self.co)
        smoke=[]
        for entity in self.smoke[:MAX_SOURCES]:
            if isinstance(entity,str) and robot_tasks.ENTITY.fullmatch(entity) and entity.startswith('binary_sensor.'):smoke.append(entity)
            else:complete=False
        if len(self.smoke)>MAX_SOURCES:complete=False
        robots=self.robots if robot_validator._valid() else []
        if not robot_validator._valid() or len({r['name'] for r in robots})!=len(robots):complete=False;robots=[]
        specs={'co':(self.co,'binary_sensor.'),'stored':(self.stored,'input_boolean.'),'saving':(self.saving,'calendar.'),'power':(self.power,'event.'),'dismissed':(self.dismissed,'input_text.')}
        sources={};invalid=set()
        for name,(entity,prefix) in specs.items():
            if entity and (not robot_tasks.ENTITY.fullmatch(entity) or not entity.startswith(prefix)):complete=False;invalid.add(name);sources[name]=''
            else:sources[name]=entity
        ids=smoke+[e for e in sources.values() if e]
        for r in robots:ids.extend(r.get(k,'') for k in ('entity','last_completed','error','last_error','battery','blade'))
        ids=list(dict.fromkeys(e for e in ids if e))
        with ThreadPoolExecutor(max_workers=12) as pool:values=dict(zip(ids,pool.map(self.get,ids)))
        now=self.clock();life=[];robot_alerts=[];energy=[]
        for entity in smoke:
            state=robot_tasks.state(values,entity)
            if state not in ('on','off'):complete=False
        if any(robot_tasks.state(values,e)=='on' for e in smoke):life.append(flag('smoke','SMOKE ALARM'))
        if sources['co']:
            state=robot_tasks.state(values,sources['co'])
            if state not in ('on','off'):complete=False
            if state=='on':life.append(flag('co','CARBON MONOXIDE','CO ALARM'))
        mower_stored='stored' in invalid
        if sources['stored'] and any(r['kind']=='mower' for r in robots):
            value=robot_tasks.state(values,sources['stored'])
            if value not in ('on','off'):complete=False;mower_stored=True
            else:mower_stored=value=='on'
        have=set()
        ordered=[r for r in robots if r['kind']=='mower']+[r for r in robots if r['kind']!='mower']
        for r in ordered:
            if r['kind']=='mower' and mower_stored:continue
            device=values.get(r['entity']);error=values.get(r.get('error','')) if r.get('error') else None
            if not isinstance(device,dict) or (r.get('error') and not isinstance(error,dict)):complete=False
            if isinstance(device,dict) and device.get('state') in ('unknown','unavailable'):complete=False
            if isinstance(error,dict) and error.get('state') in ('unknown','unavailable'):complete=False
            code=robot_tasks.number(error.get('state')) if isinstance(error,dict) else None
            code=int(code) if code is not None else None
            description=robot_tasks.attrs(values,r.get('error','')).get('description')
            reason=describe(code,description)
            if reason:
                robot_alerts.append(flag('robot',r['name']+': '+reason));have.add(r['name'])
            elif r['kind']=='mower' and code is None and isinstance(device,dict) and device.get('state')=='error':
                blade=robot_tasks.number(robot_tasks.state(values,r.get('blade','')))
                worn=blade is not None and blade<=5
                robot_alerts.append(flag('robot',r['name']+(': blades worn out' if worn else ': mower stopped'),r['name']+(': blades' if worn else ': stopped')));have.add(r['name'])
        for r in robots:
            if r['name'] in have or (r['kind']=='mower' and mower_stored):continue
            device=values.get(r['entity'])
            if not isinstance(device,dict):continue
            active=('mowing','returning') if r['kind']=='mower' else ('cleaning','returning')
            if device.get('state') in active:
                changed=iso(device.get('last_changed'))
                if changed is None:complete=False;continue
                elapsed=now-changed
                if elapsed>2.5*3600:
                    robot_alerts.append(flag('robot',r['name']+': lost? out '+str(math.floor(elapsed/3600))+'h',r['name']+': lost?'));have.add(r['name'])
        for r in robots:
            if r['name'] in have or (r['kind']=='mower' and mower_stored):continue
            device=values.get(r['entity']);last=values.get(r.get('last_error','')) if r.get('last_error') else None
            if not isinstance(device,dict) or not isinstance(last,dict):continue
            error_time=iso(last.get('state'));finish=iso(robot_tasks.state(values,r['last_completed'])) or 0
            battery=robot_tasks.number(robot_tasks.state(values,r.get('battery','')))
            stopped=device.get('state') in ('paused','idle') or (device.get('state')=='error' and battery is not None and battery<98)
            if error_time is not None and error_time>finish and now-error_time<86400 and stopped:
                minutes=math.floor((now-error_time)/60+0.5);age=str(minutes)+'m' if minutes<90 else str(math.floor(minutes/60+0.5))+'h'
                code=robot_tasks.attrs(values,r.get('last_error','')).get('code');suffix=(str(code)+' ') if code else ''
                robot_alerts.append(flag('robot',r['name']+': stopped, error '+suffix+age+' ago',r['name']+': stopped'));have.add(r['name'])
        if sources['saving']:
            value=values.get(sources['saving']);attrs=value.get('attributes') if isinstance(value,dict) and isinstance(value.get('attributes'),dict) else None
            if isinstance(value,dict) and value.get('state') in ('unknown','unavailable'):complete=False
            if attrs is None:complete=False
            else:
                start,end=iso(attrs.get('start_time')),iso(attrs.get('end_time'))
                if value.get('state')=='on' and (start is None or end is None):complete=False
                if start is not None and end is not None and start<=now<end:
                    minutes=math.ceil((end-now)/60);energy.append(flag('saving','⚡ Save energy now — '+str(minutes)+' min left','⚡ Save: '+str(minutes)+'m left'))
        if sources['power']:
            value=values.get(sources['power']);attrs=value.get('attributes') if isinstance(value,dict) and isinstance(value.get('attributes'),dict) else None
            if isinstance(value,dict) and value.get('state') in ('unknown','unavailable'):complete=False
            events=attrs.get('events') if attrs else None
            if not isinstance(events,list) or len(events)>1000:complete=False
            else:
                intervals=[]
                for event in events:
                    try:
                        if not isinstance(event,dict):raise ValueError()
                        start=free_power.parse(event.get('start')).timestamp();end=free_power.parse(event.get('end')).timestamp()
                        if end<=start:raise ValueError()
                        if end>now:intervals.append((start,end))
                    except (ValueError,TypeError,OverflowError,OSError):complete=False
                intervals.sort();merged=[]
                for start,end in intervals:
                    if merged and merged[-1][1]==start:merged[-1]=(merged[-1][0],end)
                    else:merged.append((start,end))
                if merged and merged[0][0]<=now:
                    end=time.strftime('%H:%M',self.localtime(merged[0][1]));energy.insert(0,flag('free','⚡ Free electricity NOW — until '+end,'⚡ Free until '+end))
        if 'dismissed' in invalid and energy:energy=[]
        if sources['dismissed'] and energy:
            value=values.get(sources['dismissed'])
            if not isinstance(value,dict):complete=False;energy=[]
            else:
                raw=value.get('state');dismissed=iso(raw)
                if not isinstance(raw,str) or (raw and dismissed is None):complete=False;energy=[]
                elif dismissed is not None and 0<=now-dismissed<5400:energy=[]
        return {'configured':True,'complete':complete,'alerts':energy+life+robot_alerts}
