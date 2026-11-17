"""Read-only home health and display health tasks from configured entities."""
import math
import time
from concurrent.futures import ThreadPoolExecutor
import robot_tasks

FIELDS={'dns':'binary_sensor.','smoke':'binary_sensor.','backup':'sensor.','forecast':'sensor.','forecast_fallback':'sensor.','disk':'sensor.','temperature':'sensor.','offline':'sensor.','display_seen':'sensor.','display_battery':'sensor.'}

def row(title,detail='',urgent=False):return {'title':title,'detail':detail,'priority':'normal','urgent':urgent}

class Health:
    def __init__(self,entities,display_name,get,clock=None,localtime=None):
        self.entities=entities if isinstance(entities,dict) else {}
        self.name=display_name if isinstance(display_name,str) else ''
        self.get=get;self.clock=clock or time.time;self.localtime=localtime or time.localtime
    def read(self):
        active={k:v for k,v in self.entities.items() if v}
        if not active:return {'configured':False,'complete':True,'items':[]}
        if not self.name.strip() or len(self.name)>80 or any(ord(c)<32 for c in self.name):return {'configured':True,'complete':False,'items':[]}
        if any(k not in FIELDS or not isinstance(v,str) or not robot_tasks.ENTITY.fullmatch(v) or not v.startswith(FIELDS[k]) for k,v in active.items()):return {'configured':True,'complete':False,'items':[]}
        ids=list(dict.fromkeys(active.values()))
        with ThreadPoolExecutor(max_workers=10) as pool:values=dict(zip(ids,pool.map(self.get,ids)))
        now=self.clock();hour=self.localtime(now).tm_hour;complete=True;items=[]
        def get_state(key):
            nonlocal complete
            entity=active.get(key)
            if not entity:return None
            v=values.get(entity)
            if not isinstance(v,dict) or not isinstance(v.get('state'),str) or v['state'] in ('unknown','unavailable'):
                complete=False;return None
            return v
        for key,title,detail in [('dns','🛡 Pi-hole is down · house DNS off','Check the Pi-hole Pi'),('smoke','📴 First floor smoke alarm is offline','Check its battery')]:
            v=get_state(key)
            if v and v['state']=='on':items.append(row(title,detail,True))
            elif v and v['state'] not in ('on','off'):complete=False
        backup=get_state('backup')
        if backup:
            t=robot_tasks.epoch(backup['state'])
            if t is None:complete=False
            elif now-t>3*86400:items.append(row('💾 No backup for '+str(math.floor((now-t)/86400))+' days','Check Settings > System > Backups',True))
        if active.get('forecast') or active.get('forecast_fallback'):
            selected=values.get(active.get('forecast')) or values.get(active.get('forecast_fallback'))
            if hour<9 or hour>=12:
                daily=selected.get('attributes',{}).get('daily') if isinstance(selected,dict) and isinstance(selected.get('attributes'),dict) else None
                temperatures=[]
                if isinstance(daily,list):
                    slots=([0] if hour<9 else ([1,0] if hour>=18 else [1]))
                    for i in slots:
                        if i<len(daily) and isinstance(daily[i],dict):
                            value=daily[i].get('templow')
                            if type(value) in (int,float) and math.isfinite(value):temperatures.append(value)
                if not temperatures:complete=False
                elif min(temperatures)<=2:items.append(row('❄ Frost '+('this morning' if hour<9 else 'tonight')+' · '+str(math.floor(min(temperatures)+0.5))+'°'))
        problems=[]
        for key,threshold,comparison,label in [('disk',10,'lt','disk almost full'),('temperature',80,'gt','Pi too hot'),('offline',15,'ge','devices offline')]:
            v=get_state(key)
            if not v:continue
            n=robot_tasks.number(v['state'])
            if n is None:complete=False;continue
            if (comparison=='lt' and n<threshold) or (comparison=='gt' and n>threshold) or (comparison=='ge' and n>=threshold):
                problems.append((str(int(n))+' '+label) if key=='offline' else label)
        if problems:items.append(row('⚠ Home Assistant · '+problems[0],' · '.join(problems[1:]),True))
        seen=get_state('display_seen');battery=get_state('display_battery')
        if ('display_seen' in active or 'display_battery' in active) and (not seen or not battery):complete=False
        if seen and battery:
            t=robot_tasks.epoch(seen['state'])
            if t is None:complete=False
            else:
                attributes=battery.get('attributes',{}) if battery and isinstance(battery.get('attributes'),dict) else {}
                charging=attributes.get('charging') is True or attributes.get('charging')=='True'
                last_hour=self.localtime(t).tm_hour;threshold=360 if charging else (75 if last_hour>=23 or last_hour<6 else 30)
                minutes=math.floor((now-t)/60+0.5)
                if minutes>threshold:items.append(row('📴 '+self.name.strip()+' hasn’t checked in · '+(str(minutes)+' min' if minutes<120 else str(math.floor(minutes/60+0.5))+' h')))
                if battery:
                    level=robot_tasks.number(battery['state'])
                    if level is None:complete=False
                    else:
                        level=int(level)
                        if now-t<threshold*60 and level<=25 and not charging:items.append(row('🔋 Charge the '+self.name.strip()+' · '+str(level)+'%',urgent=level<=10))
        return {'configured':True,'complete':complete,'items':items}
