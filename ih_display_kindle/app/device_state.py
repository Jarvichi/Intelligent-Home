"""Read-only Kindle heartbeat and page offset for the preview."""
import datetime
import math
import time

def read(ha,battery_entity,last_seen_entity,page_entity='',now=None):
    now=time.time() if now is None else now
    result={'configured':bool(battery_entity and last_seen_entity),'fresh':False,'charging':False,'battery':None,'page_offset':0}
    if page_entity:
        page=ha.optional_state(page_entity)
        try:result['page_offset']=int(page.get('state','0')) if isinstance(page,dict) else 0
        except (TypeError,ValueError):pass
    if not result['configured']:return result
    battery=ha.optional_state(battery_entity);seen=ha.optional_state(last_seen_entity)
    if not isinstance(battery,dict) or not isinstance(seen,dict):return result
    try:
        dt=datetime.datetime.fromisoformat(str(seen['state']).replace('Z','+00:00'))
        if dt.tzinfo is None:return result
        age=now-dt.timestamp();level=float(battery['state'])
        if not math.isfinite(level) or not 0<=level<=100 or age<0:return result
        threshold=75*60 if dt.astimezone().hour>=23 or dt.astimezone().hour<6 else 30*60
        result['fresh']=age<threshold
        attrs=battery.get('attributes') if isinstance(battery.get('attributes'),dict) else {}
        charging=attrs.get('charging') is True or str(attrs.get('charging','')).lower()=='true'
        result['charging']=bool(charging and age<30*60)
        if result['fresh']:result['battery']=int(level)
    except (TypeError,ValueError,KeyError,OverflowError,OSError):pass
    return result
