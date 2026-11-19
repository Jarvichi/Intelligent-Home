"""Read-only Kindle meal menu projected from the Cooking data owner."""
import datetime
import hashlib
import json
import math
import recipeadmin

DAYS=('mon','tue','wed','thu','fri','sat','sun')

def ready_minutes(now,lead):
    """Round the earliest realistic serving time up to the next five minutes."""
    return ((now.hour*60+now.minute+lead+2+4)//5)*5

def read(store,now=None,page=0):
    now=now or datetime.datetime.now();plan,source=store.plan()
    if not isinstance(plan,dict):raise ValueError('Invalid meal plan')
    with recipeadmin._WRITE_LOCK:
        return _recipe_snapshot(store,plan,source,now,page)

def _recipe_snapshot(store,plan,source,now,page):
    data=recipeadmin.listing(store.data)
    by_slug={};aliases={}
    for row in data:
        slug=row.get('id');title=row.get('title');minutes=row.get('minutes')
        if not isinstance(slug,str) or not isinstance(title,str) or not isinstance(minutes,(int,float)) or isinstance(minutes,bool) or not math.isfinite(minutes):continue
        item={'slug':slug,'title':title[:120],'lead':max(0,min(int(minutes),1440))}
        by_slug[slug]=item
        aliases[slug.casefold()]=slug;aliases[title.casefold()]=slug
        full=store.recipe(slug)
        if isinstance(full,dict):
            for name in (full.get('name'),full.get('title')):
                if isinstance(name,str):aliases.setdefault(name.casefold(),slug)
            item['revision']=full.get('_revision','')
    def resolve(value):return aliases.get(str(value or '').strip().casefold())
    today=DAYS[now.weekday()];tonight=resolve(plan.get(today));week=[]
    for day in DAYS:
        slug=resolve(plan.get(day))
        if slug and slug not in week:week.append(slug)
    first=([tonight] if tonight else [])+[x for x in week if x!=tonight]
    rest=[x for x in by_slug if x not in first];rest.sort(key=lambda x:(by_slug[x]['lead']>120,x))
    order=first+rest;pages=max(1,(len(order)+7)//8)
    page=max(0,min(int(page),pages-1));items=[]
    for slug in order[page*8:page*8+8]:
        item=dict(by_slug[slug]);minutes=ready_minutes(now,item['lead'])
        item['eat']='%02d:%02d'%((minutes//60)%24,minutes%60);item['tonight']=slug==tonight;items.append(item)
    # The revision describes identities and recipes, not the moving suggested times.
    basis={'plan':plan,'source':source,'recipes':[by_slug[x] for x in order]}
    revision=hashlib.sha256(json.dumps(basis,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'revision':revision,'page':page,'pages':pages,'items':items,'source':source}
