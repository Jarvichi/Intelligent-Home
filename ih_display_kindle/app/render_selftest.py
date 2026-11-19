"""Render a synthetic preview with the converter installed in the image."""
import struct
import sys
import kindle_render
import main
import popup_render

sample={'sun':'Sunset 19:00','weather_configured':True,'tasks_configured':True,'tasks_partial':False,'tasks':[{'title':'Example task '+str(i),'detail':'in 6 days','urgent':i==2,'display_group':('message' if i==1 else 'collection' if i==5 else 'task')} for i in range(1,6)],
        'panels':[{'available':True,'active':True,'label':'Alarm','detail':'Off'},{'available':True,'active':True,'label':'Home','detail':'Example person','persons':[{'name':'Example'}]},{'available':True,'active':True,'label':'Doors locked','detail':''},{'available':True,'active':True,'label':'Heat idle','detail':''},{'available':True,'active':True,'label':'Lights on','detail':'Example room'},{'available':True,'active':True,'label':'Energy','detail':'20p now'}],
        'weather':{'current':{'temperature':10,'condition':'partlycloudy'},'facts':{'wind':'10 km/h','next_rain':'16:00','sky':'Cloud 60%'},'daily':[{'label':'Today','low':3,'high':12},{'label':'Tomorrow','low':4,'high':11,'condition':'rainy','precipitation':1.3},{'label':'Next day','low':3,'high':10,'condition':'cloudy'}],'hours':[{'time':f'{(12+i*2)%24:02d}:00','temperature':10-i,'condition':'cloudy'} for i in range(6)]}}
sample['device']={'fresh':True,'charging':True,'battery':85,'page_offset':0}
if '--short' in sys.argv:
    sample['tasks']=sample['tasks'][:1]
    sample['panels'].append({'kind':'room','label':'Example room','detail':'19.5°','available':True,'active':True})
if '--media' in sys.argv:
    sample['panels'].extend([{'kind':'media','label':'Example speaker','detail':'Example song · Example artist','available':True,'active':True},{'kind':'game','label':'Example console','detail':'Example game','available':True,'active':True}])
alerts={'configured':True,'complete':False,'alerts':[{'kind':'free','title':'EXAMPLE ENERGY'},{'kind':'smoke','title':'EXAMPLE SMOKE'},{'kind':'co','title':'EXAMPLE CO'}]} if '--alert' in sys.argv else {'configured':True,'complete':True,'alerts':[]}
image=main.png(kindle_render.svg(sample,alerts,now=12*3600))
if image[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',image[16:24])!=(1072,1448):raise RuntimeError('Invalid preview PNG')
if '--stdout' not in sys.argv:
    screens=[popup_render.svg('dinner',{'items':[{'title':'Example meal','eat':'19:00','lead':40}],'page':0,'pages':1}),
             popup_render.svg('home',{'members':[{'name':'Example','home':True}]}),
             popup_render.svg('birthdays',{'items':[{'name':'Example','date':'Monday','countdown':'TODAY'}]}),
             kindle_render.svg({'cooking':{'running':True,'title':'Example meal','eat':'19:00','next':[{'at':'18:30','text':'Example step'}],'prep':[{'text':'Example prep'}],'steps':[]}},alerts)]
    for screen in screens:
        converted=main.png(screen)
        if converted[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',converted[16:24])!=(1072,1448):raise RuntimeError('Invalid screen PNG')
if '--stdout' in sys.argv:sys.stdout.buffer.write(image)
