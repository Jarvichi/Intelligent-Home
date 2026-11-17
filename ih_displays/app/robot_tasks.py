"""Read-only robot status tasks from explicitly configured HA entities."""
import datetime
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor

ENTITY=re.compile(r'^[a-z][a-z0-9_]*\.[a-z0-9_]+$')
FIELDS={'name','kind','entity','last_completed','last_active','error','last_error','battery','blade'}
OPTIONAL=('last_active','error','last_error','battery','blade')
RAIN={'rainy','pouring','lightning-rainy','snowy','snowy-rainy'}

def epoch(value):
    try:
        if not isinstance(value,str):return None
        return datetime.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (ValueError,OverflowError,OSError):return None

def number(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError,OverflowError):return None

def state(values,entity):
    v=values.get(entity)
    return v.get('state') if isinstance(v,dict) else None

def attrs(values,entity):
    v=values.get(entity)
    a=v.get('attributes') if isinstance(v,dict) else None
    return a if isinstance(a,dict) else {}

class Robots:
    def __init__(self,robots,weather,forecast,last_rain,stored,get,clock=None,localtime=None,forecast_fallback=''):
        self.robots=robots if isinstance(robots,list) else []
        self.weather=weather if isinstance(weather,str) else ''
        self.forecast=forecast if isinstance(forecast,str) else ''
        self.forecast_fallback=forecast_fallback if isinstance(forecast_fallback,str) else ''
        self.last_rain=last_rain if isinstance(last_rain,str) else ''
        self.stored=stored if isinstance(stored,str) else ''
        self.get=get;self.clock=clock or time.time;self.localtime=localtime or time.localtime
    def _valid(self):
        if len(self.robots)>6:return False
        ids=[]
        for r in self.robots:
            if not isinstance(r,dict) or not set(r)<=FIELDS or not {'name','kind','entity','last_completed'}<=set(r):return False
            name,kind,device,completed=r['name'],r['kind'],r['entity'],r['last_completed']
            if not isinstance(name,str) or not name.strip() or len(name)>80 or any(ord(c)<32 for c in name):return False
            if kind not in ('vacuum','mower') or not isinstance(device,str) or not ENTITY.fullmatch(device) or not device.startswith(('vacuum.' if kind=='vacuum' else 'lawn_mower.')):return False
            if not isinstance(completed,str) or not ENTITY.fullmatch(completed) or not completed.startswith('sensor.') or device in ids:return False
            ids.append(device)
            for key in OPTIONAL:
                val=r.get(key,'')
                if not isinstance(val,str) or (val and (not ENTITY.fullmatch(val) or not val.startswith('sensor.'))):return False
        for val,domain in ((self.weather,'weather.'),(self.forecast,'sensor.'),(self.forecast_fallback,'sensor.'),(self.last_rain,'sensor.'),(self.stored,'input_boolean.')):
            if val and (not ENTITY.fullmatch(val) or not val.startswith(domain)):return False
        return True
    def _reason(self,r,values,now):
        code=number(state(values,r.get('error','')))
        desc=attrs(values,r.get('error','')).get('description')
        if code is not None and code>0 and (int(code)==520 or desc):return ('stuck · ' if int(code)==520 else '')+'error '+str(int(code))
        previous=values.get(r.get('last_error',''))
        prev_time=epoch(previous.get('state')) if isinstance(previous,dict) else None
        finished=epoch(state(values,r['last_completed'])) or 0
        prev_code=number(attrs(values,r.get('last_error','')).get('code'))
        if prev_time is not None and prev_time>finished and prev_code is not None and int(prev_code)==520:return 'stuck · error 520'
        if r['kind']=='mower':
            blade=number(state(values,r.get('blade','')))
            if blade is not None and blade<=5:return 'blades worn out'
        battery=number(state(values,r.get('battery','')))
        if battery is not None and battery<20:return 'battery low '+str(math.floor(battery+0.5))+'%'
        if r['kind']=='mower':
            best=attrs(values,self.forecast);forecast=attrs(values,self.forecast if values.get(self.forecast) is not None else self.forecast_fallback);condition=state(values,self.weather)
            hourly=best.get('hourly') if isinstance(best.get('hourly'),list) else []
            for entry in hourly:
                if not isinstance(entry,dict):continue
                when=epoch(entry.get('datetime'))
                if when is not None and when<=now<when+3600:
                    condition=entry.get('condition');break
            if isinstance(condition,str) and condition in RAIN:return 'raining now'
            daily=forecast.get('daily') if isinstance(forecast.get('daily'),list) else []
            raw_rain=daily[0].get('precipitation') if daily and isinstance(daily[0],dict) else None
            rain=number(raw_rain) if type(raw_rain) in (int,float) else None
            if rain is not None and rain>=0.5:return 'rain today · '+format(math.floor(rain*10+0.5)/10,'g')+' mm'
            ended=epoch(state(values,self.last_rain))
            if ended is not None and 0<=now-ended<12*3600:
                hours=(now-ended)/3600
                return 'wet lawn? rain '+('<1' if hours<1 else str(math.floor(hours+0.5)))+'h ago'
        return 'cause unknown'
    def read(self):
        if not self.robots:return {'configured':False,'complete':True,'items':[]}
        if not self._valid():return {'configured':True,'complete':False,'items':[]}
        ids=[]
        for r in self.robots:
            ids.extend(r.get(k,'') for k in ('entity','last_completed')+OPTIONAL)
        ids.extend((self.weather,self.forecast,self.forecast_fallback,self.last_rain,self.stored))
        ids=list(dict.fromkeys(x for x in ids if x))
        with ThreadPoolExecutor(max_workers=12) as pool:values=dict(zip(ids,pool.map(self.get,ids)))
        now=self.clock();month=self.localtime(now).tm_mon;items=[];complete=True
        for r in self.robots:
            if r['kind']=='mower' and self.stored:
                stored=state(values,self.stored)
                if stored not in ('on','off'):complete=False;continue
                if stored=='on':continue
            device=values.get(r['entity'])
            if not isinstance(device,dict) or not isinstance(device.get('state'),str):complete=False;continue
            name=r['name'].strip()
            if device['state']=='unavailable':items.append({'title':'📴 '+name+' is offline','detail':'','priority':'normal'});continue
            if r['kind']=='mower' and (month<3 or month>10):continue
            times=[epoch(state(values,r.get(k,''))) for k in ('last_completed','last_active')]
            valid=[t for t in times if t is not None]
            if not valid:complete=False;continue
            if values.get(r['last_completed']) is None:complete=False
            latest=max(valid);threshold=5*86400 if r['kind']=='mower' and month>=9 else 26*3600
            if now-latest>threshold:
                if r['kind']=='mower':
                    if any(key and values.get(key) is None for key in (self.weather,self.last_rain)):complete=False
                    if (self.forecast or self.forecast_fallback) and values.get(self.forecast) is None and values.get(self.forecast_fallback) is None:complete=False
                days=math.floor((now-latest)/86400)
                title=('✂️ ' if r['kind']=='mower' else '🧹 ')+name+(' hasn’t run for '+str(days)+' days' if days>=2 else ' hasn’t run today')
                items.append({'title':title,'detail':self._reason(r,values,now),'priority':'normal'})
        return {'configured':True,'complete':complete,'items':items}
