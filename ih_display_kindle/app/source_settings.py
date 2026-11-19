"""Validate and atomically save admin-imported, non-secret display source mappings."""
import json
import datetime
import os
import re
import tempfile
import threading
from pathlib import Path

ENTITY=re.compile(r'^[a-z][a-z0-9_]*\.[a-z0-9_]+$')
SCALARS={'kindle_dishwasher_entity':'input_boolean.','kindle_wheel_entity':'input_text.','kindle_warning_dismissed_entity':'input_text.','kindle_presence_times_entity':'sensor.','kindle_birthdays_entity':'sensor.','kindle_battery_entity':'sensor.','kindle_last_seen_entity':'sensor.','kindle_page_entity':'input_text.',
         'render_alarm':'alarm_control_panel.','render_cooking_entity':'sensor.',
         'render_intrusion_alert':'input_boolean.','render_intrusion_log':'input_text.',
         'render_heat_boiler':'binary_sensor.','render_heat_thermostat':'climate.',
         'weather_forecast_entity':'sensor.','weather_forecast_fallback_entity':'sensor.',
         'weather_current_entity':'weather.','render_sun_entity':'sun.',
         'render_birthdays_entity':'sensor.','render_halloween_entity':'input_boolean.'}
LISTS={'kindle_message_entities':'input_text.','kindle_lights_off_entities':'light.','render_intrusion_presence':'switch.',
       'render_heat_floors':'climate.','render_media_entities':'media_player.',
       'render_game_entities':'media_player.','render_overlay_entities':'binary_sensor.',
       'render_entities':''}
ENERGY={'current':'sensor.','next':'sensor.','electricity':'sensor.','gas':'sensor.',
        'calendar':'calendar.','sessions':'event.','rates_today':'event.','rates_tomorrow':'event.'}
LIST_LIMITS={'kindle_message_entities':12,'kindle_lights_off_entities':100,'render_intrusion_presence':10,'render_heat_floors':8,
             'render_media_entities':8,'render_game_entities':8,'render_overlay_entities':8,'render_entities':6}

def entity(value,prefix,empty=False):
    return isinstance(value,str) and ((empty and not value) or (len(value)<=255 and bool(ENTITY.fullmatch(value)) and value.startswith(prefix)))

def local_picture(value):
    return isinstance(value,str) and len(value)<=255 and bool(re.fullmatch(r'/local/[A-Za-z0-9_./-]+',value)) and '..' not in value.split('/')

def validate(value):
    if not isinstance(value,dict) or not value or len(value)>50:return False
    for key,item in value.items():
        if key in SCALARS:
            if not entity(item,SCALARS[key],True):return False
        elif key in LISTS:
            if not isinstance(item,list) or len(item)>LIST_LIMITS[key] or any(not entity(x,LISTS[key]) for x in item):return False
        elif key=='render_doors':
            if not isinstance(item,list) or len(item)>10:return False
            for row in item:
                if not isinstance(row,dict) or set(row)!={'name','lock','contact'} or not isinstance(row['name'],str) or not 0<len(row['name'].strip())<=80 or not entity(row['lock'],'lock.') or not entity(row['contact'],'binary_sensor.'):return False
        elif key=='render_lights':
            if not isinstance(item,list) or len(item)>40:return False
            for row in item:
                if not isinstance(row,dict) or set(row) not in ({'name','entities'},{'name','entities','floor'}) or not isinstance(row['name'],str) or not 0<len(row['name'].strip())<=80 or not isinstance(row['entities'],list) or not 0<len(row['entities'])<=50 or ('floor' in row and (not isinstance(row['floor'],str) or not 0<len(row['floor'].strip())<=40)):return False
                if any(not entity(e,'light.') and not entity(e,'switch.') for e in row['entities']):return False
            if len({e for row in item for e in row['entities']})>100:return False
        elif key in ('kindle_lights_off_floors','kindle_lights_off_exclude'):
            if not isinstance(item,list) or len(item)>20 or any(not isinstance(x,str) or not re.fullmatch(r'[a-z0-9_]{1,80}',x) for x in item) or len(set(item))!=len(item):return False
        elif key=='render_rooms':
            if not isinstance(item,list) or len(item)>20:return False
            for row in item:
                if not isinstance(row,dict) or set(row)!={'name','entity'} or not isinstance(row['name'],str) or not 0<len(row['name'].strip())<=80 or not (entity(row['entity'],'sensor.') or entity(row['entity'],'climate.')):return False
        elif key=='render_presence':
            if not isinstance(item,list) or len(item)>10:return False
            for row in item:
                if not isinstance(row,dict) or not {'name','entity'}<=set(row) or set(row)-{'name','entity','picture','presence_key'} or not isinstance(row['name'],str) or not 0<len(row['name'].strip())<=80 or not entity(row['entity'],'switch.') or ('picture' in row and not local_picture(row['picture'])) or ('presence_key' in row and (not isinstance(row['presence_key'],str) or not re.fullmatch(r'[a-z0-9_]{1,40}',row['presence_key']))):return False
        elif key=='render_background_path':
            if not isinstance(item,str) or (item and not local_picture(item)):return False
        elif key=='render_birthdays_fixed':
            if not isinstance(item,list) or len(item)>100:return False
            for row in item:
                if not isinstance(row,dict) or set(row)!={'name','month','day'} or not isinstance(row['name'],str) or not 0<len(row['name'].strip())<=80 or type(row['month']) is not int or type(row['day']) is not int:return False
                try:datetime.date(2000,row['month'],row['day'])
                except ValueError:return False
        elif key=='render_energy':
            if not isinstance(item,dict) or any(k not in ENERGY or not entity(v,ENERGY[k],True) for k,v in item.items()):return False
        else:return False
    return len(json.dumps(value,ensure_ascii=False).encode('utf-8'))<=65536

class SourceSettings:
    def __init__(self,path):self.path=Path(path);self.lock=threading.Lock()
    def read(self):
        try:
            if self.path.stat().st_size>65536:return {}
            value=json.loads(self.path.read_text(encoding='utf-8'))
            return value if validate(value) else {}
        except (OSError,ValueError,TypeError):return {}
    def save(self,value):
        if not validate(value):raise ValueError('Invalid source mappings')
        with self.lock:
            merged=self.read();energy=dict(merged.get('render_energy') or {});energy.update(value.get('render_energy') or {});merged.update(value)
            if energy:merged['render_energy']=energy
            if not validate(merged):raise ValueError('Invalid source mappings')
            self.path.parent.mkdir(parents=True,exist_ok=True)
            fd,tmp=tempfile.mkstemp(prefix='.wall-sources-',dir=self.path.parent)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(merged,f,ensure_ascii=False)
                os.replace(tmp,self.path)
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
