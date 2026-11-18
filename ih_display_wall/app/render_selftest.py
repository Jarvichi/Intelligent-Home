"""Verify the image converter against a representative generated picture at build time."""
import struct
import sys
import main
import render

profile={'state':'ready','width':1920,'height':1080}
data={'items':[{'label':'Temperature','value':'20 °C'}],
      'tasks':[{'title':'Example task','detail':'Example detail','priority':'normal'}],
      'tasks_configured':True,'tasks_partial':False}
picture=main.png(render.svg(profile,data))
if picture[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',picture[16:24])!=(1920,1080):
    raise RuntimeError('Generated PNG has invalid dimensions or format')
if sys.argv[1:]==['--stdout']:sys.stdout.buffer.write(picture)
