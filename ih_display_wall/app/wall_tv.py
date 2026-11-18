"""TV layout based on the established wall picture, using selected read-only data."""
import datetime
import re
import textwrap
import time
from render import clean

ART=re.compile(r'^data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]{1,700000}$')
CONDITIONS={'partlycloudy':'Partly cloudy','clear-night':'Clear','lightning-rainy':'Thunder & rain','snowy-rainy':'Sleet','windy-variant':'Windy','sunny':'Sunny','cloudy':'Cloudy','rainy':'Showers','pouring':'Heavy rain','fog':'Fog','snowy':'Snow','hail':'Hail','lightning':'Thunder','windy':'Windy','exceptional':'Warning'}

def icon(condition,x,y,size):
    condition=str(condition or '').lower();night=condition=='clear-night';sun=('sun' in condition or 'clear' in condition or 'partly' in condition);cloud=('cloud' in condition or 'rain' in condition or 'snow' in condition)
    bits=[f'<g transform="translate({x} {y}) scale({size/100})">']
    if night:
        bits.append('<path d="M52 10 A31 31 0 1 0 82 62 A27 27 0 0 1 52 10 Z" fill="#f6df9b"/>')
    elif sun:
        bits.append('<circle cx="38" cy="33" r="22" fill="#ffb02e"/>')
        for a,b,c,d in ((38,0,38,7),(7,32,14,32),(17,10,22,15),(61,9,56,15),(7,55,14,50)):
            bits.append(f'<line x1="{a}" y1="{b}" x2="{c}" y2="{d}" stroke="#ffb02e" stroke-width="6"/>')
    if cloud or not sun:
        bits.append('<path d="M21 75 C5 75 4 53 20 49 C21 31 44 25 55 40 C71 31 90 42 88 57 C101 62 97 76 84 76 Z" fill="#b9cbd0"/>')
    if 'rain' in condition or 'pour' in condition:
        bits.append('<path d="M30 83l-5 10 M52 83l-5 10 M74 83l-5 10" stroke="#4eb9ee" stroke-width="5" stroke-linecap="round"/>')
    if 'snow' in condition:bits.append('<text x="35" y="97" font-size="27" fill="#ffffff">✱</text>')
    return ''.join(bits)+'</g>'

def number(value):return '–' if value is None else str(value)

