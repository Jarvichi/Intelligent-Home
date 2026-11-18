"""Read-only Home Assistant data and an app-owned SVG display prototype."""
import html
import http.client
import json
import re
import time
import datetime
import math
import textwrap
import urllib.error
import urllib.request
import frames
from concurrent.futures import ThreadPoolExecutor

ENTITY=re.compile(r'^[a-z][a-z0-9_]*\.[a-z0-9_]+$')
MAX_RESPONSE=1024*1024

def clean(value,limit=80):
    text=' '.join(str(value or '').split())
    text=''.join(c for c in text if 32<=ord(c)<=0x10ffff and not 0x7f<=ord(c)<0xa0 and not 0xd800<=ord(c)<=0xdfff and ord(c) not in (0xfffe,0xffff))
    if len(text)>limit:text=text[:limit-1]+'…'
    return html.escape(text,quote=True)

class HA:
    def __init__(self,token,opener=None):self.token=token;self.opener=opener or urllib.request.build_opener(frames.NoRedirect())
    def request(self,path,body=None):
        if not self.token:raise ValueError('not configured')
        raw=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request('http://supervisor/core/api/'+path,data=raw,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        with self.opener.open(req,timeout=4) as response:
            raw=response.read(MAX_RESPONSE+1)
            if response.status!=200 or len(raw)>MAX_RESPONSE:raise ValueError('unavailable')
        return json.loads(raw)
    def state(self,entity):
        if not isinstance(entity,str) or not ENTITY.fullmatch(entity):return {'label':'Invalid entity','value':'Unavailable'}
        try:
            value=self.request('states/'+entity)
            if not isinstance(value,dict):raise ValueError()
            attrs=value.get('attributes') if isinstance(value.get('attributes'),dict) else {}
            name=attrs.get('friendly_name') or entity
            state=value.get('state')
            if not isinstance(state,str) or state in ('unknown','unavailable'):state='Unavailable'
            unit=attrs.get('unit_of_measurement')
            if state!='Unavailable' and isinstance(unit,str) and unit:state+=' '+unit
            return {'label':str(name),'value':state}
        except (OSError,ValueError,urllib.error.URLError,http.client.HTTPException):return {'label':entity,'value':'Unavailable'}
    def active_panel(self,entity,kind):
        prefix={'media':'media_player.','overlay':'binary_sensor.','cooking':'sensor.'}.get(kind,'')
        if not isinstance(entity,str) or not ENTITY.fullmatch(entity) or not entity.startswith(prefix):return {'available':False}
        try:
            value=self.request('states/'+entity)
            if not isinstance(value,dict) or value.get('state') in ('unknown','unavailable') or not isinstance(value.get('state'),str):return {'available':False}
            if kind=='overlay' and value['state'] not in ('on','off'):return {'available':False}
            if kind=='cooking' and value['state'] not in ('on','off'):return {'available':False}
            attrs=value.get('attributes') if isinstance(value.get('attributes'),dict) else {}
            active=value['state']=='playing' if kind=='media' else value['state']=='on'
            label=str(attrs.get('friendly_name') or entity)
            detail=''
            if kind=='media' and active:
                title=attrs.get('media_title');artist=attrs.get('media_artist')
                detail=' · '.join(x for x in (title,artist) if isinstance(x,str) and x.strip())
            if kind=='cooking' and active:
                label=str(attrs.get('title') or 'Cooking')
                eat=attrs.get('eat');oven=attrs.get('oven');target=attrs.get('target');setting=attrs.get('set')
                def finite(x):
                    try:return math.isfinite(float(x))
                    except (ValueError,TypeError):return False
                details=[]
                if isinstance(eat,str) and eat:details.append('Eating at '+eat)
                if finite(setting) and finite(target) and float(setting)!=float(target):details.append('SET '+str(setting)+'°!')
                elif oven=='off':details.append('Oven off')
                elif finite(oven):details.append('Oven '+str(oven)+'°'+(' / '+str(target)+'°' if finite(target) else ''))
                detail=' · '.join(details)
            return {'available':True,'active':active,'label':label,'detail':detail}
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return {'available':False}
    def intrusion_panel(self,alert,log,presence):
        if not isinstance(presence,list) or not presence:return {'available':False}
        entities=[alert,log]+presence[:10]
        if len(presence)>10 or any(not isinstance(e,str) or not ENTITY.fullmatch(e) for e in entities):return {'available':False}
        if not alert.startswith('input_boolean.') or not log.startswith('input_text.') or any(not e.startswith('switch.') for e in presence):return {'available':False}
        try:
            with ThreadPoolExecutor(max_workers=12) as pool:values=list(pool.map(lambda entity:self.request('states/'+entity),entities))
            states=[x.get('state') if isinstance(x,dict) else None for x in values]
            if states[0] not in ('on','off') or any(x not in ('on','off') for x in states[2:]) or not isinstance(states[1],str) or states[1] in ('unknown','unavailable'):return {'available':False}
            rooms={'living':'Living room','hall':'Hall','kitchen':'Kitchen','utility':'Utility','landing':'Landing','top':'Top floor'}
            entries=[]
            for item in states[1].split(',')[:30]:
                fields=item.split('@')
                if len(fields)==2 and fields[0] in rooms and re.fullmatch(r'\d{2}-\d{2} \d{2}:\d{2}',fields[1]):entries.append(rooms[fields[0]]+' '+fields[1])
            return {'available':True,'active':states[0]=='on' and 'on' in states[2:] and bool(entries),'urgent':True,'label':'Motion while away','detail':' · '.join(entries[:6])}
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return {'available':False}
    def heat_panel(self,boiler,thermostat,floors):
        if not isinstance(floors,list) or len(floors)>8:return {'available':False}
        entities=[boiler,thermostat]+floors
        if any(not isinstance(e,str) or not ENTITY.fullmatch(e) for e in entities):return {'available':False}
        if not boiler.startswith('binary_sensor.') or any(not e.startswith('climate.') for e in [thermostat]+floors):return {'available':False}
        try:
            with ThreadPoolExecutor(max_workers=10) as pool:values=list(pool.map(lambda e:self.request('states/'+e),entities))
            if any(not isinstance(v,dict) or not isinstance(v.get('state'),str) or v.get('state') in ('unknown','unavailable') for v in values) or values[0].get('state') not in ('on','off'):return {'available':False}
            floor_names=[]
            for entity,value in zip(floors,values[2:]):
                attrs=value.get('attributes') if isinstance(value.get('attributes'),dict) else {}
                if attrs.get('hvac_action')=='heating':floor_names.append(str(attrs.get('friendly_name') or entity))
            heating=values[0]['state']=='on';label='Heating' if heating else 'Floors' if floor_names else 'Heat idle'
            attrs=values[1].get('attributes') if isinstance(values[1].get('attributes'),dict) else {}
            current,target=attrs.get('current_temperature'),attrs.get('temperature')
            if floor_names and not heating:detail=', '.join(floor_names)
            elif all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (current,target)):detail=f'{current:.1f}° → {round(target)}°'
            else:detail=''
            return {'available':True,'active':True,'label':label,'detail':detail}
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return {'available':False}
    def doors_panel(self,doors):
        if not isinstance(doors,list) or not doors or len(doors)>10:return {'available':False}
        for door in doors:
            if not isinstance(door,dict) or not isinstance(door.get('name'),str) or not door['name'].strip():return {'available':False}
            if any(not isinstance(door.get(k),str) or not ENTITY.fullmatch(door[k]) or not door[k].startswith(prefix) for k,prefix in (('lock','lock.'),('contact','binary_sensor.'))):return {'available':False}
        try:
            entities=[door[k] for door in doors for k in ('lock','contact')]
            with ThreadPoolExecutor(max_workers=12) as pool:values=list(pool.map(lambda entity:self.request('states/'+entity),entities))
            states=[v.get('state') if isinstance(v,dict) else None for v in values]
            if any(states[i] not in ('locked','unlocked') or states[i+1] not in ('on','off') for i in range(0,len(states),2)):return {'available':False}
            bad=[(door,states[2*i+1]=='on') for i,door in enumerate(doors) if states[2*i]!='locked' or states[2*i+1]=='on']
            label='Doors open' if any(opened for _,opened in bad) else 'Doors unlocked' if bad else 'Doors locked'
            detail=', '.join(door['name']+(' open' if opened else '') for door,opened in bad)
            return {'available':True,'active':True,'urgent':bool(bad),'label':label,'detail':detail}
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return {'available':False}
    def alarm_panel(self,entity):
        if not isinstance(entity,str) or not ENTITY.fullmatch(entity) or not entity.startswith('alarm_control_panel.'):return {'available':False}
        try:
            value=self.request('states/'+entity);state=value.get('state') if isinstance(value,dict) else None
            if not isinstance(state,str) or state in ('unknown','unavailable',''):return {'available':False}
            label='Off' if state=='disarmed' else 'Armed' if state.startswith('armed') else 'ALARM!' if state=='triggered' else state.replace('_',' ').capitalize()
            return {'available':True,'active':True,'urgent':state=='triggered','label':'Alarm','detail':label}
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return {'available':False}
    def presence_panel(self,entities):
        if not isinstance(entities,list) or not entities or len(entities)>10 or any(not isinstance(e,str) or not ENTITY.fullmatch(e) or not e.startswith('switch.') for e in entities):return {'available':False}
        try:
            with ThreadPoolExecutor(max_workers=10) as pool:values=list(pool.map(lambda e:self.request('states/'+e),entities))
            if any(not isinstance(v,dict) or v.get('state') not in ('on','off') for v in values):return {'available':False}
            people=[]
            for entity,value in zip(entities,values):
                if value['state']=='on':
                    attrs=value.get('attributes') if isinstance(value.get('attributes'),dict) else {}
                    people.append(str(attrs.get('friendly_name') or entity))
            return {'available':True,'active':True,'label':'Home','detail':', '.join(people) if people else 'Nobody home'}
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return {'available':False}
    def lights_panel(self,groups):
        if not isinstance(groups,list) or not groups or len(groups)>8:return {'available':False}
        for group in groups:
            if not isinstance(group,dict) or not isinstance(group.get('name'),str) or not group['name'].strip() or not isinstance(group.get('entities'),list) or not group['entities'] or len(group['entities'])>50:return {'available':False}
            if any(not isinstance(e,str) or not ENTITY.fullmatch(e) or not e.startswith('light.') for e in group['entities']):return {'available':False}
        entities=list(dict.fromkeys(e for group in groups for e in group['entities']))
        if len(entities)>100:return {'available':False}
        try:
            with ThreadPoolExecutor(max_workers=12) as pool:values=dict(zip(entities,pool.map(lambda e:self.request('states/'+e),entities)))
            if any(not isinstance(v,dict) or v.get('state') not in ('on','off') for v in values.values()):return {'available':False}
            active=[]
            for group in groups:
                names=[]
                for entity in group['entities']:
                    value=values[entity]
                    if value['state']=='on':
                        attrs=value.get('attributes') if isinstance(value.get('attributes'),dict) else {}
                        names.append(str(attrs.get('friendly_name') or entity))
                if names:active.append(group['name']+': '+', '.join(names))
            return {'available':True,'active':True,'label':'Lights on','detail':' · '.join(active) if active else 'All off'}
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return {'available':False}
    def energy_panel(self,sources,now=None):
        now=time.time() if now is None else now
        prefixes={'current':'sensor.','next':'sensor.','electricity':'sensor.','gas':'sensor.','calendar':'calendar.','sessions':'event.','rates_today':'event.','rates_tomorrow':'event.'}
        if not isinstance(sources,dict) or not sources.get('current'):return {'available':False}
        enabled={k:v for k,v in sources.items() if k in prefixes and v}
        if any(not isinstance(v,str) or not ENTITY.fullmatch(v) or not v.startswith(prefixes[k]) for k,v in enabled.items()):return {'available':False}
        def fetch(item):
            key,entity=item
            try:return key,self.request('states/'+entity)
            except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return key,None
        with ThreadPoolExecutor(max_workers=8) as pool:values=dict(pool.map(fetch,enabled.items()))
        missing=any(not isinstance(v,dict) or not isinstance(v.get('state'),str) or v.get('state') in ('unknown','unavailable') for v in values.values())
        def attrs(key):
            value=values.get(key);return value.get('attributes',{}) if isinstance(value,dict) and isinstance(value.get('attributes'),dict) else {}
        def number(x):
            try:
                n=float(x);return n if math.isfinite(n) else None
            except (TypeError,ValueError):return None
        def state(key):return number(values[key].get('state')) if isinstance(values.get(key),dict) else None
        def epoch(x):
            try:return datetime.datetime.fromisoformat(str(x).replace('Z','+00:00')).timestamp()
            except (ValueError,TypeError,OverflowError,OSError):return None
        start=epoch(attrs('calendar').get('start_time'));end=epoch(attrs('calendar').get('end_time'));session=None
        if start is not None and end is not None and end>now and start-now<36*3600:session=(start,end,True)
        if not session:
            events=attrs('sessions').get('available_events',[])
            if isinstance(events,list):
                for event in events[:200]:
                    if not isinstance(event,dict):continue
                    start=epoch(event.get('start'));end=epoch(event.get('end'))
                    if start is not None and end is not None and end>now and start-now<36*3600:session=(start,end,False);break
        if session:
            start,end,joined=session;start_label=datetime.datetime.fromtimestamp(start).astimezone().strftime('%a %H:%M');end_label=datetime.datetime.fromtimestamp(end).astimezone().strftime('%H:%M')
            detail=('Saving now until '+end_label) if start<=now else ('Saving '+start_label+'–'+end_label if joined else 'Saving '+start_label+' · join in app')
        else:
            current=state('current');upcoming=state('next')
            if current is None:return {'available':False}
            price=current*100;detail=f'{price:.1f}p now'+(' peak' if price>=28 else ' cheap' if price<15 else '')
            if upcoming is not None:
                detail+=f' · {upcoming*100:.1f}p next'
                rates=[]
                for key in ('rates_today','rates_tomorrow'):
                    part=attrs(key).get('rates',[])
                    if isinstance(part,list):rates.extend(part[:500])
                for rate in rates:
                    if not isinstance(rate,dict):continue
                    finish=epoch(rate.get('end'));begins=epoch(rate.get('start'));rate_value=number(rate.get('value_inc_vat'))
                    if finish is not None and begins is not None and finish>now and rate_value is not None and abs(rate_value*100-price)>0.01:
                        detail+=' from '+datetime.datetime.fromtimestamp(begins).astimezone().strftime('%H:%M');break
            for key in ('electricity','gas'):
                cost=state(key)
                if cost is not None:detail+=(' · Electricity £' if key=='electricity' else ' · Gas £')+f'{cost:.2f}'
        return {'available':not missing,'active':True,'label':'Energy','detail':detail}
    def todos(self,entity):
        if not isinstance(entity,str) or not entity.startswith('todo.') or not ENTITY.fullmatch(entity):return None
        try:
            response=self.request('services/todo/get_items?return_response',{'entity_id':entity,'status':['needs_action']})
            items=response['service_response'][entity]['items']
            if not isinstance(items,list):return None
            return [i['summary'] for i in items if isinstance(i,dict) and isinstance(i.get('summary'),str)][:5]
        except (OSError,ValueError,KeyError,TypeError,urllib.error.URLError,http.client.HTTPException):return None
    def task_feed(self,entity):
        if not isinstance(entity,str) or not ENTITY.fullmatch(entity):return None
        try:
            value=self.request('states/'+entity)
            if not isinstance(value,dict) or not isinstance(value.get('state'),str) or value.get('state') in ('unknown','unavailable'):return None
            items=value['attributes']['items']
            if not isinstance(items,list) or len(items)>500:return None
            result=[]
            for row in items:
                if not isinstance(row,dict):return None
                title,detail,priority=row.get('title'),row.get('detail',''),row.get('priority','normal')
                if not isinstance(title,str) or not title.strip() or not isinstance(detail,str) or priority not in ('high','normal','low'):return None
                result.append({'title':title,'detail':detail,'priority':priority})
            return result
        except (OSError,ValueError,KeyError,TypeError,urllib.error.URLError,http.client.HTTPException):return None
    def forecast(self,primary,fallback,now=None):
        now=time.time() if now is None else now
        for entity,source in ((primary,'primary'),(fallback,'fallback')):
            if not isinstance(entity,str) or not entity.startswith('sensor.') or not ENTITY.fullmatch(entity):continue
            try:
                value=self.request('states/'+entity)
                if not isinstance(value,dict) or value.get('state') in ('unknown','unavailable'):continue
                updated=datetime.datetime.fromisoformat(str(value.get('last_updated','')).replace('Z','+00:00'))
                if updated.tzinfo is None or not -60<=now-updated.timestamp()<=10800:continue
                attrs=value['attributes'];hourly=attrs['hourly']
                if not isinstance(hourly,list) or len(hourly)>500:continue
                hours=[]
                for row in hourly:
                    if not isinstance(row,dict):continue
                    date=datetime.datetime.fromisoformat(str(row.get('datetime','')).replace('Z','+00:00'))
                    if date.tzinfo is None:continue
                    stamp=date.timestamp();temp=row.get('temperature');condition=row.get('condition')
                    if stamp<=now-3600 or stamp>now+86400 or isinstance(temp,bool) or not isinstance(temp,(int,float)) or not math.isfinite(temp) or not isinstance(condition,str) or not condition:continue
                    hours.append((stamp,{'time':date.astimezone().strftime('%H:%M'),'temperature':round(temp),'condition':condition.replace('_',' '),'wind_speed':row.get('wind_speed'),'wind_bearing':row.get('wind_bearing'),'uv_index':row.get('uv_index'),'cloud_coverage':row.get('cloud_coverage'),'precipitation':row.get('precipitation')}))
                hours.sort(key=lambda x:x[0])
                if not hours or hours[0][0]>now+3600:continue
                current=dict(hours[0][1],current=hours[0][0]<=now)
                today=datetime.datetime.fromtimestamp(now).astimezone().date();daily=[]
                rows=attrs.get('daily',[])
                if isinstance(rows,list) and len(rows)<=30:
                    for row in rows:
                        try:
                            date=datetime.datetime.fromisoformat(str(row.get('datetime','')).replace('Z','+00:00'))
                            if date.tzinfo is None:continue
                            day=date.astimezone().date();offset=(day-today).days
                            high,low,condition=row.get('temperature'),row.get('templow'),row.get('condition')
                            if offset not in (0,1,2) or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in (high,low)) or not isinstance(condition,str) or not condition:continue
                            daily.append((offset,{'label':('Today','Tomorrow','Next day')[offset],'high':round(high),'low':round(low),'condition':condition.replace('_',' ')}))
                        except (ValueError,AttributeError,TypeError,OverflowError):continue
                daily.sort(key=lambda x:x[0])
                def numeric(x):return not isinstance(x,bool) and isinstance(x,(int,float)) and math.isfinite(x)
                def wet(row):
                    p=row.get('precipitation');return (numeric(p) and p>=0.1) or row['condition'] in ('rainy','pouring','lightning-rainy','snowy-rainy')
                rain=next(((stamp,row) for stamp,row in hours if wet(row)),None)
                next_rain=('Due now' if rain[0]<=now else rain[1]['time']) if rain else 'None in hourly forecast'
                if rain and rain[0]>now and datetime.datetime.fromtimestamp(rain[0]).astimezone().date()!=today:next_rain+=' next day'
                wind=current.get('wind_speed');bearing=current.get('wind_bearing');uv=current.get('uv_index');cloud=current.get('cloud_coverage')
                facts={'wind':str(round(wind))+' km/h' if numeric(wind) else 'Unavailable','bearing':round(bearing)%360 if numeric(bearing) else None,'next_rain':next_rain,'sky':('UV '+str(round(uv)) if numeric(uv) and uv>=0.5 else 'Cloud '+str(round(cloud))+'%' if numeric(cloud) else 'Unavailable')}
                return {'source':source,'hours':[x[1] for x in hours[:5]],'current':current,'daily':[x[1] for x in daily[:3]],'facts':facts}
            except (OSError,ValueError,KeyError,TypeError,OverflowError,urllib.error.URLError,http.client.HTTPException):continue
        return None
    def snapshot(self,entities,todo,feed='',shared=None,weather_primary='',weather_fallback='',media=None,overlays=None,cooking='',intrusion=None,heat=None,doors=None,energy=None,alarm='',presence=None,lights=None):
        entities=entities if isinstance(entities,list) else []
        entities=[x for x in entities[:6] if isinstance(x,str)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures=[pool.submit(self.state,e) for e in entities]
            tf=pool.submit(self.todos,todo) if todo else None
            ff=pool.submit(self.task_feed,feed) if feed else None
            sf=pool.submit(shared) if shared else None
            wf=pool.submit(self.forecast,weather_primary,weather_fallback) if weather_primary or weather_fallback else None
            panels=[pool.submit(self.active_panel,e,kind) for kind,values in (('media',media),('overlay',overlays)) for e in (values[:8] if isinstance(values,list) else [])]
            if cooking:panels.append(pool.submit(self.active_panel,cooking,'cooking'))
            if isinstance(intrusion,dict) and any(intrusion.values()):panels.append(pool.submit(self.intrusion_panel,intrusion.get('alert',''),intrusion.get('log',''),intrusion.get('presence',[])))
            if isinstance(heat,dict) and any(heat.values()):panels.append(pool.submit(self.heat_panel,heat.get('boiler',''),heat.get('thermostat',''),heat.get('floors',[])))
            if doors:panels.append(pool.submit(self.doors_panel,doors))
            if isinstance(energy,dict) and any(energy.values()):panels.append(pool.submit(self.energy_panel,energy))
            if alarm:panels.append(pool.submit(self.alarm_panel,alarm))
            if presence:panels.append(pool.submit(self.presence_panel,presence))
            if lights:panels.append(pool.submit(self.lights_panel,lights))
            sources=[f.result() for f in (ff,tf) if f is not None]
            shared_result=sf.result() if sf else None
            shared_partial=bool(sf and (shared_result is None or not shared_result.get('complete',False)))
            if sf:sources.insert(0,shared_result.get('items') if shared_result else None)
            configured=bool(todo or feed or shared);partial=shared_partial or any(x is None for x in sources)
            if not configured or all(x is None for x in sources): tasks=None
            else:
                tasks=[]
                for source in sources:
                    if source is not None:
                        tasks.extend(item if isinstance(item,dict) else {'title':item,'detail':'','priority':'normal'} for item in source)
                ranks={'high':0,'normal':1,'low':2}
                tasks.sort(key=lambda item:ranks[item['priority']])
            return {'items':[f.result() for f in futures], 'tasks':tasks, 'tasks_configured':configured,'tasks_partial':partial,'weather':wf.result() if wf else None,'weather_configured':bool(wf),'panels':[f.result() for f in panels]}

def svg(profile,data,alerts=None,now=None):
    if not isinstance(profile,dict) or profile.get('state')!='ready':raise ValueError('Profile unavailable')
    width,height=profile.get('width'),profile.get('height')
    if any(type(v) is not int or not 1<=v<=8192 for v in (width,height)):raise ValueError('Invalid dimensions')
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid meet">',
           '<rect width="1920" height="1080" fill="#101619"/>',
           '<text x="70" y="105" fill="#f4f7f4" font-family="sans-serif" font-size="44" font-weight="bold">HOME</text>',
           '<text x="70" y="148" fill="#b6c6bd" font-family="sans-serif" font-size="24">Wall display preview</text>']
    panels=data.get('panels') or []
    active=sorted((p for p in panels if p.get('active')),key=lambda p:not p.get('urgent'))
    items=list(data.get('items') or [])+[{'label':p.get('label',''),'value':p.get('detail') or 'Active'} for p in active if not p.get('urgent')]
    item_pages=max(1,(len(items)+5)//6);item_page=int((time.time() if now is None else now)//60)%item_pages
    for i,item in enumerate(items[item_page*6:item_page*6+6]):
        x=70+(i%2)*405;y=190+(i//2)*185
        parts.append(f'<rect x="{x}" y="{y}" width="375" height="155" rx="20" fill="#26352f"/>')
        parts.append(f'<text x="{x+24}" y="{y+45}" fill="#c4d5c9" font-family="sans-serif" font-size="22">{clean(item.get("label"),24)}</text>')
        lines=textwrap.wrap(str(item.get('value') or ''),width=25,max_lines=3,placeholder='…') or ['']
        for j,line in enumerate(lines):parts.append(f'<text x="{x+24}" y="{y+75+j*28}" fill="#f7faf7" font-family="sans-serif" font-size="22">{clean(line,25)}</text>')
    if not items:parts.append('<text x="70" y="245" fill="#c4d5c9" font-family="sans-serif" font-size="25">Choose display entities in the app configuration.</text>')
    if item_pages>1:parts.append(f'<text x="770" y="148" fill="#c4d5c9" font-family="sans-serif" font-size="18">Cards {item_page+1}/{item_pages}</text>')
    urgent=[p for p in active if p.get('urgent')]
    if urgent or any(not p.get('available') for p in panels):
        titles=[p.get('label','')+(': '+p['detail'] if p.get('detail') else '') for p in urgent]
        if any(not p.get('available') for p in panels):titles.append('Some sources unavailable')
        pages=max(1,(len(titles)+1)//2);page=int((time.time() if now is None else now)//300)%pages
        for i,title in enumerate(titles[page*2:page*2+2]):parts.append(f'<text x="390" y="{105+i*25}" fill="#ffd88c" font-family="sans-serif" font-size="19">{clean(title,85)}</text>')
        if pages>1:parts.append(f'<text x="1425" y="105" fill="#ffd88c" font-family="sans-serif" font-size="17">{page+1}/{pages}</text>')
    parts.append('<rect x="900" y="190" width="575" height="525" rx="20" fill="#26352f"/>')
    parts.append('<text x="930" y="245" fill="#f7faf7" font-family="sans-serif" font-size="30" font-weight="bold">Selected tasks</text>')
    tasks=data.get('tasks')
    if tasks is None:
        label='Task list unavailable' if data.get('tasks_configured') else 'Choose a task list in the app configuration.'
        parts.append(f'<text x="930" y="300" fill="#c4d5c9" font-family="sans-serif" font-size="22">{clean(label)}</text>')
    elif not tasks and not data.get('tasks_partial'):parts.append('<text x="930" y="300" fill="#c4d5c9" font-family="sans-serif" font-size="22">No items from selected sources</text>')
    else:
        pages=max(1,(len(tasks)+4)//5);page=int((time.time() if now is None else now)//60)%pages
        if pages>1:parts.append(f'<text x="1370" y="245" fill="#c4d5c9" font-family="sans-serif" font-size="20">{page+1}/{pages}</text>')
        for i,task in enumerate(tasks[page*5:page*5+5]):
            title=task.get('title','') if isinstance(task,dict) else task
            detail=task.get('detail','') if isinstance(task,dict) else ''
            colour='#ffd88c' if isinstance(task,dict) and task.get('urgent') is True else '#f7faf7'
            parts.append(f'<text x="930" y="{305+i*75}" fill="{colour}" font-family="sans-serif" font-size="24">• {clean(title,37)}</text>')
            if detail:parts.append(f'<text x="950" y="{331+i*75}" fill="#c4d5c9" font-family="sans-serif" font-size="18">{clean(detail,43)}</text>')
        if data.get('tasks_partial'):parts.append('<text x="930" y="658" fill="#ffd88c" font-family="sans-serif" font-size="20">Some task sources are unavailable</text>')
    parts.append('<text x="930" y="694" fill="#c4d5c9" font-family="sans-serif" font-size="18">Only selected sources are shown</text>')
    if data.get('weather_configured'):
        weather=data.get('weather')
        if weather and weather.get('facts'):
            facts=weather['facts'];wind=facts['wind']+((' · '+str(facts['bearing'])+'°') if facts.get('bearing') is not None else '')
            for x,label in ((390,'Wind '+wind),(700,'Rain '+facts['next_rain']),(1110,facts['sky'])):
                parts.append(f'<text x="{x}" y="148" fill="#c4d5c9" font-family="sans-serif" font-size="19">{clean(label,35)}</text>')
        parts.append('<rect x="70" y="730" width="1405" height="75" rx="16" fill="#26352f"/>')
        if not weather:parts.append('<text x="90" y="775" fill="#ffd88c" font-family="sans-serif" font-size="22">Hourly forecast unavailable</text>')
        else:
            current=weather.get('current');daily=weather.get('daily') or []
            label=('Now' if current and current.get('current') else 'Next') if current else 'Forecast'
            if current:label+=' '+str(current['temperature'])+'° '+current['condition']
            if weather['source']=='fallback':label='fallback · '+label
            parts.append(f'<text x="90" y="756" fill="#c4d5c9" font-family="sans-serif" font-size="18">{clean(label,28)}</text>')
            if not daily:parts.append('<text x="390" y="756" fill="#ffd88c" font-family="sans-serif" font-size="18">Daily forecast unavailable</text>')
            for i,day in enumerate(daily):
                desc=day['label']+' '+str(day['high'])+'°/'+str(day['low'])+'° '+day['condition']
                parts.append(f'<text x="{390+i*355}" y="756" fill="#c4d5c9" font-family="sans-serif" font-size="18">{clean(desc,32)}</text>')
            for i,hour in enumerate(weather['hours']):
                text=hour['time']+'  '+str(hour['temperature'])+'°  '+hour['condition']
                parts.append(f'<text x="{90+i*270}" y="790" fill="#f7faf7" font-family="sans-serif" font-size="19">{clean(text,26)}</text>')
    if alerts is None:
        parts.append('<text x="70" y="172" fill="#ffd88c" font-family="sans-serif" font-size="19">Alert preview not connected</text>')
    elif not alerts.get('configured'):
        parts.append('<text x="70" y="172" fill="#ffd88c" font-family="sans-serif" font-size="19">Alert preview not configured</text>')
    else:
        warnings=alerts.get('alerts') or []
        complete=alerts.get('complete') is True
        if warnings or not complete:
            parts.append('<rect x="70" y="165" width="1405" height="170" rx="16" fill="#761e24"/>')
            heading='URGENT · SELECTED SOURCES' if complete else 'URGENT · ALERT INPUTS INCOMPLETE'
            parts.append(f'<text x="95" y="193" fill="#ffffff" font-family="sans-serif" font-size="19" font-weight="bold">{heading}</text>')
            if warnings:
                pages=(len(warnings)+3)//4;page=int((time.time() if now is None else now)//300)%pages
                for i,item in enumerate(warnings[page*4:page*4+4]):
                    parts.append(f'<text x="95" y="{220+i*29}" fill="#ffffff" font-family="sans-serif" font-size="22" font-weight="bold">{clean(item.get("title"),92)}</text>')
                if pages>1:parts.append(f'<text x="1370" y="193" fill="#ffffff" font-family="sans-serif" font-size="18">{page+1}/{pages}</text>')
            else:parts.append('<text x="95" y="230" fill="#ffffff" font-family="sans-serif" font-size="22">Alert sources unavailable</text>')
        else:parts.append('<text x="70" y="172" fill="#c4d5c9" font-family="sans-serif" font-size="19">No active alerts from selected sources</text>')
    parts.append('</svg>')
    return ''.join(parts).encode('utf-8')
