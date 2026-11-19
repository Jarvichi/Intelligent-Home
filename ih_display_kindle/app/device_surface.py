"""Build matching pictures and zones from one captured owner snapshot."""
import datetime
import re
import secrets
import time

import device_state
import device_menu
import kindle_render
import popup_render

EATS={'asap':'ASAP','1230':'12:30','1330':'13:30','1830':'18:30'}

class DeviceSurface:
    def __init__(self,snapshot,alerts,menu,cooking,actions,png,ha,settings,clock=time.time):
        self.snapshot=snapshot;self.alerts=alerts;self.menu=menu;self.cooking=cooking
        self.actions=actions;self.png=png;self.ha=ha;self.settings=settings;self.clock=clock

    def charging(self):
        cfg=self.settings()
        return device_state.read(self.ha,cfg.get('kindle_battery_entity',''),cfg.get('kindle_last_seen_entity',''),now=self.clock()).get('charging') is True

    def build(self,screen,page,eat):
        now=self.clock();data=self.snapshot();alerts=self.alerts();zones=[]
        powered=isinstance(data.get('device'),dict) and data['device'].get('charging') is True
        def add(box,descriptor):
            if descriptor:
                power='battery' if descriptor.get('kind') in ('cook','dishwasher','alert') else 'mains'
                target=descriptor.get('screen') if descriptor.get('kind')=='navigate' else None
                preview=target if target in ('dinner','home','birthdays') and target!=screen else 'none'
                zones.append({'box':box,'action':descriptor,'power':power,'preview':preview})
        def nav(box,target,page=0,eat='asap'):
            if powered:add(box,{'kind':'navigate','screen':target,'page':page,'eat':eat})
        if screen=='main':
            warning=kindle_render.dismissible_alert(data,alerts,now)
            dismiss=self.actions.describe('alert',alert=warning) if warning else None
            data=dict(data);data['allow_alert_dismiss']=bool(dismiss)
            if not isinstance(data.get('cooking'),dict):
                # Do not offer household controls while an active cook may be hidden.
                data['cooking_unavailable']=True
                data['device']=dict(data.get('device') or {},charging=False)
                image=kindle_render.svg(data,alerts,now=now)
                add([872,612,1014,664],dismiss)
                return self.png(image),zones
            image=kindle_render.svg(data,alerts,now=now)
            cook=data.get('cooking')
            if isinstance(cook,dict) and cook.get('running') is True:
                revision=cook.get('revision')
                def action(box,name,item=None):
                    if isinstance(revision,str) and re.fullmatch(r'[a-f0-9]{64}',revision):
                        add(box,{'kind':'cook','revision':revision,'action':name,'item_id':item})
                action([354,116,598,174],'stop')
                for i,row in enumerate(cook.get('next',[])[:3]):
                    if not isinstance(row,dict) or not isinstance(row.get('id'),str) or row.get('done') is True:continue
                    y=278+i*60
                    # The first matching zone wins; keep narrower controls ahead
                    # of the whole-row DONE fallback.
                    for x0,x1,kind in ((956,1024,'delay'),(884,952,'early'),(792,884,'ack'),(692,792,'did'),(48,1024,'done')):action([x0,y,x1,y+58],kind,row['id'])
                for i,row in enumerate(cook.get('prep',[])[:5]):
                    if isinstance(row,dict) and isinstance(row.get('id'),str):action([48,1046+i*75,1024,1107+i*75],'tick',row['id'])
            else:
                add([872,612,1014,664],dismiss)
                if powered:
                    nav([292,726,528,866],'home');nav([880,884,1024,924],'menu')
                    if isinstance(cook,dict) and 16<=datetime.datetime.fromtimestamp(now).astimezone().hour<19:nav([48,726,280,866],'dinner')
                    add([270,884,410,924],self.actions.describe('lights'))
                    add([985,36,1024,76],self.actions.describe('refresh'))
                tasks=data.get('tasks');offset=data.get('device',{}).get('page_offset',0)
                rows,_,pages,grouped=kindle_render.task_page(tasks,offset if type(offset) is int else 0,now)
                if grouped and data.get('tasks_partial') is not True:
                    for i,row in enumerate(rows[:5]):
                        if not isinstance(row,dict):continue
                        kind=row.get('action_kind');source=row.get('action_source');source_identity=row.get('action_identity')
                        if not isinstance(source,str):continue
                        descriptor=self.actions.message_for(row.get('title',''),row.get('detail',''),source=source,source_identity=source_identity) if kind=='message' else self.actions.describe(kind,source=source,source_identity=source_identity) if kind in ('dishwasher','wheel') else None
                        if len(rows)<=2:box=[48+i*494,1150,530+i*494,1294]
                        else:
                            h,pitch={3:(87,99),4:(61,75),5:(50,59)}[len(rows)];box=[48,1150+i*pitch,1024,1150+i*pitch+h]
                        add(box,descriptor)
                if powered and grouped and pages>1 and data.get('tasks_partial') is not True:
                    add([800,1106,908,1142],self.actions.describe('page',delta=-1));add([916,1106,1024,1142],self.actions.describe('page',delta=1))
        elif screen=='menu':
            image=device_menu.svg()
            nav([140,300,932,416],'dinner');nav([140,440,932,556],'home')
            if powered:
                add([140,580,932,696],self.actions.describe('lights'));add([140,720,932,836],self.actions.describe('refresh'))
            nav([140,860,932,976],'wifi');nav([140,1000,932,1116],'birthdays');nav([140,1140,932,1256],'main')
        elif screen=='dinner':
            menu=self.menu(page)
            if isinstance(menu,dict):
                menu=dict(menu);menu['selected_eat']=EATS[eat]
                current=menu.get('page',0);pages=menu.get('pages',1)
                for i in range(4):nav([48+i*247,112,283+i*247,176],'dinner',current,tuple(EATS)[i])
                for i,row in enumerate(menu.get('items',[])[:8]):
                    if not powered or not isinstance(row,dict):continue
                    slug=row.get('slug');revision=row.get('revision');ready=row.get('eat')
                    if not isinstance(slug,str) or not isinstance(revision,str) or not isinstance(ready,str):continue
                    selected=EATS[eat];effective=max(ready,selected) if selected!='ASAP' else ready
                    add([48,200+i*128,1024,304+i*128],{'kind':'start_cook','slug':slug,'recipe_revision':revision,'eat':effective,'request_id':secrets.token_hex(16),'lead':row.get('lead')})
                if type(pages) is int and pages>1:
                    nav([48,1240,288,1344],'dinner',(current-1)%pages,eat);nav([312,1240,760,1344],'main');nav([784,1240,1024,1344],'dinner',(current+1)%pages,eat)
                else:nav([48,1240,1024,1344],'main')
            else:nav([48,1240,1024,1344],'main')
            image=popup_render.svg('dinner',menu)
        elif screen=='home':
            image=popup_render.svg('home',next((p for p in data.get('panels',[]) if p.get('label')=='Home'),None));nav([140,1010,932,1114],'main')
        elif screen=='birthdays':
            image=popup_render.svg('birthdays',data.get('birthdays'));nav([48,1240,1024,1344],'main')
        else:raise ValueError('invalid screen')
        return self.png(image),zones

    def dispatch(self,descriptor):
        kind=descriptor.get('kind')
        if kind=='navigate':
            if not self.charging():return {'ok':False}
            return {'ok':True,'navigate':{'screen':descriptor['screen'],'page':descriptor['page'],'eat':descriptor['eat']}}
        if kind=='cook':
            return self.cooking.post('/cook/display-action',{k:descriptor[k] for k in ('revision','action','item_id')})
        if kind=='start_cook':
            if not self.charging():return {'ok':False}
            # Never silently start later than the time captured on the picture.
            try:
                now=datetime.datetime.fromtimestamp(self.clock()).astimezone();lead=descriptor['lead']
                if type(lead) is not int or not 0<=lead<=1440:return {'ok':False}
                earliest=((now.hour*60+now.minute+lead+6)//5)*5
                if earliest>=1440 or earliest>int(descriptor['eat'][:2])*60+int(descriptor['eat'][3:]):return {'ok':False}
            except (KeyError,ValueError,TypeError,OverflowError):return {'ok':False}
            return self.cooking.post('/cook/menu-start',{k:descriptor[k] for k in ('slug','recipe_revision','eat','request_id')})
        return self.actions.dispatch(descriptor)
