"""Read-only Home Assistant data and an app-owned SVG display prototype."""
import html
import http.client
import json
import re
import time
import datetime
import math
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
                    hours.append((stamp,{'time':date.astimezone().strftime('%H:%M'),'temperature':round(temp),'condition':condition.replace('_',' ')}))
                hours.sort(key=lambda x:x[0])
                if not hours or hours[0][0]>now+3600:continue
                return {'source':source,'hours':[x[1] for x in hours[:5]]}
            except (OSError,ValueError,KeyError,TypeError,OverflowError,urllib.error.URLError,http.client.HTTPException):continue
        return None
    def snapshot(self,entities,todo,feed='',shared=None,weather_primary='',weather_fallback=''):
        entities=entities if isinstance(entities,list) else []
        entities=[x for x in entities[:6] if isinstance(x,str)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures=[pool.submit(self.state,e) for e in entities]
            tf=pool.submit(self.todos,todo) if todo else None
            ff=pool.submit(self.task_feed,feed) if feed else None
            sf=pool.submit(shared) if shared else None
            wf=pool.submit(self.forecast,weather_primary,weather_fallback) if weather_primary or weather_fallback else None
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
            return {'items':[f.result() for f in futures], 'tasks':tasks, 'tasks_configured':configured,'tasks_partial':partial,'weather':wf.result() if wf else None,'weather_configured':bool(wf)}

def svg(profile,data,alerts=None,now=None):
    if not isinstance(profile,dict) or profile.get('state')!='ready':raise ValueError('Profile unavailable')
    width,height=profile.get('width'),profile.get('height')
    if any(type(v) is not int or not 1<=v<=8192 for v in (width,height)):raise ValueError('Invalid dimensions')
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid meet">',
           '<rect width="1920" height="1080" fill="#101619"/>',
           '<text x="70" y="105" fill="#f4f7f4" font-family="sans-serif" font-size="44" font-weight="bold">HOME</text>',
           '<text x="70" y="148" fill="#b6c6bd" font-family="sans-serif" font-size="24">Wall display preview</text>']
    items=data.get('items') or []
    for i,item in enumerate(items[:6]):
        x=70+(i%2)*405;y=190+(i//2)*185
        parts.append(f'<rect x="{x}" y="{y}" width="375" height="155" rx="20" fill="#26352f"/>')
        parts.append(f'<text x="{x+24}" y="{y+45}" fill="#c4d5c9" font-family="sans-serif" font-size="22">{clean(item.get("label"),24)}</text>')
        parts.append(f'<text x="{x+24}" y="{y+105}" fill="#f7faf7" font-family="sans-serif" font-size="38">{clean(item.get("value"),18)}</text>')
    if not items:parts.append('<text x="70" y="245" fill="#c4d5c9" font-family="sans-serif" font-size="25">Choose display entities in the app configuration.</text>')
    parts.append('<rect x="900" y="190" width="575" height="525" rx="20" fill="#26352f"/>')
    parts.append('<text x="930" y="245" fill="#f7faf7" font-family="sans-serif" font-size="30" font-weight="bold">Selected tasks</text>')
    tasks=data.get('tasks')
    if tasks is None:
        label='Task list unavailable' if data.get('tasks_configured') else 'Choose a task list in the app configuration.'
        parts.append(f'<text x="930" y="300" fill="#c4d5c9" font-family="sans-serif" font-size="22">{clean(label)}</text>')
    elif not tasks and not data.get('tasks_partial'):parts.append('<text x="930" y="300" fill="#c4d5c9" font-family="sans-serif" font-size="22">No items from selected sources</text>')
    else:
        for i,task in enumerate(tasks[:5]):
            title=task.get('title','') if isinstance(task,dict) else task
            detail=task.get('detail','') if isinstance(task,dict) else ''
            colour='#ffd88c' if isinstance(task,dict) and task.get('urgent') is True else '#f7faf7'
            parts.append(f'<text x="930" y="{305+i*75}" fill="{colour}" font-family="sans-serif" font-size="24">• {clean(title,37)}</text>')
            if detail:parts.append(f'<text x="950" y="{331+i*75}" fill="#c4d5c9" font-family="sans-serif" font-size="18">{clean(detail,43)}</text>')
        if data.get('tasks_partial'):parts.append('<text x="930" y="658" fill="#ffd88c" font-family="sans-serif" font-size="20">Some task sources are unavailable</text>')
    parts.append('<text x="930" y="694" fill="#c4d5c9" font-family="sans-serif" font-size="18">Only selected sources are shown</text>')
    if data.get('weather_configured'):
        weather=data.get('weather')
        parts.append('<rect x="70" y="730" width="1405" height="75" rx="16" fill="#26352f"/>')
        if not weather:parts.append('<text x="90" y="775" fill="#ffd88c" font-family="sans-serif" font-size="22">Hourly forecast unavailable</text>')
        else:
            label='Hourly forecast' if weather['source']=='primary' else 'Hourly forecast · fallback'
            parts.append(f'<text x="90" y="756" fill="#c4d5c9" font-family="sans-serif" font-size="18">{label}</text>')
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
