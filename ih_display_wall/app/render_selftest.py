"""Verify the image converter against a representative generated picture at build time."""
import struct
import sys
import base64
import zlib
import main
import wall_tv

profile={'state':'ready','width':1920,'height':1080}
data={'items':[{'label':'Alarm','value':'Armed'},{'label':'Doors','value':'Locked'},
               {'label':'Heat','value':'19.5 °C → 20 °C'},{'label':'Energy','value':'25.0p now · 15.0p next'},
               {'label':'Lights','value':'Living room, kitchen'},{'label':'Music','value':'Example song · Example artist'}],
      'tasks':[{'title':'Example task '+str(i),'detail':'Example detail','priority':'normal'} for i in range(1,6)],
      'tasks_configured':True,'tasks_partial':False,
      'sun':'Sunset 19:00','sun_configured':True,'sun_dark':False,
      'panels':[{'available':True,'active':True,'pinned':True,'label':'Home','detail':'Example person'},
                {'available':True,'active':True,'pinned':True,'slot':'occasion','label':'Occasion','detail':'Example in 3 days'}],
      'weather_configured':True,'weather':{'source':'primary','current':{'current':True,'temperature':18,'condition':'cloudy'},
          'facts':{'wind':'12 km/h','bearing':180,'next_rain':'15:00','sky':'Cloud 60%'},
          'daily':[{'label':'Today','high':20,'low':12,'condition':'cloudy'},{'label':'Tomorrow','high':19,'low':11,'condition':'rainy'}],
          'hours':[{'time':f'{12+i}:00','temperature':18-i,'condition':'cloudy'} for i in range(6)]}}
def chunk(kind,payload):return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
sample_art=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\x80\xa0\xc0\xff'))+chunk(b'IEND',b'')
data['items'][-1]['art']='data:image/png;base64,'+base64.b64encode(sample_art).decode('ascii')
data['panels'][0]['persons']=[{'name':'Example person','art':'data:image/png;base64,'+base64.b64encode(sample_art).decode('ascii')}]
width,height=64,36
pixels=b''.join(b'\x00'+b''.join(bytes((20+x,15+y,25+(x+y)//2,255)) for x in range(width)) for y in range(height))
background=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
data['background']='data:image/png;base64,'+base64.b64encode(background).decode('ascii')
data['weather']['daily'][1]['precipitation']=1.3
alerts={'configured':True,'complete':True,'alerts':[{'title':'EXAMPLE ALERT'}] if '--alert' in sys.argv else []}
if '--night' in sys.argv:data['night_mode']='deep';data['sun']='Sunrise 07:00'
picture=main.png(wall_tv.svg(profile,data,alerts,now=12*3600))
if picture[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',picture[16:24])!=(1920,1080):
    raise RuntimeError('Generated PNG has invalid dimensions or format')
if '--stdout' in sys.argv:sys.stdout.buffer.write(picture)
