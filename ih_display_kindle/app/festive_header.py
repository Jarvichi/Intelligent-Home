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
    'other':'Roboto',
}

def style(event):
    event=event if isinstance(event,dict) else {}
    inverted=bool(event.get('active') and event.get('event_today'))
    return inverted,FONTS.get(event.get('theme'),'Roboto') if inverted else 'Roboto'

def lines(event):
    if not isinstance(event,dict) or not event.get('active'):return []
    days=event.get('countdown_days');name=event.get('countdown_name')
    if type(days) is int and days>0 and isinstance(name,str) and name:
        icon={'Christmas':'🎄','Halloween':'🎃','Easter':'🐣','Pancake Day':'🥞'}.get(name,'🎁')
        if name=='Christmas' and days>1:return [str(days)+' sleeps:',icon+' '+name]
        return [('Tomorrow:' if days==1 else str(days)+' days:'),icon+' '+name]
    detail=str(event.get('detail') or '')
    icon=next((symbol for phrase,symbol in (
        ('Haunted house','👻'),('Christmas','🎄'),('Boxing Day','🎁'),('New Year’s Eve','🥂'),
        ('New Year','🎆'),('Halloween','🎃'),('Bonfire','🎆'),('Valentine','❤'),
        ('Pancake','🥞'),('Mother','💐'),('Good Friday','🐣'),('Easter Monday','🐰'),
        ('Easter','🐣'),('Father','👔'),('birthday','🎂')) if phrase in detail),'')
    return [(icon+' ' if icon else '')+detail]
