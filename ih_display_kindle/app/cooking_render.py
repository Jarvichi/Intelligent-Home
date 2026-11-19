"""Render the owner-provided cooking snapshot; preview has no actions."""
from render import clean

def svg(cooking,alerts=None):
    p=['<svg xmlns="http://www.w3.org/2000/svg" width="1072" height="1448" viewBox="0 0 1072 1448"><rect width="1072" height="1448" fill="white"/>']
    def text(x,y,value,size=30,bold=False,fill='black',anchor='start',max_width=None):
        if max_width and len(str(value))*size*.62>max_width:size=max(12,int(max_width/(len(str(value))*.62)))
        p.append(f'<text x="{x}" y="{y}" fill="{fill}" font-family="sans-serif" font-size="{size}" font-weight="{"bold" if bold else "normal"}" text-anchor="{anchor}">{clean(value,200)}</text>')
    def box(x,y,w,h,fill='white',radius=20):
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="black" stroke-width="3"/>')
    def pill(x,y,w,label):
        box(x,y,w,40,'white',20);text(x+w/2,y+27,label,18,True,'black','middle')
    life=[str(x.get('title') or '') for x in (alerts.get('alerts') or []) if isinstance(x,dict) and x.get('kind') in ('smoke','co')] if isinstance(alerts,dict) else []
    status='Alert sources not configured' if isinstance(alerts,dict) and alerts.get('configured') is False else 'Alert sources unavailable' if alerts is None or (isinstance(alerts,dict) and not alerts.get('complete')) else ''
    warnings=life[:2]+([status] if status else [])
    text(48,100,cooking.get('title') or 'Cooking',46,True);text(48,152,'Eating at '+str(cooking.get('eat') or '—'),30,True);pill(360,122,232,'STOP COOKING')
    box(640,40,384,140);text(832,82,'OVEN',22,True,anchor='middle');target=cooking.get('oven_target');text(832,148,(str(target)+'° target') if isinstance(target,(float,int)) and not isinstance(target,bool) else 'Unavailable',30,True,anchor='middle')
    next_rows=cooking.get('next') if isinstance(cooking.get('next'),list) else [];n=min(3,len(next_rows));height=max(150,150+max(0,n-1)*60)
    box(40,206,992,height,'black');text(76,258,'NEXT',22,True,'white')
    if not n:text(76,320,'No upcoming steps',28,True,'white')
    for i,row in enumerate(next_rows[:3]):
        if not isinstance(row,dict):continue
        y=320+i*60;text(76,y,str(row.get('at') or '')+' '+str(row.get('text') or ''),24,True,'white',max_width=500)
        for x,w,caption in ((596,92,'DONE'),(696,92,'DID IT'),(796,84,'ON IT'),(888,60,'−5'),(956,60,'+5')):pill(x,286+i*60,w,caption)
    line_y=max(392,206+height+36);p.append(f'<path d="M48 {line_y}H1024" stroke="black" stroke-width="3"/>')
    steps=cooking.get('steps') if isinstance(cooking.get('steps'),list) else []
    for i,row in enumerate(steps[:9]):
        if not isinstance(row,dict):continue
        y=line_y+48+i*54
        if y>=990:break
        text(60,y,row.get('at',''),23,True);text(210,y,('✓ ' if row.get('done') else '')+str(row.get('text') or ''),23,max_width=800)
    p.append('<path d="M48 1000H1024" stroke="black" stroke-width="3"/>');text(48,1032,'PREPARATION',22,True)
    prep=cooking.get('prep') if isinstance(cooking.get('prep'),list) else []
    for i,row in enumerate(prep[:5]):
        if not isinstance(row,dict):continue
        y=1046+i*75;box(48,y,976,61,radius=14);box(66,y+14,32,32,radius=6);text(118,y+42,row.get('text',''),24,max_width=875)
    # Safety stays above the cooking layer even when an oven or title occupies the header.
    if warnings:
        height=30*len(warnings)+6;box(0,0,1072,height,'black',0)
        for i,warning in enumerate(warnings):text(536,25+i*30,warning,20,True,'white','middle')
    return (''.join(p)+'</svg>').encode('utf-8')
