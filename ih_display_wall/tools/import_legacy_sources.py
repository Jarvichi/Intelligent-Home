"""Build a private, reviewable wall-source import from existing dashboard rules.

The output contains household entity IDs. Keep it private; never commit it.
"""
import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import source_settings

def extract(dashboard,script,template):
    view=next(v for v in dashboard['views'] if any(r.get('element')=='k_alarm_v' for c in v.get('cards',[]) for r in c.get('config',{}).get('rules',[])))
    rules=view['cards'][0]['config']['rules']
    def rule(name):
        for item in rules:
            if item.get('element')!=name:continue
            actions=item['state_action'];actions=actions if isinstance(actions,list) else [actions]
            for action in actions:
                if action.get('service')=='floorplan.text_set':return item,action['service_data']
        raise ValueError('Missing dashboard rule: '+name)
    def literal(pattern,source):
        match=re.search(pattern,source,re.M)
        if not match:raise ValueError('Expected source mapping not found')
        return ast.literal_eval(match.group(1))
    def entity(item):
        value=item.get('entity') or next(iter(item.get('entities',[])),None)
        if not isinstance(value,str):raise ValueError('Expected entity not found')
        return value
    heat=literal(r'^HE\s*=\s*(\[[^\n]+\])',script)
    intrusion=literal(r'^IE\s*=\s*(\[[^\n]+\])',script)
    people=literal(r'var PEOPLE\s*=\s*(\[[^\n]+\]);',template)
    rooms=literal(r'var ROOMS\s*=\s*(\[[^\n]+\]);',template)
    doors=literal(r'var DS=(\[.*?\]);\s*var bad',rule('k_doors_v')[1])
    lights=[]
    for name in ('k_lt0','k_lt1','k_lt2','k_lt3'):
        match=re.search(r'var R=(\[.*?\]); var on=',rule(name)[1])
        if not match:raise ValueError('Expected light mapping not found')
        lights.extend({'name':name,'entities':entities} for name,entities in json.loads(match.group(1)))
    energy=rule('k_elec')[1];cost=rule('k_cost')[1]
    match=re.search(r"var E='([^']+)'",energy)
    calendar=re.search(r'calendar\.[a-z0-9_]+',energy);sessions=re.search(r'event\.[a-z0-9_]+',energy)
    costs=re.findall(r'sensor\.[a-z0-9_]+current_accumulative_cost',cost)
    current_weather=re.search(r"^W_\s*=\s*'([^']+)'",script,re.M)
    current_forecast=re.search(r"var BF=hass\.states\['([^']+)'\]",script)
    fallback_forecast=re.search(r"var f=hass\.states\['[^']+'\]\|\|hass\.states\['([^']+)'\]",script)
    if not all((match,calendar,sessions,current_weather,current_forecast,fallback_forecast)) or len(costs)<2 or len(heat)<2 or len(intrusion)<3:raise ValueError('Source mapping is incomplete')
    base=match.group(1)
    result={'render_alarm':entity(rule('k_alarm_v')[0]),'render_sun_entity':entity(rule('k_sun')[0]),'render_presence':[p[1] for p in people],
            'render_doors':[{'name':d[0],'lock':d[1],'contact':d[2]} for d in doors],
            'render_heat_boiler':heat[0],'render_heat_thermostat':heat[1],'render_heat_floors':heat[2:],
            'render_lights':lights,'render_rooms':[{'name':name,'entity':entity(rule('k_rt'+str(i))[0])} for i,name in enumerate(rooms)],'render_media_entities':[entity(rule('k_np_title')[0])],
            'render_game_entities':[entity(rule('k_ps_t1')[0])],'render_cooking_entity':entity(rule('k_ck_title')[0]),
            'render_intrusion_alert':intrusion[0],'render_intrusion_log':intrusion[1],'render_intrusion_presence':intrusion[2:],
            'weather_current_entity':current_weather.group(1),'weather_forecast_entity':current_forecast.group(1),
            'weather_forecast_fallback_entity':fallback_forecast.group(1),
            'render_energy':{'current':'sensor.'+base+'_current_rate','next':'sensor.'+base+'_next_rate',
                'electricity':costs[0],'gas':costs[1],'calendar':calendar.group(), 'sessions':sessions.group(),
                'rates_today':'event.'+base+'_current_day_rates','rates_tomorrow':'event.'+base+'_next_day_rates'}}
    if not source_settings.validate(result):raise ValueError('Extracted source mapping failed validation')
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for option in ('dashboard','rule-script','wall-template','output'):parser.add_argument('--'+option,required=True)
    args=parser.parse_args()
    config=extract(json.loads(Path(args.dashboard).read_text()),Path(args.rule_script).read_text(),Path(args.wall_template).read_text())
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as handle:json.dump(config,handle,ensure_ascii=False,indent=2)
    print('Private source mapping created. Review it before importing.')

if __name__=='__main__':main()
