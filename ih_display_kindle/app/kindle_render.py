"""Read-only e-ink preview; it does not publish a touch-zone map."""
import datetime
import time
from render import clean

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
    if event:text(1024,114,event.get('detail',''),25,True,'end')
    text(1024,181,data.get('sun') or 'Sun unavailable',34,True,'end');p.append('<path d="M48 214H1024" stroke="black" stroke-width="3"/>')
    weather=data.get('weather')
    if weather:
        current=weather.get('current') or {};facts=weather.get('facts') or {};daily=weather.get('daily') or [];hours=weather.get('hours') or []
        icon(current.get('condition'),48,248,125);text(200,350,str(current.get('temperature','–'))+'°',110,True);condition=str(current.get('condition') or 'Unavailable').replace('-',' ').replace('_',' ').capitalize();text(48,397,condition,37,True)
        day=next((d for d in daily if d.get('label')=='Today'),{})
        if day:text(48,438,'Today '+str(day.get('low','–'))+'° – '+str(day.get('high','–'))+'°',28)
        for i,(key,value) in enumerate((('WIND',facts.get('wind','Unavailable')),('NEXT RAIN',facts.get('next_rain','Unavailable')),('SKY',facts.get('sky','Unavailable')))):
            label(425,269+i*74,key,19);text(425,300+i*74,value,28,True)
        future=[x for x in daily if x.get('label')!='Today'][:2]
        for i,day in enumerate(future):
            y=238+i*118;box(640,y,384,98);name=day.get('label','')
            if name=='Next day':name=(today+datetime.timedelta(days=2)).strftime('%A')
            icon(day.get('condition'),935,y+10,70);text(662,y+43,name,30,True);text(662,y+79,str(day.get('low','–'))+'° – '+str(day.get('high','–'))+'°',26)
        box(48,478,976,190)
        for i,h in enumerate(hours[:6]):
            x=130+i*163;text(x,520,h.get('time',''),25,True,'middle');icon(h.get('condition'),x-30,532,60);text(x,630,str(h.get('temperature','–'))+'°',36,True,'middle')
            if i:p.append(f'<path d="M{x-80} 496v152" stroke="#aaa" stroke-width="2"/>')
    else:text(48,330,'Weather unavailable' if data.get('weather_configured') else 'Weather not configured',30)
    label(48,707,'HOUSE',25)
    alarm=next((x for x in panels if x.get('label')=='Alarm'),{});home=next((x for x in panels if x.get('label')=='Home'),{});doors=next((x for x in panels if str(x.get('label','')).startswith('Doors')),{});heat=next((x for x in panels if str(x.get('label','')).startswith(('Heat','Heating','Floors'))),{})
    for i,(title,value) in enumerate((('ALARM',alarm.get('detail')),('HOME',home.get('detail')),('DOORS',doors.get('label','').removeprefix('Doors ')),('HEAT',heat.get('label','').removeprefix('Heat ')))):
        x=48+i*248;box(x,726,232,140);label(x+20,761,title,21);text(x+20,809,value or 'Unavailable',27,True)
    lights=next((x for x in panels if x.get('label')=='Lights on'),{})
    label(48,911,'LIGHTS ON',25);text(48,958,lights.get('detail') or 'Unavailable',26)
    p.append('<path d="M48 1031H1024" stroke="black" stroke-width="3"/>')
    energy=next((x for x in panels if x.get('label')=='Energy'),{})
    text(48,1065,energy.get('detail') or 'Energy unavailable',25,True)
    warning=[]
    if isinstance(alerts,dict):warning=[str(x.get('title','')) for x in (alerts.get('alerts') or []) if isinstance(x,dict)]
    if alerts is None or (isinstance(alerts,dict) and not alerts.get('complete')):warning.append('Alert sources unavailable')
    if warning:
        box(48,486,976,90,'black');label(65,540,' · '.join(warning[:2]),27,'white')
    tasks=data.get('tasks');partial=data.get('tasks_partial') is True
    label(48,1136,'TO DO',25)
    if tasks is None:text(48,1180,'Task sources unavailable' if data.get('tasks_configured') else 'Task sources not configured',25)
    elif not tasks:text(48,1180,'No tasks' if not partial else 'Task sources unavailable',25)
    else:
        page=int(timestamp//60)%max(1,(len(tasks)+4)//5)
        for i,task in enumerate(tasks[page*5:page*5+5]):
            y=1150+i*58;urgent=task.get('urgent') is True;box(48,y,976,50,'black' if urgent else 'white');label(70,y+34,task.get('title',''),24,'white' if urgent else 'black')
    if partial:text(1024,1136,'Some task sources unavailable',18,anchor='end')
    return (''.join(p)+'</svg>').encode('utf-8')
