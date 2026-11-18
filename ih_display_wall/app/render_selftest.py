"""Verify the image converter against a representative generated picture at build time."""
import struct
import sys
import main
import render

profile={'state':'ready','width':1920,'height':1080}
data={'items':[{'label':'Alarm','value':'Armed'},{'label':'Doors','value':'Locked'},
               {'label':'Heat','value':'19.5 °C → 20 °C'},{'label':'Energy','value':'25.0p now · 15.0p next'},
               {'label':'Lights','value':'Living room, kitchen'},{'label':'Music','value':'Example song · Example artist'}],
      'tasks':[{'title':'Example task '+str(i),'detail':'Example detail','priority':'normal'} for i in range(1,6)],
      'tasks_configured':True,'tasks_partial':False,
      'panels':[{'available':True,'active':True,'pinned':True,'label':'Home','detail':'Example person'}],
      'weather_configured':True,'weather':{'source':'primary','current':{'current':True,'temperature':18,'condition':'cloudy'},
          'facts':{'wind':'12 km/h','bearing':180,'next_rain':'15:00','sky':'Cloud 60%'},
          'daily':[{'label':'Today','high':20,'low':12,'condition':'cloudy'},{'label':'Tomorrow','high':19,'low':11,'condition':'rainy'}],
          'hours':[{'time':f'{12+i}:00','temperature':18-i,'condition':'cloudy'} for i in range(6)]}}
alerts={'configured':True,'complete':True,'alerts':[{'title':'EXAMPLE ALERT'}] if '--alert' in sys.argv else []}
picture=main.png(render.svg(profile,data,alerts,now=0))
if picture[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',picture[16:24])!=(1920,1080):
    raise RuntimeError('Generated PNG has invalid dimensions or format')
if '--stdout' in sys.argv:sys.stdout.buffer.write(picture)
