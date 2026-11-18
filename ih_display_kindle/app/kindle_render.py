"""Read-only e-ink preview; it does not publish a touch-zone map."""
import datetime
import re
import time
from render import clean

ART=re.compile(r'^data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]{1,700000}$')

def svg(data,alerts=None,now=None):
    timestamp=time.time() if now is None else now;today=datetime.datetime.fromtimestamp(timestamp).astimezone()
    p=['<svg xmlns="http://www.w3.org/2000/svg" width="1072" height="1448" viewBox="0 0 1072 1448"><rect width="1072" height="1448" fill="white"/>']
    def text(x,y,value,size=28,bold=False,anchor='start'):
        p.append(f'<text x="{x}" y="{y}" fill="black" font-family="sans-serif" font-size="{size}" font-weight="{"bold" if bold else "normal"}" text-anchor="{anchor}">{clean(value,80)}</text>')
    def box(x,y,w,h,fill='white'):
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="{fill}" stroke="black" stroke-width="3"/>')
    def label(x,y,value,size=28,fill='black',anchor='start'):
        p.append(f'<text x="{x}" y="{y}" fill="{fill}" font-family="sans-serif" font-size="{size}" font-weight="bold" text-anchor="{anchor}">{clean(value,70)}</text>')
    def icon(condition,x,y,size):
        condition=str(condition or '').lower();p.append(f'<g transform="translate({x} {y}) scale({size/100})">')
        if 'sun' in condition or 'clear' in condition or 'partly' in condition:
            p.append('<circle cx="35" cy="30" r="22" fill="#777"/>')
        if any(key in condition for key in ('cloud','rain','snow','pour')) or not condition:
            p.append('<path d="M18 70 C3 70 4 48 20 47 C24 27 50 27 58 42 C74 32 91 43 88 56 C99 60 96 72 82 72 Z" fill="#bbb" stroke="#777" stroke-width="2"/>')
        if 'rain' in condition or 'pour' in condition:p.append('<path d="M30 79l-5 11 M52 79l-5 11 M74 79l-5 11" stroke="#555" stroke-width="5"/>')
        if 'snow' in condition:p.append('<path d="M30 82l5 5m0-5l-5 5 M62 82l5 5m0-5l-5 5" stroke="#555" stroke-width="4"/>')
        p.append('</g>')
    text(48,115,today.strftime('%A'),96,True);text(48,187,str(today.day)+' '+today.strftime('%B'),52)
    text(1024,64,'Updated '+today.strftime('%H:%M'),25,anchor='end')
    panels=[x for x in (data.get('panels') or []) if isinstance(x,dict)];active=[x for x in panels if x.get('active')]
    event=next((x for x in active if x.get('slot')=='occasion'),None)
    if event:
        occasion=str(event.get('detail') or '');match=re.fullmatch(r'(.+?) in (\d+) days',occasion)
        if match:occasion=match.group(2)+' days: '+match.group(1)
        text(1024,114,occasion,25,True,'end')
    text(1024,181,data.get('sun') or 'Sun unavailable',34,True,'end');p.append('<path d="M48 214H1024" stroke="black" stroke-width="3"/>')
    weather=data.get('weather')
    if weather:
        current=weather.get('current') or {};facts=weather.get('facts') or {};daily=weather.get('daily') or [];hours=weather.get('hours') or []
        icon(current.get('condition'),48,248,125);text(200,350,str(current.get('temperature','–'))+'°',110,True);condition=str(current.get('condition') or 'Unavailable');condition={'partlycloudy':'Partly cloudy','clear-night':'Clear night','snowy-rainy':'Sleet','lightning-rainy':'Thunder and rain'}.get(condition,condition.replace('-',' ').replace('_',' ').capitalize());text(48,397,condition,37,True)
        day=next((d for d in daily if d.get('label')=='Today'),{})
        if day:text(48,438,'Today '+str(day.get('low','–'))+'° – '+str(day.get('high','–'))+'°',28)
        sky=str(facts.get('sky','Unavailable'));sky_label='UV INDEX' if sky.startswith('UV ') else 'CLOUD COVER';sky_value=sky.removeprefix('UV ').removeprefix('Cloud ')
        for i,(key,value) in enumerate((('WIND',facts.get('wind','Unavailable')),('NEXT RAIN',facts.get('next_rain','Unavailable')),(sky_label,sky_value))):
            label(425,269+i*74,key,19);text(425,300+i*74,value,28,True)
        future=[x for x in daily if x.get('label')!='Today'][:2]
        for i,day in enumerate(future):
            y=238+i*118;box(640,y,384,98);name=day.get('label','')
            if name=='Next day':name=(today+datetime.timedelta(days=2)).strftime('%A')
            icon(day.get('condition'),935,y+10,70);text(662,y+43,name,30,True);rain=day.get('precipitation');forecast=str(day.get('low','–'))+'° – '+str(day.get('high','–'))+'°';
            if isinstance(rain,(int,float)) and not isinstance(rain,bool):forecast+=(' '+str(round(rain,1))+' mm rain') if rain>=0.1 else ' dry'
            text(662,y+79,forecast,23)
        box(48,478,976,190)
        for i,h in enumerate(hours[:6]):
            x=130+i*163;text(x,520,h.get('time',''),25,True,'middle');icon(h.get('condition'),x-30,532,60);text(x,630,str(h.get('temperature','–'))+'°',36,True,'middle')
            rain=h.get('precipitation')
            if isinstance(rain,(int,float)) and not isinstance(rain,bool) and rain>0.1:text(x,655,str(round(rain,1))+' mm',17,anchor='middle')
            if i:p.append(f'<path d="M{x-80} 496v152" stroke="#aaa" stroke-width="2"/>')
    else:text(48,330,'Weather unavailable' if data.get('weather_configured') else 'Weather not configured',30)
    label(48,707,'HOUSE',25)
    alarm=next((x for x in panels if x.get('label')=='Alarm'),{});home=next((x for x in panels if x.get('label')=='Home'),{});doors=next((x for x in panels if str(x.get('label','')).startswith('Doors')),{});heat=next((x for x in panels if str(x.get('label','')).startswith(('Heat','Heating','Floors'))),{})
    for i,(title,value) in enumerate((('ALARM',alarm.get('detail')),('HOME',home.get('detail')),('DOORS',doors.get('label','').removeprefix('Doors ')),('HEAT',heat.get('label','').removeprefix('Heat ')))):
        x=48+i*248;box(x,726,232,140);label(x+20,761,title,21)
        persons=home.get('persons') or [] if title=='HOME' else []
        if persons:
            p.append('<defs><filter id="gray"><feColorMatrix type="saturate" values="0"/></filter></defs>')
            for j,person in enumerate(persons[:3]):
                px=x+20+j*64;art=person.get('art') if isinstance(person,dict) else None
                p.append(f'<circle cx="{px+28}" cy="816" r="26" fill="#ddd" stroke="black" stroke-width="2"/>')
                if isinstance(art,str) and ART.fullmatch(art):p.append(f'<clipPath id="person{j}"><circle cx="{px+28}" cy="816" r="25"/></clipPath><image href="{art}" x="{px+3}" y="791" width="50" height="50" preserveAspectRatio="xMidYMid slice" clip-path="url(#person{j})" filter="url(#gray)"/>')
                else:text(px+28,824,(person.get('name') or '?')[:1],22,True,'middle')
        else:text(x+20,809,(str(value or 'Unavailable')[:12]+'…') if len(str(value or 'Unavailable'))>13 else value or 'Unavailable',23,True)
        if title=='HEAT' and heat.get('detail'):text(x+20,843,heat['detail'],18)
        if title=='DOORS' and doors.get('detail'):text(x+20,843,doors['detail'],18)
    lights=next((x for x in panels if x.get('label')=='Lights on'),{})
    label(48,911,'LIGHTS ON',25);text(48,958,lights.get('detail') or 'Unavailable',26);p.append('<rect x="270" y="884" width="140" height="36" rx="18" fill="black"/><rect x="880" y="884" width="144" height="36" rx="18" fill="black"/>');label(340,909,'ALL OFF',18,'white','middle');label(952,909,'MENU',18,'white','middle')
    p.append('<path d="M48 1031H1024" stroke="black" stroke-width="3"/>')
    energy=next((x for x in panels if x.get('label')=='Energy'),{})
    energy_detail=energy.get('detail') or 'Energy unavailable'
    if str(energy_detail).startswith('Saving'):
        box(48,1040,976,48,'black');label(65,1073,energy_detail,22,'white')
    else:text(48,1065,energy_detail,25,True)
    # Keep life-safety alerts visible while cycling other alerts. The source
    # emits at most one aggregate smoke alert and one CO alert.
    entries=[x for x in (alerts.get('alerts') or []) if isinstance(x,dict) and x.get('title')] if isinstance(alerts,dict) else []
    life=[str(x['title']) for x in entries if x.get('kind') in ('smoke','co')]
    motion=[str(x.get('label'))+(': '+str(x.get('detail')) if x.get('detail') else '') for x in active if x.get('label')=='Motion while away' and x.get('urgent') is True]
    other=motion+[str(x['title']) for x in entries if x.get('kind') not in ('smoke','co')]
    status='Alert sources not configured' if isinstance(alerts,dict) and alerts.get('configured') is False else 'Alert sources unavailable' if alerts is None or (isinstance(alerts,dict) and not alerts.get('complete')) else ''
    slots=max(0,4-min(len(life),2)-(1 if status else 0));pages=max(1,(len(other)+slots-1)//slots) if slots else 1;page=int(timestamp//300)%pages
    warning=life[:2]+(other[page*slots:page*slots+slots] if slots else [])+([status] if status else [])
    if warning:
        box(48,478,976,190,'black')
        for i,item in enumerate(warning):label(70,517+i*37,item[:52],25,'white')
        if pages>1:label(995,650,f'{page+1}/{pages}',18,'white','end')
    tasks=data.get('tasks');partial=data.get('tasks_partial') is True
    label(48,1136,'TO DO',25)
    if tasks is None:text(48,1180,'Task sources unavailable' if data.get('tasks_configured') else 'Task sources not configured',25)
    elif not tasks:text(48,1180,'No tasks' if not partial else 'Task sources unavailable',25)
    else:
        pages=max(1,(len(tasks)+4)//5);page=int(timestamp//60)%pages
        if pages>1:text(180,1136,f'{page+1}/{pages}',22,True)
        for i,task in enumerate(tasks[page*5:page*5+5]):
            y=1150+i*58;urgent=task.get('urgent') is True;fill='white' if urgent else 'black';box(48,y,976,50,'black' if urgent else 'white')
            title=str(task.get('title',''));detail=str(task.get('detail') or '');title=title if len(title)<=48 else title[:47]+'…'
            label(70,y+34,title,24,fill)
            if detail:label(1000,y+33,detail[:22],18,fill,'end')
    if partial:text(1024,1136,'Some task sources unavailable',18,anchor='end')
    return (''.join(p)+'</svg>').encode('utf-8')
