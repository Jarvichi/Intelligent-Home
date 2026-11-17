"""Read-only battery and robot-maintenance task projection."""
import math
import re
import robot_tasks

DEVICE_ID=re.compile(r'^[a-f0-9]{32}$')

def trim_title(value):
    encoded=value.encode('utf-16-le')
    return value if len(encoded)<=124 else encoded[:122].decode('utf-16-le',errors='ignore')+'…'
def task(title,urgent=False):return {'title':trim_title(title),'detail':'','priority':'normal','urgent':urgent}
def maps(rows,key,value,allow_empty=False):
    if not isinstance(rows,list) or len(rows)>500:raise ValueError('invalid mappings')
    result={}
    for row in rows:
        if not isinstance(row,dict) or set(row)!={key,value} or not all(isinstance(row[k],str) for k in (key,value)) or not row[key] or (not allow_empty and not row[value]) or row[key] in result:raise ValueError('invalid mappings')
        result[row[key]]=row[value]
    return result

class BatteryMaintenance:
    def __init__(self,config,snapshot):self.config=config if isinstance(config,dict) else {};self.snapshot=snapshot
    def _validate(self):
        c=self.config
        enabled=c.get('battery_enabled') is True
        groups=c.get('maintenance_groups',[])
        if not isinstance(groups,list) or len(groups)>20:raise ValueError('invalid maintenance')
        for group in groups:
            if not isinstance(group,dict) or not {'name','kind','parts'}<=set(group) or not set(group)<={'name','kind','stored_entity','parts'}:raise ValueError('invalid group')
            if not isinstance(group['name'],str) or not group['name'].strip() or len(group['name'])>80 or group['kind'] not in ('vacuum','mower') or not isinstance(group['parts'],list) or len(group['parts'])>20:raise ValueError('invalid group')
            stored=group.get('stored_entity','') or ''
            if not isinstance(stored,str) or (stored and (not robot_tasks.ENTITY.fullmatch(stored) or not stored.startswith('input_boolean.'))):raise ValueError('invalid group')
            for part in group['parts']:
                if not isinstance(part,dict) or set(part)!={'entity','label','threshold'} or not isinstance(part['entity'],str) or not part['entity'].startswith('sensor.') or not robot_tasks.ENTITY.fullmatch(part['entity']) or not isinstance(part['label'],str) or not part['label'].strip() or len(part['label'])>80 or type(part['threshold']) is not int or not -100<=part['threshold']<=100:raise ValueError('invalid part')
        if enabled:
            for key,prefix in (('battery_forecast_entity','sensor.'),('battery_ignored_entity','input_text.')):
                v=c.get(key,'')
                if not isinstance(v,str) or not v.startswith(prefix) or not robot_tasks.ENTITY.fullmatch(v):raise ValueError('invalid source')
            excluded=c.get('battery_exclude_tokens',[])
            if not isinstance(excluded,list) or len(excluded)>100 or any(not isinstance(x,str) or not x or len(x)>80 for x in excluded):raise ValueError('invalid exclusions')
            models=maps(c.get('battery_models',[]),'model','type')
            overrides=c.get('battery_device_overrides',[])
            if not isinstance(overrides,list) or len(overrides)>500:raise ValueError('invalid overrides')
            by_device={}
            for override in overrides:
                if not isinstance(override,dict) or not {'device_id'}<=set(override) or not set(override)<={'device_id','name','type'}:raise ValueError('invalid override')
                ident=override['device_id']
                if not isinstance(ident,str) or not DEVICE_ID.fullmatch(ident) or ident in by_device:raise ValueError('invalid override')
                if any(not isinstance(override.get(k,''),str) or len(override.get(k,'') or '')>80 for k in ('name','type')):raise ValueError('invalid override')
                by_device[ident]=override
        else:models={};by_device={}
        return enabled,groups,models,by_device
    def read(self):
        try:enabled,groups,models,overrides=self._validate()
        except ValueError:return {'configured':True,'complete':False,'items':[]}
        if not enabled and not groups:return {'configured':False,'complete':True,'items':[]}
        snapshot=self.snapshot()
        if not isinstance(snapshot,dict) or not all(isinstance(snapshot.get(k),list) for k in ('states','entities','devices','areas')):return {'configured':True,'complete':False,'items':[]}
        states={x['entity_id']:x for x in snapshot['states'] if isinstance(x,dict) and isinstance(x.get('entity_id'),str)}
        entities={x['entity_id']:x for x in snapshot['entities'] if isinstance(x,dict) and isinstance(x.get('entity_id'),str)}
        devices={x['id']:x for x in snapshot['devices'] if isinstance(x,dict) and isinstance(x.get('id'),str)}
        areas={x['area_id']:x.get('name','') for x in snapshot['areas'] if isinstance(x,dict) and isinstance(x.get('area_id'),str)}
        complete=True;maintenance=[];battery=[];offline=[]
        for group in groups:
            mute=group.get('stored_entity','') or ''
            if mute:
                state=robot_tasks.state(states,mute)
                if state not in ('on','off'):complete=False;continue
                if state=='on':continue
            worn=[]
            for part in group['parts']:
                raw=robot_tasks.state(states,part['entity'])
                if raw is None or raw in ('unknown','unavailable'):complete=False;continue
                value=robot_tasks.number(raw)
                if value is not None and value<=part['threshold']:worn.append(part['label'].strip())
            if worn:maintenance.append(task(('✂️ ' if group['kind']=='mower' else '🧹 ')+group['name'].strip()+' — replace '+', '.join(worn)))
        if enabled:
            ignore=robot_tasks.state(states,self.config['battery_ignored_entity'])
            forecast=states.get(self.config['battery_forecast_entity'])
            if not isinstance(ignore,str) or ignore in ('unknown','unavailable'):
                return {'configured':True,'complete':False,'items':maintenance}
            ignored=set(DEVICE_ID.findall(ignore))
            attrs=forecast.get('attributes') if isinstance(forecast,dict) else None
            estimates=attrs.get('forecast') if isinstance(attrs,dict) else None
            if not isinstance(estimates,dict):complete=False;estimates={}
            exclude=self.config.get('battery_exclude_tokens',[])
            for eid,s in states.items():
                if 'battery' not in eid or any(token in eid for token in exclude) or not (eid.startswith('sensor.') or eid.startswith('binary_sensor.')):continue
                attributes=s.get('attributes') if isinstance(s.get('attributes'),dict) else {}
                if attributes.get('device_class')!='battery':continue
                entry=entities.get(eid,{})
                device_id=entry.get('device_id')
                if not isinstance(device_id,str):device_id=''
                if device_id in ignored:continue
                device=devices.get(device_id,{})
                override=overrides.get(device_id,{})
                model_id=device.get('model_id') if isinstance(device.get('model_id'),str) else ''
                model=device.get('model') if isinstance(device.get('model'),str) else ''
                battery_type=override.get('type') or models.get(model_id) or models.get(model) or ''
                rechargeable=battery_type=='recharge'
                area_id=entry.get('area_id') or device.get('area_id')
                if not isinstance(area_id,str):area_id=''
                area=areas.get(area_id,area_id) if area_id else ''
                if not isinstance(area,str):area=''
                name=override.get('name') or device.get('name_by_user') or device.get('name') or ('' if device else eid)
                name=name.strip() if isinstance(name,str) else ''
                if not area and not rechargeable:continue
                if area and name==area:
                    suffix={'RWL021':'dimmer switch','Hue dimmer switch':'dimmer switch','RDM001':'wall switch','TRVZB':'radiator valve'}.get(model_id or model,'device')
                    name=area+' '+suffix
                if not name and rechargeable:name=eid
                where=name if not area or area.split(' ')[0].lower() in name.lower() else name+' — '+area
                current=s.get('state')
                if current=='unavailable':
                    title='🔌 '+where+' · offline, check battery'
                    if not any(item['title']==trim_title(title) for item in offline):offline.append(task(title))
                    continue
                suffix=' · '+battery_type if battery_type and not rechargeable else ''
                if eid.startswith('binary_sensor.'):
                    if current=='on':battery.append(task('🔋 '+where+' · low'+suffix))
                    continue
                value=robot_tasks.number(current)
                if value is None:continue
                f=estimates.get(eid);f=f if isinstance(f,dict) else {};days=robot_tasks.number(f.get('d')) if f.get('d') is not None else None
                if f.get('d') is not None and days is None:complete=False;continue
                if (days<20 or value<=5) if days is not None else value<=15:
                    title=('🔋 Recharge ' if rechargeable else '🔋 ')+where+' · '+str(math.floor(value+0.5))+'%'
                    if days is not None:title+=' · ~'+str(max(1,math.ceil(days)))+' days'
                    battery.append(task(title+suffix,(days is not None and days<7) or value<=5))
                elif (robot_tasks.number(f.get('f')) or 0)>=2:battery.append(task('🔋 '+where+' · dropping out, battery?'+suffix))
        return {'configured':True,'complete':complete,'items':maintenance+battery+offline}
