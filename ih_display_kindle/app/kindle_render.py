"""Read-only e-ink preview; it does not publish a touch-zone map."""
import datetime
import cat_render
import halloween_art
import re
import time
import math
import textwrap
from render import clean
import cooking_render
import festive_header
import weather_icons

CONDITION_NAMES={'partlycloudy':'Partly cloudy','clear-night':'Clear','lightning-rainy':'Thunder & rain','snowy-rainy':'Sleet','windy-variant':'Windy','sunny':'Sunny','cloudy':'Cloudy','rainy':'Showers','pouring':'Heavy rain','fog':'Fog','snowy':'Snow','hail':'Hail','lightning':'Thunder','windy':'Windy','exceptional':'Warning'}
ENVELOPE='\u2709\ufe0e '   # text-style envelope in front of a message row, as on the original Kindle

def cap(value):
    value=str(value or '');return value[:1].upper()+value[1:]

def num(value):
    return f'{round(value,1):g}'

def next_rain_text(value):
    value=str(value)
    return 'Dry 3 days' if value=='None in hourly forecast' else value.replace(' next day',' tmrw')

def detail_text(value):
    return re.sub(r'^In (\d+ days?)$',r'in \1',str(value))

ART=re.compile(r'^data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]{1,700000}$')

def text_units(value):
    return sum(1.0 if c in 'MW@%&' or ord(c)>0x2fff else .32 if c in " ilI.,'!:;|" else .72 if c.isupper() else .6 for c in str(value or ''))

