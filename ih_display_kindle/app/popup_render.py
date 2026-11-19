"""Render read-only cached-screen previews from owner-provided snapshots."""
import datetime
import re
from render import clean

ART=re.compile(r'^data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]{1,700000}$')

def svg(screen,data,now=None):
    now=now or datetime.datetime.now().astimezone()
    if screen not in ('dinner','home','birthdays'):raise ValueError('Invalid screen')
    p=['<svg xmlns="http://www.w3.org/2000/svg" width="1072" height="1448" viewBox="0 0 1072 1448"><defs><filter id="gray"><feColorMatrix type="saturate" values="0"/></filter><pattern id="home-stripes" width="18" height="18" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="18" stroke="#bbb" stroke-width="4"/></pattern></defs><rect width="1072" height="1448" fill="white"/>']
    def text(x,y,value,size=30,bold=False,fill='black',anchor='start'):
        p.append(f'<text x="{x}" y="{y}" fill="{fill}" font-family="sans-serif" font-size="{size}" font-weight="{"bold" if bold else "normal"}" text-anchor="{anchor}">{clean(value,75)}</text>')
    def box(x,y,w,h,fill='white',radius=18):p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="black" stroke-width="3"/>')
    if screen=='dinner':
        text(48,84,"🍽 What's for dinner?",56,True)
        selected=data.get('selected_eat','ASAP') if isinstance(data,dict) else 'ASAP'
        if selected not in ('ASAP','12:30','13:30','18:30'):selected='ASAP'
        for i,caption in enumerate(('As soon as ready','12:30 lunch','13:30 lunch','18:30 dinner')):
            x=48+i*247;on=selected==('ASAP','12:30','13:30','18:30')[i];box(x,112,235,64,'black' if on else 'white',32);text(x+117,153,caption,27,True,'white' if on else 'black','middle')
        items=data.get('items') if isinstance(data,dict) and isinstance(data.get('items'),list) else []
        if not isinstance(data,dict):text(76,250,'Cooking menu unavailable',26)
        elif not items:text(76,250,'No recipes available',26)
        for i,item in enumerate(items[:8]):
            if not isinstance(item,dict):continue
            y=200+i*128;box(48,y,976,104,'black' if item.get('tonight') is True else 'white');fill='white' if item.get('tonight') is True else 'black'
            text(76,y+48,str(item.get('title') or '')[:35],38,True,fill);text(76,y+86,('TONIGHT’S PLAN · ' if item.get('tonight') is True else '')+'ready from '+str(item.get('eat') or '—')+' · '+str(item.get('lead') or 0)+' min',26,False,fill);text(1000,y+66,'COOK ›',30,True,fill,'end')
        pages=data.get('pages',1) if isinstance(data,dict) else 1;page=data.get('page',0) if isinstance(data,dict) else 0
        if type(pages) is int and pages>1:
            text(1024,84,f'{int(page)+1}/{pages}',34,True,anchor='end')
            for x,w,caption in ((48,240,'‹ PREV'),(312,448,'BACK'),(784,240,'NEXT ›')):box(x,1240,w,104,'white',52);text(x+w/2,1305,caption,40,True,'black','middle')
        else:box(48,1240,976,104,'white',52);text(536,1305,'BACK',40,True,'black','middle')
    elif screen=='home':
        p.append('<rect width="1072" height="1448" fill="url(#home-stripes)"/>');box(88,300,896,860,radius=40);text(140,400,"Who's home",64,True)
        members=data.get('members') if isinstance(data,dict) and isinstance(data.get('members'),list) else []
        if not members:text(140,485,'Presence unavailable',28)
        members=sorted(members,key=lambda x:0 if isinstance(x,dict) and x.get('home') is True else 1)
        for i,member in enumerate(members[:3]):
            if not isinstance(member,dict):continue
            y=450+i*180;art=member.get('art')
            if isinstance(art,str) and ART.fullmatch(art):p.append(f'<image href="{art}" x="140" y="{y}" width="132" height="132" preserveAspectRatio="xMidYMid slice" filter="url(#gray)"/>')
            else:box(140,y,132,132,'#ddd');text(206,y+85,str(member.get('name') or '?')[:1],44,True,anchor='middle')
            text(306,y+60,member.get('name') or 'Unknown',48,True,'#666' if member.get('home') is False else 'black');status='Home' if member.get('home') is True else 'Out' if member.get('home') is False else 'Unavailable'
            try:
                when=datetime.datetime.fromisoformat(str(member.get('transition') or '').replace('Z','+00:00')).astimezone()
                if when.date()==now.date():display='today '+when.strftime('%H:%M')
                elif (now-when).total_seconds()<6*86400:display=when.strftime('%a %H:%M')
                else:display=when.strftime('%a %d %b %H:%M')
                status+=' · '+('arrived ' if member.get('home') is True else 'left ')+display
            except (ValueError,TypeError,OverflowError,OSError):pass
            text(306,y+108,status,32)
        box(140,1010,792,104,'black',52);text(536,1075,'DONE',40,True,'white','middle')
    else:
        text(48,84,'🎂 Birthdays',56,True)
        items=data.get('items') if isinstance(data,dict) and isinstance(data.get('items'),list) else []
        if not isinstance(data,dict):text(76,190,'Birthdays unavailable',26)
        elif not items:text(76,190,'No upcoming birthdays',26)
        for i,item in enumerate(items[:8]):
            if not isinstance(item,dict):continue
            y=140+i*128;today=item.get('today') is True;fill='white' if today else 'black';box(48,y,976,104,'black' if today else 'white');text(76,y+48,item.get('name') or '',38,True,fill);text(76,y+86,item.get('date') or '',26,False,fill);text(1000,y+66,item.get('countdown') or '',30,True,fill,'end')
        box(48,1240,976,104,'white',52);text(536,1305,'BACK',40,True,'black','middle')
    return (''.join(p)+'</svg>').encode('utf-8')
