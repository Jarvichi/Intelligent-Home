"""Server-side Kindle actions for configured HA helpers.

Descriptors are captured with the picture and retained on the server; clients
must only send an opaque frame/zone identifier. HA helper writes are guarded by
an optimistic recheck, not an atomic compare-and-set. Other HA writers can
still replace a helper between the check and the service call.
"""
import datetime
import json
import re
import threading
import time
import urllib.error
import http.client
import device_state

ENTITY=re.compile(r'^[a-z][a-z0-9_]*\.[a-z0-9_]+$')

def valid(entity,prefix):
    return isinstance(entity,str) and len(entity)<=255 and entity.startswith(prefix) and bool(ENTITY.fullmatch(entity))

def identity(value):
    if not isinstance(value,dict):return None
    state=value.get('state');changed=value.get('last_changed')
    if not isinstance(state,str) or state in ('unknown','unavailable') or not isinstance(changed,str) or not changed:return None
    return {'state':state,'last_changed':changed}

class KindleActions:
    def __init__(self,ha,settings,alerts=None,refresh=None,clock=None):
        self.ha=ha;self.settings=settings;self.alerts=alerts;self.refresh=refresh
        self.clock=clock;self.lock=threading.Lock()

    def _cfg(self):
        value=self.settings() if callable(self.settings) else self.settings
        return value if isinstance(value,dict) else {}

    def _charging(self,cfg):
        battery=cfg.get('kindle_battery_entity','');seen=cfg.get('kindle_last_seen_entity','')
        if not valid(battery,'sensor.') or not valid(seen,'sensor.'):return False
        return device_state.read(self.ha,battery,seen,now=self.clock() if self.clock else None)['charging'] is True

    def _target(self,cfg,kind,index=None):
        if kind=='message':
            values=cfg.get('kindle_message_entities')
            if not isinstance(values,list) or not 0<len(values)<=12 or type(index) is not int or not 0<=index<len(values):return None
            entity=values[index];prefix='input_text.'
        else:
            key,prefix={'dishwasher':('kindle_dishwasher_entity','input_boolean.'),'wheel':('kindle_wheel_entity','input_text.'),'alert':('kindle_warning_dismissed_entity','input_text.'),'page':('kindle_page_entity','input_text.'),'halloween':('render_halloween_entity','input_boolean.')}.get(kind,('',''))
            entity=cfg.get(key,'')
        return entity if prefix and valid(entity,prefix) else None

    def _lights(self,cfg):
        """Resolve currently-on floor lights using the same HA floor registry as the original action."""
        floors=cfg.get('kindle_lights_off_floors')
        if floors is None:return cfg.get('kindle_lights_off_entities')
        excludes=cfg.get('kindle_lights_off_exclude',[])
        valid_list=lambda x:isinstance(x,list) and len(x)<=20 and all(isinstance(v,str) and re.fullmatch(r'[a-z0-9_]{1,80}',v) for v in x) and len(set(x))==len(x)
        if not valid_list(floors) or not floors or not valid_list(excludes):return None
        expr='('+json.dumps(floors)+" | map('floor_entities') | sum(start=[])) | select('match','^light[.]')"
        if excludes:expr+=" | reject('search',"+json.dumps('|'.join(excludes))+")"
        template='{{ '+expr+" | select('is_state','on') | unique | list | to_json }}"
        try:
            values=self.ha.request('template',{'template':template})
            if not isinstance(values,list) or len(values)>100 or any(not valid(e,'light.') for e in values) or len(set(values))!=len(values):return None
            return sorted(values)
        except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return None

    def describe(self,kind,index=None,delta=None,alert=None,source=None,source_identity=None):
        """Capture a server-only descriptor; return None for unavailable actions."""
        cfg=self._cfg()
        if kind=='refresh':return {'kind':kind} if callable(self.refresh) and self._charging(cfg) else None
        if kind=='lights':
            entities=self._lights(cfg)
            if not self._charging(cfg) or not isinstance(entities,list) or not 0<len(entities)<=100 or any(not valid(x,'light.') for x in entities) or len(set(entities))!=len(entities):return None
            states={e:identity(self.ha.optional_state(e)) for e in entities}
            if any(not x or x['state'] not in ('on','off') for x in states.values()) or not any(x['state']=='on' for x in states.values()):return None
            return {'kind':kind,'states':states}
        target=self._target(cfg,kind,index)
        if not target or (source is not None and source!=target):return None
        expected=identity(self.ha.optional_state(target))
        if not expected:return None
        # Task feeds carry the state used for the picture. Re-reading the same
        # entity alone can accidentally bind an old row to a newer event.
        if source is not None and (identity(source_identity)!=source_identity or source_identity!=expected):return None
        descriptor={'kind':kind,'target':target,'expected':expected}
        if kind=='message':
            if not expected['state'].strip() or not self._charging(cfg):return None
            descriptor['index']=index
        elif kind=='dishwasher':
            if expected['state']!='on':return None
        elif kind=='wheel':
            if not self._charging(cfg) or not expected['state'].startswith('spun'):return None
        elif kind=='page':
            if not self._charging(cfg) or delta not in (-1,1):return None
            try:int(expected['state'])
            except ValueError:return None
            descriptor['delta']=delta
        elif kind=='halloween':
            today=datetime.datetime.fromtimestamp(self.clock() if self.clock else time.time()).astimezone().date()
            if (today.month,today.day)!=(10,31) or not self._charging(cfg) or expected['state'] not in ('on','off'):return None
        elif kind=='alert':
            if not isinstance(alert,dict) or alert.get('kind') not in ('free','saving') or alert.get('dismissible') is not True:return None
            descriptor['alert']={key:alert.get(key) for key in ('kind','title','short','dismissible')}
        else:return None
        return descriptor

    def message_for(self,title,detail,source=None,source_identity=None):
        """Match the displayed message uniquely to a configured source."""
        cfg=self._cfg();entities=cfg.get('kindle_message_entities')
        if not isinstance(entities,list) or len(entities)>12:return None
        matches=[]
        for index in range(len(entities)):
            descriptor=self.describe('message',index=index,source=source,source_identity=source_identity)
            if descriptor:
                value=descriptor['expected']['state'];left,_,right=value.partition('|')
                if left.strip()==title and right.strip()==detail:matches.append(descriptor)
        return matches[0] if len(matches)==1 else None

    def dispatch(self,descriptor):
        """Execute a captured descriptor after rechecking current sources."""
        if not isinstance(descriptor,dict):return {'ok':False,'why':'invalid action'}
        kind=descriptor.get('kind')
        with self.lock:
            cfg=self._cfg()
            try:
                if kind=='refresh':
                    if not self._charging(cfg) or not callable(self.refresh):return {'ok':False,'why':'unavailable'}
                    self.refresh();return {'ok':True}
                if kind=='lights':
                    current=self.describe('lights')
                    if not current or current!=descriptor:return {'ok':False,'why':'stale display'}
                    active=[e for e,v in current['states'].items() if v['state']=='on']
                    self.ha.request('services/light/turn_off',{'entity_id':active});return {'ok':True}
                target=self._target(cfg,kind,descriptor.get('index'))
                expected=descriptor.get('expected')
                if not target or target!=descriptor.get('target') or identity(expected)!=expected or identity(self.ha.optional_state(target))!=expected:return {'ok':False,'why':'stale display'}
                if kind in ('message','wheel','page') and not self._charging(cfg):return {'ok':False,'why':'not charging'}
                if kind=='message' and expected['state'].strip():value=''
                elif kind=='wheel' and expected['state'].startswith('spun'):value='dismissed'
                elif kind=='page' and descriptor.get('delta') in (-1,1):value=str((int(expected['state'])+descriptor['delta'])%2520)
                elif kind=='dishwasher' and expected['state']=='on':
                    self.ha.request('services/input_boolean/turn_off',{'entity_id':target});return {'ok':True}
                elif kind=='halloween':
                    today=datetime.datetime.fromtimestamp(self.clock() if self.clock else time.time()).astimezone().date()
                    if (today.month,today.day)!=(10,31) or not self._charging(cfg) or expected['state'] not in ('on','off'):return {'ok':False,'why':'unavailable'}
                    self.ha.request('services/input_boolean/'+('turn_off' if expected['state']=='on' else 'turn_on'),{'entity_id':target});return {'ok':True}
                elif kind=='alert':
                    alert=descriptor.get('alert');current=self.alerts() if callable(self.alerts) else None
                    if not isinstance(alert,dict) or alert.get('kind') not in ('free','saving') or alert.get('dismissible') is not True or not isinstance(current,dict) or not current.get('configured') or alert not in current.get('alerts',[]):return {'ok':False,'why':'stale display'}
                    value=datetime.datetime.now(datetime.timezone.utc).isoformat()
                else:return {'ok':False,'why':'invalid action'}
                self.ha.request('services/input_text/set_value',{'entity_id':target,'value':value});return {'ok':True}
            except (OSError,ValueError,TypeError,urllib.error.URLError,http.client.HTTPException):return {'ok':False,'why':'action unavailable'}
