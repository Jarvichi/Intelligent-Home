"""Read-only e-ink preview; it does not publish a touch-zone map."""
import datetime
import re
import time
import math
import textwrap
from render import clean
import cooking_render

ART=re.compile(r'^data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]{1,700000}$')

def task_page(tasks,offset,now):
    # Existing Kindle pins messages and two collections on every five-minute page.
    if not isinstance(tasks,list):return [],0,1,False
    grouped=all(isinstance(x,dict) and x.get('display_group') in ('message','task','collection') for x in tasks)
    if not grouped:
        pages=max(1,math.ceil(len(tasks)/5));page=(int(now//300)+offset)%pages
        return tasks[page*5:page*5+5],page,pages,False
    messages=[x for x in tasks if x['display_group']=='message'][:3]
    collections=[x for x in tasks if x['display_group']=='collection'][:2]
    general=[x for x in tasks if x['display_group']=='task']
    per_page=max(1,3-len(messages));pages=max(1,math.ceil(len(general)/per_page));page=(int(now//300)+offset)%pages
    return (messages+general[page*per_page:page*per_page+per_page]+collections)[:5],page,pages,True

def dismissible_alert(data,alerts,now):
    """The one displayed energy warning, if present in the current cycle."""
    if not isinstance(alerts,dict):return None
    entries=[x for x in (alerts.get('alerts') or []) if isinstance(x,dict) and x.get('title')]
    life=[x for x in entries if x.get('kind') in ('smoke','co')]
    active=[x for x in (data.get('panels') or []) if isinstance(x,dict) and x.get('active')]
    motion=[None for x in active if x.get('label')=='Motion while away' and x.get('urgent') is True]
    other=motion+[x for x in entries if x.get('kind') not in ('smoke','co')]
    status=alerts.get('configured') is False or not alerts.get('complete')
    slots=max(0,4-min(len(life),2)-(1 if status else 0));pages=max(1,(len(other)+slots-1)//slots) if slots else 1;page=int(now//300)%pages
    visible=other[page*slots:page*slots+slots] if slots else []
    matches=[x for x in visible if isinstance(x,dict) and x.get('kind') in ('free','saving') and x.get('dismissible') is True]
    return matches[0] if len(matches)==1 else None

def svg(data,alerts=None,now=None):
    if isinstance(data.get('cooking'),dict) and data['cooking'].get('running') is True:return cooking_render.svg(data['cooking'],alerts)
    timestamp=time.time() if now is None else now;today=datetime.datetime.fromtimestamp(timestamp).astimezone()
    p=['<svg xmlns="http://www.w3.org/2000/svg" width="1072" height="1448" viewBox="0 0 1072 1448"><defs><filter id="gray"><feColorMatrix type="saturate" values="0"/></filter></defs><rect width="1072" height="1448" fill="white"/>']
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
    device=data.get('device') if isinstance(data.get('device'),dict) else {};charging=device.get('charging') is True;info='Updated '+today.strftime('%H:%M')
    if device.get('fresh') and device.get('battery') is not None:info+=' · '+('Plugged in' if charging else 'Kindle '+str(device['battery'])+'%')
    text(970 if charging else 1024,64,info,25,anchor='end')
    if charging:p.append('<circle cx="1005" cy="55" r="19" fill="black"/><path d="M998 55a8 8 0 1 0 8-8 M1004 43l5 4-5 4" fill="none" stroke="white" stroke-width="3"/>')
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
        bearing=facts.get('bearing')
        if isinstance(bearing,(int,float)) and not isinstance(bearing,bool) and math.isfinite(bearing):
            p.append(f'<g aria-label="Wind direction" transform="translate(585 289) rotate({(bearing+180)%360:g})"><path d="M0,-16 L11,0 L4,0 L4,16 L-4,16 L-4,0 L-11,0 Z" fill="black"/></g>')
        future=[x for x in daily if x.get('label')!='Today'][:2]
        for i,day in enumerate(future):
            y=238+i*118;box(640,y,384,98);name=day.get('label','')
            if name=='Next day':name=(today+datetime.timedelta(days=2)).strftime('%A')
            icon(day.get('condition'),935,y+10,70);text(662,y+43,name,30,True);rain=day.get('precipitation');forecast=str(day.get('low','–'))+'° – '+str(day.get('high','–'))+'°'
            if isinstance(rain,(int,float)) and not isinstance(rain,bool):forecast+=(' '+str(round(rain,1))+' mm rain') if rain>=0.1 else ' dry'
            text(662,y+79,forecast,23)
        box(48,478,976,190)
        for i,h in enumerate(hours[:6]):
            x=130+i*163;text(x,520,h.get('time',''),25,True,'middle');icon(h.get('condition'),x-30,532,60);text(x,630,str(h.get('temperature','–'))+'°',36,True,'middle')
            rain=h.get('precipitation')
            if isinstance(rain,(int,float)) and not isinstance(rain,bool) and rain>0.1:text(x,655,str(round(rain,1))+' mm',17,anchor='middle')
            if i:p.append(f'<path d="M{x-80} 496v152" stroke="#aaa" stroke-width="2"/>')
    else:text(48,330,'Weather unavailable' if data.get('weather_configured') else 'Weather not configured',30)
    game=next((x for x in active if x.get('kind')=='game'),None)
    if game:
        box(640,238,384,216);art=game.get('art')
        if isinstance(art,str) and ART.fullmatch(art):p.append(f'<image href="{art}" x="653" y="251" width="140" height="190" preserveAspectRatio="xMidYMid meet" filter="url(#gray)"/>')
        text(806,278,'NOW PLAYING',18,True)
        for i,line in enumerate(textwrap.wrap(str(game.get('detail') or game.get('label') or 'Game'),width=14,max_lines=3,placeholder='…')):text(806,316+i*32,line,24,True)
    media=next((x for x in active if x.get('kind')=='media'),None)
    if media:
        box(48,478,976,190);art=media.get('art')
        if isinstance(art,str) and ART.fullmatch(art):p.append(f'<image href="{art}" x="62" y="493" width="160" height="160" preserveAspectRatio="xMidYMid meet" filter="url(#gray)"/>')
        parts=str(media.get('detail') or '').split(' · ',1);text(245,524,'NOW PLAYING',20,True);text(245,574,(parts[0] or str(media.get('label') or 'Playing'))[:42],30,True);text(245,620,(parts[1] if len(parts)>1 else '')[:55],23)
    label(48,707,'HOUSE',25)
    alarm=next((x for x in panels if x.get('label')=='Alarm'),{});home=next((x for x in panels if x.get('label')=='Home'),{});doors=next((x for x in panels if str(x.get('label','')).startswith('Doors')),{});heat=next((x for x in panels if str(x.get('label','')).startswith(('Heat','Heating','Floors'))),{})
    for i,(title,value) in enumerate((('ALARM',alarm.get('detail')),('HOME',home.get('detail')),('DOORS',doors.get('label','').removeprefix('Doors ')),('HEAT',heat.get('label','').removeprefix('Heat ')))):
        x=48+i*248;inverted=title=='DOORS' and doors.get('urgent') is True
        box(x,726,232,140,'black' if inverted else 'white');label(x+20,761,title,21,'white' if inverted else 'black')
        persons=home.get('persons') or [] if title=='HOME' else []
        if persons:
            for j,person in enumerate(persons[:3]):
                px=x+20+j*64;art=person.get('art') if isinstance(person,dict) else None
                p.append(f'<circle cx="{px+28}" cy="816" r="26" fill="#ddd" stroke="black" stroke-width="2"/>')
                if isinstance(art,str) and ART.fullmatch(art):p.append(f'<clipPath id="person{j}"><circle cx="{px+28}" cy="816" r="25"/></clipPath><image href="{art}" x="{px+3}" y="791" width="50" height="50" preserveAspectRatio="xMidYMid slice" clip-path="url(#person{j})" filter="url(#gray)"/>')
                else:text(px+28,824,(person.get('name') or '?')[:1],22,True,'middle')
        else:
            shown=(str(value or 'Unavailable')[:12]+'…') if len(str(value or 'Unavailable'))>13 else value or 'Unavailable'
            if inverted:label(x+20,809,shown,23,'white')
            else:text(x+20,809,shown,23,True)
        if title=='HEAT' and heat.get('detail'):text(x+20,843,heat['detail'],18)
        if title=='DOORS' and doors.get('detail'):
            if inverted:label(x+20,843,str(doors['detail'])[:23],18,'white')
            else:text(x+20,843,doors['detail'],18)
    if charging and isinstance(data.get('cooking'),dict) and 16<=today.hour<19:
        box(48,726,232,140,'black');label(164,786,'COOK',30,'white','middle');label(164,827,'DINNER',30,'white','middle')
    lights=next((x for x in panels if x.get('label')=='Lights on'),{})
    label(48,911,'LIGHTS ON',25);text(48,958,lights.get('detail') or 'Unavailable',26)
    if charging:
        p.append('<rect x="270" y="884" width="140" height="36" rx="18" fill="black"/><rect x="880" y="884" width="144" height="36" rx="18" fill="black"/>');label(340,909,'ALL OFF',18,'white','middle');label(952,909,'MENU',18,'white','middle')
        if (today.month,today.day)==(10,31) and data.get('halloween_mode') in ('on','off'):
            on=data['halloween_mode']=='on';p.append(f'<rect x="716" y="882" width="148" height="40" rx="20" fill="{"white" if on else "black"}" stroke="black" stroke-width="3"/>');label(790,910,'PARTY',20,'black' if on else 'white','middle')
    p.append('<path d="M48 1031H1024" stroke="black" stroke-width="3"/>')
    energy=next((x for x in panels if x.get('label')=='Energy'),{})
    energy_detail=energy.get('detail') or 'Energy unavailable'
    if str(energy_detail).startswith('Saving'):
        box(48,1040,976,48,'black');label(65,1073,energy_detail,22,'white')
    else:text(48,1065,energy_detail,25,True)
    if data.get('cooking_unavailable') is True:text(1024,1090,'Cooking unavailable · controls paused',18,True,'end')
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
        p.append('<rect x="60" y="490" width="952" height="166" rx="16" fill="none" stroke="white" stroke-width="3" stroke-dasharray="14 10"/>')
        for x in (135,937):
            p.append(f'<g transform="translate({x} 573)"><path d="M0,-50 L48,35 L-48,35 Z" fill="white" stroke="white" stroke-width="7" stroke-linejoin="round"/><text x="0" y="25" text-anchor="middle" font-family="sans-serif" font-size="58" font-weight="bold">!</text></g>')
        for i,item in enumerate(warning):
            if len(warning)<=2:x,y,limit,size=536,565+(i*48 if len(warning)==2 else 14),38,30
            else:x,y,limit,size=(350 if i%2==0 else 722),555+(i//2)*48,20,23
            value=clean(item,70);fit=f' textLength="{(650 if len(warning)<=2 else 310)}" lengthAdjust="spacingAndGlyphs"' if len(str(item))>limit else ''
            p.append(f'<text x="{x}" y="{y}" fill="white" font-family="sans-serif" font-size="{size}" font-weight="bold" text-anchor="middle"{fit}>{value}</text>')
        if pages>1:label(995,650,f'{page+1}/{pages}',18,'white','end')
        if data.get('allow_alert_dismiss') is True and dismissible_alert(data,alerts,timestamp):
            p.append('<rect x="872" y="612" width="142" height="52" rx="20" fill="white"/>');label(943,645,'DISMISS',20,'black','middle')
    tasks=data.get('tasks');partial=data.get('tasks_partial') is True
    offset=device.get('page_offset') if type(device.get('page_offset')) is int else 0
    rows,page,pages,grouped=task_page(tasks,offset,timestamp)
    label(48,1136,'TO DO'+(f' · {page+1}/{pages}' if pages>1 else ''),25)
    if partial:text(1024,1136,'Some task sources unavailable',18,anchor='end')
    elif isinstance(tasks,list) and tasks and not grouped:text(1024,1136,'Task grouping unavailable',18,anchor='end')
    elif charging and pages>1:
        for x,caption in ((800,'PREV'),(916,'NEXT')):
            p.append(f'<rect x="{x}" y="1106" width="108" height="36" rx="18" fill="black"/>');label(x+54,1131,caption,18,'white','middle')
    if tasks is None:text(48,1180,'Task sources unavailable' if data.get('tasks_configured') else 'Task sources not configured',25)
    elif not rows and partial:text(48,1180,'Task sources unavailable',25)
    elif len(rows)<=2:
        if not rows:text(70,1201,'No tasks',23,True)
        for i,task in enumerate(rows):
            x=48+i*494;urgent=task.get('urgent') is True;fill='white' if urgent else 'black';box(x,1150,482,144,'black' if urgent else 'white')
            lines=textwrap.wrap(str(task.get('title','')),width=25,max_lines=2,placeholder='…')
            for j,line in enumerate(lines):label(x+24,1201+j*32,line,23,fill)
            if task.get('detail'):label(x+24,1277,str(task['detail'])[:35],19,fill)
        rooms=[x for x in panels if x.get('kind')=='room'][:10]
        if rooms:
            label(48,1334,'ROOMS',25)
            for i,room in enumerate(rooms):
                x=97+i*97;text(x,1378,str(room.get('label',''))[:10],14,anchor='middle');text(x,1424,str(room.get('detail','–')),20,True,'middle')
    else:
        h,pitch,font={3:(87,99,29),4:(61,75,27),5:(50,59,24)}[min(len(rows),5)]
        for i,task in enumerate(rows[:5]):
            y=1150+i*pitch;urgent=task.get('urgent') is True;fill='white' if urgent else 'black';box(48,y,976,h,'black' if urgent else 'white')
            title=str(task.get('title',''));detail=str(task.get('detail') or '');title=title if len(title)<=48 else title[:47]+'…'
            label(70,y+h//2+font//3,title,font,fill)
            if detail:label(1000,y+h//2+7,detail[:22],18,fill,'end')
    return (''.join(p)+'</svg>').encode('utf-8')
