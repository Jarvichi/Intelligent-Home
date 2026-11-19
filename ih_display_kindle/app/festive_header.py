"""Presentation rules for the seasonal Kindle header.

The bundled fonts are open-licensed; household-owned personal-use fonts stay local.
"""

FONTS={
    'halloween':'Creepster',
    'christmas':'Mountains of Christmas',
    'birthday':'Bangers',
    'newyear':'Monoton',
    'bonfire':'Monoton',
    'valentine':'DejaVu Serif',
    'other':'DejaVu Sans',
}

def style(event):
    event=event if isinstance(event,dict) else {}
    inverted=bool(event.get('active') and event.get('event_today'))
    return inverted,FONTS.get(event.get('theme'),'DejaVu Sans') if inverted else 'DejaVu Sans'

def lines(event):
    if not isinstance(event,dict) or not event.get('active'):return []
    days=event.get('countdown_days');name=event.get('countdown_name')
    if type(days) is int and days>0 and isinstance(name,str) and name:
        if name=='Christmas' and days>1:return [str(days)+' sleeps:',name]
        return [('Tomorrow:' if days==1 else str(days)+' days:'),name]
    return [str(event.get('detail') or '')]