def fitted_size(value,size,width):
    """Keep changing source text within its cell without dropping words.

    A conservative glyph estimate accommodates the deployed sans-serif font
    and mixed-case names without relying on character count alone.
    """
    return min(size,max(10,int(width/max(text_units(value),1))))

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
    halloween=data.get('halloween_mode')=='on' or (today.month,today.day)==(10,31)
    p=['<svg xmlns="http://www.w3.org/2000/svg" width="1072" height="1448" viewBox="0 0 1072 1448"><defs><filter id="gray"><feColorMatrix type="saturate" values="0"/></filter></defs><rect width="1072" height="1448" fill="white"/>']
    header_color='black';header_font='Roboto, sans-serif'
    def text(x,y,value,size=28,bold=False,anchor='start',max_width=None):
        color=header_color if y<=187 else 'black';font=header_font if y<=187 else 'Roboto, sans-serif'
        if max_width:size=fitted_size(value,size,max_width)
        fit=f' textLength="{max_width}" lengthAdjust="spacingAndGlyphs"' if max_width and text_units(value)*size>max_width else ''
        p.append(f'<text x="{x}" y="{y}" fill="{color}" font-family="{font}" font-size="{size}" font-weight="{"bold" if bold else "normal"}" text-anchor="{anchor}"{fit}>{clean(value,256)}</text>')
    def box(x,y,w,h,fill='white'):
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="{fill}" stroke="black" stroke-width="3"/>')
    def label(x,y,value,size=28,fill='black',anchor='start',max_width=None):
        if max_width:size=fitted_size(value,size,max_width)
        fit=f' textLength="{max_width}" lengthAdjust="spacingAndGlyphs"' if max_width and text_units(value)*size>max_width else ''
        p.append(f'<text x="{x}" y="{y}" fill="{fill}" font-family="Roboto, sans-serif" font-size="{size}" font-weight="bold" text-anchor="{anchor}"{fit}>{clean(value,256)}</text>')
    def icon(condition,x,y,size):
        p.append(weather_icons.svg(condition,x,y,size))
    panels=[x for x in (data.get('panels') or []) if isinstance(x,dict)];active=[x for x in panels if x.get('active')]
    event=next((x for x in active if x.get('slot')=='occasion'),None)
    inverted,header_font=festive_header.style(event)
    if halloween:inverted=True
    if inverted:
        p.append('<rect width="1072" height="214" fill="black"/>');header_color='white'
    text(48,115,today.strftime('%A'),96,True);text(48,187,str(today.day)+' '+today.strftime('%B'),52)
    device=data.get('device') if isinstance(data.get('device'),dict) else {};charging=device.get('charging') is True;info='Updated '+today.strftime('%H:%M')
    if device.get('fresh') and device.get('battery') is not None:info+=' · '+('⚡ Plugged in' if charging else '🔋 Kindle '+str(device['battery'])+'%')
    text(970 if charging else 1024,64,info,25,anchor='end')
    if charging:p.append(f'<circle cx="1005" cy="55" r="19" fill="{header_color}"/><path d="M998 55a8 8 0 1 0 8-8 M1004 43l5 4-5 4" fill="none" stroke="{"black" if inverted else "white"}" stroke-width="3"/>')
    for i,line in enumerate(festive_header.lines(event)):
        size=min(25,max(12,int(380/max(1,len(line))*1.5)))
        text(1024,110+i*30,line,size,True,'end')
    text(1024,181,data.get('sun') or 'Sun unavailable',34,True,'end');p.append('<path d="M48 214H1024" stroke="black" stroke-width="3"/>')
    if halloween:p.append(halloween_art.header_decor())
    weather=data.get('weather')
    if weather:
        current=weather.get('current') or {};facts=weather.get('facts') or {};daily=weather.get('daily') or [];hours=weather.get('hours') or []
        icon(current.get('condition'),48,248,125);text(200,350,str(current.get('temperature','–'))+'°',110,True);condition=str(current.get('condition') or 'Unavailable');condition=CONDITION_NAMES.get(condition,condition.replace('-',' ').replace('_',' ').capitalize());text(48,397,condition,37,True)
        day=next((d for d in daily if d.get('label')=='Today'),{})
        if day:text(48,438,'Today '+str(day.get('low','–'))+'° – '+str(day.get('high','–'))+'°',28)
        sky=str(facts.get('sky','Unavailable'));sky_label='UV INDEX' if sky.startswith('UV ') else 'CLOUD COVER';sky_value=sky.removeprefix('UV ').removeprefix('Cloud ')
        for i,(key,value) in enumerate((('WIND',facts.get('wind','Unavailable')),('NEXT RAIN',next_rain_text(facts.get('next_rain','Unavailable'))),(sky_label,sky_value))):
            label(425,269+i*74,key,19);text(425,300+i*74,value,28,True)
        bearing=facts.get('bearing')
        if isinstance(bearing,(int,float)) and not isinstance(bearing,bool) and math.isfinite(bearing):
            p.append(f'<g aria-label="Wind direction" transform="translate(585 289) rotate({(bearing+180)%360:g})"><path d="M0,-16 L11,0 L4,0 L4,16 L-4,16 L-4,0 L-11,0 Z" fill="black"/></g>')
        future=[x for x in daily if x.get('label')!='Today'][:2]
        for i,day in enumerate(future):
            y=238+i*118;box(640,y,384,98);name=day.get('label','')
            if name=='Next day':name=(today+datetime.timedelta(days=2)).strftime('%A')
            icon(day.get('condition'),935,y+10,70);text(662,y+43,name,30,True);rain=day.get('precipitation');forecast=str(day.get('low','–'))+'–'+str(day.get('high','–'))+'°'
            forecast+=(' '+num(rain)+' mm rain') if isinstance(rain,(int,float)) and not isinstance(rain,bool) and rain>=0.1 else ' dry'
            text(662,y+79,forecast,23)
        box(48,478,976,190)
        for i,h in enumerate(hours[:6]):
            x=130+i*163;text(x,520,h.get('time',''),25,True,'middle');icon(h.get('condition'),x-30,532,60);text(x,630,str(h.get('temperature','–'))+'°',36,True,'middle')
            rain=h.get('precipitation')
            if isinstance(rain,(int,float)) and not isinstance(rain,bool) and rain>0.1:text(x,655,num(rain)+' mm',17,anchor='middle')
            if i:p.append(f'<path d="M{x-80} 496v152" stroke="#aaa" stroke-width="2"/>')
    else:text(48,330,'Weather unavailable' if data.get('weather_configured') else 'Weather not configured',30)
    game=next((x for x in active if x.get('kind')=='game'),None)
    if game:
        box(640,238,384,216);art=game.get('art')
        if isinstance(art,str) and ART.fullmatch(art):p.append(f'<image href="{art}" x="653" y="251" width="140" height="190" preserveAspectRatio="xMidYMid meet" filter="url(#gray)"/>')
        text(806,278,'NOW PLAYING',18,True)
        lines=textwrap.wrap(str(game.get('detail') or game.get('label') or 'Game'),width=14)
        if len(lines)>3:lines=lines[:2]+[' '.join(lines[2:])]
        for i,line in enumerate(lines):text(806,316+i*32,line,24,True,max_width=200)
    media=next((x for x in active if x.get('kind')=='media'),None)
    if media:
        box(48,478,976,190);art=media.get('art')
        if isinstance(art,str) and ART.fullmatch(art):p.append(f'<image href="{art}" x="62" y="493" width="160" height="160" preserveAspectRatio="xMidYMid meet" filter="url(#gray)"/>')
        parts=str(media.get('detail') or '').split(' · ',1);text(245,524,'NOW PLAYING',20,True);text(245,574,parts[0] or str(media.get('label') or 'Playing'),30,True,max_width=755);text(245,620,parts[1] if len(parts)>1 else '',23,max_width=755)
    label(48,707,'HAUNTED HOUSE' if halloween else 'HOUSE',25)
    alarm=next((x for x in panels if x.get('label')=='Alarm'),{});home=next((x for x in panels if x.get('label')=='Home'),{});doors=next((x for x in panels if str(x.get('label','')).startswith('Doors')),{});heat=next((x for x in panels if str(x.get('label','')).startswith(('Heat','Heating','Floors'))),{})
    for i,(title,value) in enumerate((('ALARM',alarm.get('detail')),('HOME',home.get('detail')),('DOORS',cap(doors.get('label','').removeprefix('Doors '))),('HEAT',cap(heat.get('label','').removeprefix('Heat '))))):
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
            shown=value or 'Unavailable'
            if inverted:label(x+20,809,shown,23,'white',max_width=192)
            else:text(x+20,809,shown,23,True,max_width=192)
        if title=='HEAT' and heat.get('detail'):text(x+20,843,heat['detail'],18,max_width=192)
        if title=='DOORS' and doors.get('detail'):
            if inverted:label(x+20,843,doors['detail'],18,'white',max_width=192)
            else:text(x+20,843,doors['detail'],18,max_width=192)
        elif title=='DOORS' and doors.get('label')=='Doors locked':text(x+20,843,'all locked',18,max_width=192)
    if charging and isinstance(data.get('cooking'),dict) and 16<=today.hour<19:
        box(48,726,232,140,'black');label(164,786,'COOK',30,'white','middle');label(164,827,'DINNER',30,'white','middle')
    lights=next((x for x in panels if x.get('label')=='Lights on'),{})
    label(48,911,'LIGHTS ON',25)
    floors=lights.get('floors')
    if isinstance(floors,list) and floors:
        for i,floor in enumerate(floors[:4]):   # two columns of floor rows, as on the original Kindle
            x=48+(i%2)*488;y=956+(i//2)*44;rooms=', '.join(floor.get('rooms') or []) or '–'
            label(x,y,str(floor.get('floor','')),26);text(x+140,y,rooms if len(rooms)<=26 else rooms[:25]+'…',26,max_width=340)
    else:text(48,958,lights.get('detail') or 'Unavailable',26,max_width=950)
    if charging:
        p.append('<rect x="270" y="884" width="140" height="36" rx="18" fill="black"/><rect x="880" y="884" width="144" height="36" rx="18" fill="black"/>');label(340,909,'ALL OFF',18,'white','middle');label(952,909,'MENU',18,'white','middle')
        if (today.month,today.day)==(10,31) and data.get('allow_halloween_toggle',True) and data.get('halloween_mode') in ('on','off'):
            on=data['halloween_mode']=='on';p.append(f'<rect x="716" y="882" width="148" height="40" rx="20" fill="{"white" if on else "black"}" stroke="black" stroke-width="3"/>');label(790,910,'PARTY',20,'black' if on else 'white','middle')
    p.append('<path d="M48 1031H1024" stroke="black" stroke-width="3"/>')
    energy=next((x for x in panels if x.get('label')=='Energy'),{})
    energy_detail=energy.get('detail') or 'Energy unavailable'
    energy_left=energy.get('kindle_left') or energy_detail;energy_cost=energy.get('kindle_cost') or ''
    if str(energy_detail).startswith('Saving'):
        box(48,1040,976,48,'black');label(65,1073,energy_left,22,'white')
    else:
        text(48,1065,energy_left,25,True,max_width=560 if energy_cost else 976)
        if energy_cost:text(1024,1065,energy_cost,25,True,'end',max_width=400)
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
            p.append(f'<g transform="translate({x} 573)"><path d="M0,-50 L48,35 L-48,35 Z" fill="white" stroke="white" stroke-width="7" stroke-linejoin="round"/><text x="0" y="25" text-anchor="middle" font-family="Roboto, sans-serif" font-size="58" font-weight="bold">!</text></g>')
        for i,item in enumerate(warning):
            if len(warning)<=2:x,y,limit,size=536,565+(i*48 if len(warning)==2 else 14),38,30
            else:x,y,limit,size=(350 if i%2==0 else 722),555+(i//2)*48,20,23
            value=clean(item,70);fit=f' textLength="{(650 if len(warning)<=2 else 310)}" lengthAdjust="spacingAndGlyphs"' if len(str(item))>limit else ''
            p.append(f'<text x="{x}" y="{y}" fill="white" font-family="Roboto, sans-serif" font-size="{size}" font-weight="bold" text-anchor="middle"{fit}>{value}</text>')
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
            lines=textwrap.wrap((ENVELOPE if task.get('display_group')=='message' else '')+str(task.get('title','')),width=25)
            if len(lines)>2:lines=lines[:1]+[' '.join(lines[1:])]
            for j,line in enumerate(lines):label(x+24,1201+j*32,line,23,fill,max_width=434)
            if task.get('detail'):label(x+24,1277,detail_text(task['detail']),19,fill,max_width=434)
        rooms=[x for x in panels if x.get('kind')=='room'][:10]
        if rooms:
            label(48,1334,'ROOMS',25)
            for i,room in enumerate(rooms):
                x=97+i*97;text(x,1378,room.get('label',''),14,anchor='middle',max_width=92);text(x,1424,str(room.get('detail','–')),20,True,'middle',max_width=92)
    else:
        h,pitch,font={3:(87,99,29),4:(61,75,27),5:(50,59,24)}[min(len(rows),5)]
        for i,task in enumerate(rows[:5]):
            y=1150+i*pitch;urgent=task.get('urgent') is True;fill='white' if urgent else 'black';box(48,y,976,h,'black' if urgent else 'white')
            title=(ENVELOPE if task.get('display_group')=='message' else '')+str(task.get('title',''));detail=detail_text(task.get('detail') or '')
            label(70,y+h//2+font//3,title,font,fill,max_width=600 if detail else 930)
            if detail:label(1000,y+h//2+7,detail,18,fill,'end',max_width=300)
    weather=data.get('weather') or {}
    current=weather.get('current') or {}
    if halloween:p.append(halloween_art.body_decor())
    p.append(cat_render.render(timestamp,current.get('condition'),halloween))
    return (''.join(p)+'</svg>').encode('utf-8')