def svg(profile,data,alerts=None,now=None):
    if not isinstance(profile,dict) or profile.get('state')!='ready':raise ValueError('Profile unavailable')
    width,height=profile.get('width'),profile.get('height')
    if any(type(v) is not int or not 1<=v<=4096 for v in (width,height)):raise ValueError('Invalid dimensions')
    timestamp=time.time() if now is None else now;today=datetime.datetime.fromtimestamp(timestamp).astimezone()
    mode=data.get('night_mode','auto');deep=mode=='deep' or (mode=='auto' and (today.hour>=23 or today.hour<6))
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid meet">',
           '<g opacity="0.85">' if deep else '<g>',
           '<rect width="1920" height="1080" fill="#080a0d"/>']
    bg=data.get('background')
    if isinstance(bg,str) and ART.fullmatch(bg):parts.append(f'<image href="{bg}" x="0" y="0" width="1920" height="1080" preserveAspectRatio="xMidYMid slice"/>')
    parts.append('<rect width="1920" height="1080" fill="#000000" opacity="0.68"/>')
    parts.append('<rect x="80" y="58" width="690" height="296" rx="18" fill="#000000" opacity="0.82"/>')
    parts.append(f'<text x="116" y="154" fill="#ffffff" font-family="sans-serif" font-size="72" font-weight="bold">{clean(today.strftime("%A"),14)}</text>')
    parts.append(f'<text x="116" y="201" fill="#aaa9ab" font-family="sans-serif" font-size="32">{clean(str(today.day)+" "+today.strftime("%B"),22)}</text>')
    panels=data.get('panels') or [];active=[p for p in panels if isinstance(p,dict) and p.get('active')]
    occasion=next((p for p in active if p.get('slot')=='occasion'),None)
    if occasion:
        detail=str(occasion.get('detail') or '');countdown=re.fullmatch(r'(.+?) in (\d+) days',detail);tomorrow=re.fullmatch(r'(.+?) tomorrow!',detail)
        if countdown:detail=countdown.group(2)+' days: '+('🎁 ' if 'birthday' in countdown.group(1).lower() else '')+countdown.group(1)
        elif tomorrow:detail='Tomorrow: '+('🎁 ' if 'birthday' in tomorrow.group(1).lower() else '')+tomorrow.group(1)
        elif 'birthday today' in detail.lower():detail='🎂 '+detail
        parts.append(f'<text x="138" y="265" fill="#ffffff" font-family="sans-serif" font-size="28" font-weight="bold">{clean(detail,38)}</text>')
    sun=data.get('sun') or ('Sun unavailable' if data.get('sun_configured') else '')
    if sun:parts.append(f'<text x="116" y="319" fill="#aaa9ab" font-family="sans-serif" font-size="25">{clean(sun,24)}</text>')
    presence=next((p for p in active if p.get('label')=='Home'),None)
    if presence:
        persons=presence.get('persons') or []
        if persons:
            for i,person in enumerate(persons[:5]):
                x=1300+i*90;art=person.get('art')
                parts.append(f'<circle cx="{x+38}" cy="92" r="38" fill="#27342f" stroke="#e4f0e7" stroke-width="2"/>')
                if isinstance(art,str) and ART.fullmatch(art):
                    parts.append(f'<clipPath id="person{i}"><circle cx="{x+38}" cy="92" r="36"/></clipPath><image href="{art}" x="{x+2}" y="56" width="72" height="72" preserveAspectRatio="xMidYMid slice" clip-path="url(#person{i})"/>')
                else:parts.append(f'<text x="{x+38}" y="103" text-anchor="middle" fill="#ffffff" font-family="sans-serif" font-size="28">{clean(str(person.get("name") or "?")[:1])}</text>')
        else:parts.append(f'<text x="1300" y="100" fill="#ffffff" font-family="sans-serif" font-size="22">{clean(presence.get("detail"),24)}</text>')
    parts.append('<rect x="80" y="385" width="690" height="535" rx="18" fill="#000000" opacity="0.82"/>')
    weather=data.get('weather')
    if not weather:
        label='Weather unavailable' if data.get('weather_configured') else 'Weather not configured'
        parts.append(f'<text x="116" y="450" fill="#ffca8a" font-family="sans-serif" font-size="28">{label}</text>')
    else:
        current=weather.get('current') or {};daily=weather.get('daily') or [];facts=weather.get('facts') or {}
        parts.append(icon(current.get('condition'),118,422,120))
        parts.append(f'<text x="270" y="505" fill="#ff8758" font-family="sans-serif" font-size="88" font-weight="bold">{clean(str(current.get("temperature","–"))+"°",8)}</text>')
        condition=str(current.get('condition') or 'Unavailable');label=CONDITIONS.get(condition,condition.replace('_',' ').capitalize())
        parts.append(f'<text x="465" y="460" fill="#ffffff" font-family="sans-serif" font-size="34" font-weight="bold">{clean(label,20)}</text>')
        day=next((row for row in daily if row.get('label')=='Today'),None)
        if day:parts.append(f'<text x="465" y="499" fill="#aaa9ab" font-family="sans-serif" font-size="25">Today {clean(number(day.get("low")))}° – {clean(number(day.get("high")))}°</text>')
        if not current.get('current'):parts.append('<text x="465" y="529" fill="#ffca8a" font-family="sans-serif" font-size="18">Next forecast</text>')
        sky=str(facts.get('sky','Unavailable'));sky_label='Cloud cover';sky_value=sky.removeprefix('Cloud ')
        if sky.startswith('UV '):
            sky_label='UV index';value=sky.removeprefix('UV ')
            try:
                index=float(value);level='low' if index<3 else 'moderate' if index<6 else 'high' if index<8 else 'very high';sky_value=value+' · '+level
            except ValueError:sky_value='Unavailable'
        for x,label,value in ((116,'Wind',facts.get('wind','Unavailable')),(252,'Next rain',facts.get('next_rain','Unavailable')),(405,sky_label,sky_value)):
            parts.append(f'<text x="{x}" y="557" fill="#d5d5d5" font-family="sans-serif" font-size="23">{label}</text><text x="{x}" y="591" fill="#ffffff" font-family="sans-serif" font-size="26" font-weight="bold">{clean(value,18)}</text>')
        for i,hour in enumerate((weather.get('hours') or [])[:6]):
            x=130+i*105
            parts.append(f'<text x="{x}" y="650" fill="#9b9b9d" font-family="sans-serif" font-size="19">{clean(hour.get("time"),8)}</text>')
            parts.append(icon(hour.get('condition'),x,657,52))
            parts.append(f'<text x="{x}" y="735" fill="#ffffff" font-family="sans-serif" font-size="25" font-weight="bold">{clean(str(hour.get("temperature","–"))+"°",8)}</text>')
            rain=hour.get('precipitation')
            if isinstance(rain,(int,float)) and not isinstance(rain,bool) and rain>0.1:parts.append(f'<text x="{x}" y="760" fill="#ff9a57" font-family="sans-serif" font-size="16">{clean(rain)} mm</text>')
        future=[day for day in daily if day.get('label')!='Today'][:2]
        if not future:parts.append('<text x="130" y="840" fill="#ffc67f" font-family="sans-serif" font-size="20">Daily forecast unavailable</text>')
        for i,day in enumerate(future):
            x=130+i*315;label=day.get('label','')
            if label=='Next day':label=(today+datetime.timedelta(days=2)).strftime('%A')
            parts.append(f'<text x="{x}" y="840" fill="#ffffff" font-family="sans-serif" font-size="28" font-weight="bold">{clean(label,15)}</text>')
            parts.append(icon(day.get('condition'),x+180,810,42))
            rain=day.get('precipitation');detail=number(day.get('low'))+'° – '+number(day.get('high'))+'°'
            if isinstance(rain,(int,float)) and not isinstance(rain,bool) and rain>=0.1:detail+='  '+str(round(rain,1))+' mm rain'
            parts.append(f'<text x="{x}" y="872" fill="#aaa9ab" font-family="sans-serif" font-size="20">{clean(detail,28)}</text>')
    parts.append('<rect x="807" y="175" width="1015" height="690" rx="18" fill="#000000" opacity="0.84"/>')
    warnings=[];incomplete=False
    if alerts is None or not alerts.get('configured'):incomplete=True
    else:warnings=alerts.get('alerts') or [];incomplete=alerts.get('complete') is not True
    important=[p for p in active if p.get('urgent')]
    banners=[str(x.get('title') or '') for x in warnings]+[str(p.get('label') or '')+(': '+str(p.get('detail')) if p.get('detail') else '') for p in important]
    if incomplete:banners.append('Alert sources incomplete')
    safety=bool(banners)
    if not banners:
        banners=[str(p.get('label') or '')+(': '+str(p.get('detail')) if p.get('detail') else '') for p in active if p.get('kind') in ('cooking','media','game')]
    tasks=data.get('tasks');partial=data.get('tasks_partial') or any(not p.get('available') for p in panels)
    y=215
    if banners:
        pages=max(1,(len(banners)+3)//4);page=int(timestamp//300)%pages
        parts.append(f'<rect x="837" y="195" width="955" height="92" rx="12" fill="{"#781b21" if safety else "#26342f"}"/>')
        for i,banner in enumerate(banners[page*4:page*4+4]):parts.append(f'<text x="{860+(i%2)*460}" y="{228+(i//2)*35}" fill="#ffffff" font-family="sans-serif" font-size="22" font-weight="bold">{clean(banner,38)}</text>')
        if pages>1:parts.append(f'<text x="1735" y="220" fill="#ffffff" font-family="sans-serif" font-size="16">{page+1}/{pages}</text>')
        y=315
    count=len(tasks) if isinstance(tasks,list) else 0;per_page=4 if banners else 5;pages=max(1,(count+per_page-1)//per_page);page=int(timestamp//60)%pages
    heading='TO DO'+(f' · {page+1}/{pages}' if pages>1 else '')
    parts.append(f'<text x="837" y="{y}" fill="#aaa9ab" font-family="sans-serif" font-size="20" font-weight="bold" letter-spacing="2">{heading}</text>')
    if partial:parts.append(f'<text x="1220" y="{y}" fill="#ffc67f" font-family="sans-serif" font-size="18">Some sources unavailable</text>')
    if tasks is None:
        label='Task list unavailable' if data.get('tasks_configured') else 'Task sources not configured'
        parts.append(f'<text x="875" y="{y+55}" fill="#ddd" font-family="sans-serif" font-size="25">{label}</text>')
    elif not tasks and not partial:parts.append(f'<text x="875" y="{y+55}" fill="#ddd" font-family="sans-serif" font-size="25">No items from selected sources</text>')
    else:
        for i,task in enumerate(tasks[page*per_page:page*per_page+per_page]):
            top=y+28+i*112;title=task.get('title','') if isinstance(task,dict) else str(task);detail=task.get('detail','') if isinstance(task,dict) else ''
            if isinstance(task,dict) and task.get('urgent') is True:parts.append(f'<rect x="837" y="{top-6}" width="955" height="95" rx="12" fill="#4a0c10"/><rect x="837" y="{top-6}" width="9" height="95" rx="4" fill="#ff4d4d"/>')
            lines=textwrap.wrap(str(title),width=45,max_lines=2,placeholder='…') or ['']
            for line_no,line in enumerate(lines):parts.append(f'<text x="875" y="{top+32+line_no*38}" fill="#ffffff" font-family="sans-serif" font-size="33" font-weight="bold">{clean(line,45)}</text>')
            if detail and len(lines)==1:parts.append(f'<text x="875" y="{top+64}" fill="#aaaaaa" font-family="sans-serif" font-size="21">{clean(detail,70)}</text>')
    parts.append(f'<text x="1815" y="1035" text-anchor="end" fill="#aaaaaa" font-family="sans-serif" font-size="20">Updated {today.strftime("%H:%M")}</text>')
    parts.append('</g></svg>')
    return ''.join(parts).encode('utf-8')
