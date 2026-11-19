"""Legacy Kindle cats: five-minute visitors and hourly weather scenes.

Artwork and grayscale weather props were drawn for the original local Kindle
floorplan; only generic SVG geometry is included here.
"""
import math

P = {'cat_calico_a': '<path d="M14,-3 C30,-2 33,-18 25,-25" fill="none" stroke="#000" stroke-width="7" stroke-linecap="round"/><path d="M14,-3 C30,-2 33,-18 25,-25" fill="none" stroke="#8a8a8a" stroke-width="4" stroke-linecap="round"/><ellipse cx="0" cy="-16" rx="15" ry="17" fill="#fff" stroke="#000" stroke-width="2.5"/><ellipse cx="6" cy="-21" rx="7" ry="8" fill="#8a8a8a"/><ellipse cx="-7" cy="-9" rx="5" ry="4" fill="#000"/><path d="M-10,-42 L-8,-55 L-1,-47 Z" fill="#000"/><path d="M10,-42 L8,-55 L1,-47 Z" fill="#8a8a8a" stroke="#000" stroke-width="2"/><circle cx="0" cy="-38" r="11" fill="#fff" stroke="#000" stroke-width="2.5"/><path d="M-11,-38 A11,11 0 0,1 -1,-49 L-2,-37 Z" fill="#000"/><path d="M11,-38 A11,11 0 0,0 4,-48.3 L5,-40 Z" fill="#8a8a8a"/><circle cx="-4" cy="-39" r="1.8" fill="#fff"/><circle cx="4" cy="-39" r="1.8" fill="#000"/><path d="M-1.5,-34.5 L1.5,-34.5 L0,-33 Z" fill="#000"/>', 'cat_tux_a': '<path d="M14,-3 C30,-2 33,-18 25,-25" fill="none" stroke="#000" stroke-width="5" stroke-linecap="round"/><ellipse cx="0" cy="-16" rx="15" ry="17" fill="#000"/><circle cx="0" cy="-38" r="11" fill="#000"/><path d="M-10,-42 L-8,-55 L-1,-47 Z M10,-42 L8,-55 L1,-47 Z" fill="#000"/><ellipse cx="0" cy="-17" rx="6" ry="9" fill="#fff"/><ellipse cx="0" cy="-33.5" rx="5" ry="3.5" fill="#fff"/><ellipse cx="-5" cy="-1.5" rx="4" ry="2.5" fill="#fff"/><ellipse cx="5" cy="-1.5" rx="4" ry="2.5" fill="#fff"/><circle cx="-4" cy="-40" r="2" fill="#fff"/><circle cx="4" cy="-40" r="2" fill="#fff"/><circle cx="0" cy="-35" r="1.3" fill="#000"/>', 'cat_calico_c': '<ellipse cx="35" cy="-16" rx="32" ry="16" fill="#fff" stroke="#000" stroke-width="2.5"/><ellipse cx="22" cy="-21" rx="12" ry="8" fill="#8a8a8a"/><ellipse cx="40" cy="-25" rx="8" ry="5" fill="#000"/><path d="M50,-26 L52,-37 L58,-28 Z M59,-28 L64,-37 L67,-25 Z" fill="#000"/><circle cx="59" cy="-18" r="11" fill="#fff" stroke="#000" stroke-width="2.5"/><path d="M48,-18 A11,11 0 0,1 59,-29 L59,-18 Z" fill="#8a8a8a"/><path d="M5,-9 C10,3 50,4 62,-4" fill="none" stroke="#000" stroke-width="8" stroke-linecap="round"/><path d="M5,-9 C10,3 50,4 62,-4" fill="none" stroke="#8a8a8a" stroke-width="5" stroke-linecap="round"/><path d="M55,-19 q3,2.5 6,0" fill="none" stroke="#000" stroke-width="1.8" stroke-linecap="round"/><text x="70" y="-30" style="font:700 13px sans-serif" fill="#000">z</text><text x="78" y="-42" style="font:700 17px sans-serif" fill="#000">z</text>', 'cat_tux_c': '<ellipse cx="35" cy="-16" rx="32" ry="16" fill="#000"/><path d="M50,-26 L52,-37 L58,-28 Z M59,-28 L64,-37 L67,-25 Z" fill="#000"/><circle cx="59" cy="-18" r="11" fill="#000"/><ellipse cx="63" cy="-13" rx="5" ry="3.5" fill="#fff"/><path d="M5,-9 C10,3 50,4 62,-4" fill="none" stroke="#000" stroke-width="6" stroke-linecap="round"/><ellipse cx="46" cy="-3" rx="5" ry="2.5" fill="#fff"/><path d="M55,-19 q3,2.5 6,0" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/><text x="70" y="-30" style="font:700 13px sans-serif" fill="#000">z</text><text x="78" y="-42" style="font:700 17px sans-serif" fill="#000">z</text>', 'cat_calico_b': '<path d="M12,-27 C1,-31 -2,-45 6,-52" fill="none" stroke="#000" stroke-width="7" stroke-linecap="round"/><path d="M12,-27 C1,-31 -2,-45 6,-52" fill="none" stroke="#8a8a8a" stroke-width="4" stroke-linecap="round"/><rect x="13" y="-17" width="6" height="17" rx="3" fill="#fff" stroke="#000" stroke-width="2.5"/><rect x="22" y="-17" width="6" height="17" rx="3" fill="#fff" stroke="#000" stroke-width="2.5"/><rect x="40" y="-17" width="6" height="17" rx="3" fill="#fff" stroke="#000" stroke-width="2.5"/><rect x="49" y="-17" width="6" height="17" rx="3" fill="#fff" stroke="#000" stroke-width="2.5"/><ellipse cx="33" cy="-24" rx="23" ry="11" fill="#fff" stroke="#000" stroke-width="2.5"/><path d="M52,-37 L54,-48 L60,-39 Z M60,-38 L65,-48 L67,-36 Z" fill="#000"/><circle cx="60" cy="-31" r="9.5" fill="#fff" stroke="#000" stroke-width="2.5"/><ellipse cx="25" cy="-27" rx="9" ry="6" fill="#8a8a8a"/><ellipse cx="41" cy="-19" rx="6" ry="4" fill="#000"/><path d="M51,-31 A9.5,9.5 0 0,1 60,-40.5 L60,-31 Z" fill="#000"/><circle cx="63" cy="-33" r="1.8" fill="#000"/>', 'cat_tux_b': '<path d="M12,-27 C1,-31 -2,-45 6,-52" fill="none" stroke="#000" stroke-width="5" stroke-linecap="round"/><rect x="13" y="-17" width="6" height="17" rx="3" fill="#000"/><rect x="22" y="-17" width="6" height="17" rx="3" fill="#000"/><rect x="40" y="-17" width="6" height="17" rx="3" fill="#000"/><rect x="49" y="-17" width="6" height="17" rx="3" fill="#000"/><ellipse cx="33" cy="-24" rx="23" ry="11" fill="#000"/><path d="M52,-37 L54,-48 L60,-39 Z M60,-38 L65,-48 L67,-36 Z" fill="#000"/><circle cx="60" cy="-31" r="9.5" fill="#000"/><ellipse cx="53" cy="-21" rx="5" ry="6" fill="#fff"/><circle cx="65" cy="-28" r="3.5" fill="#fff"/><rect x="13" y="-4" width="6" height="4" rx="2" fill="#fff"/><rect x="22" y="-4" width="6" height="4" rx="2" fill="#fff"/><rect x="40" y="-4" width="6" height="4" rx="2" fill="#fff"/><rect x="49" y="-4" width="6" height="4" rx="2" fill="#fff"/><circle cx="63" cy="-33" r="1.8" fill="#fff"/>'}
SIT = {k: P[f"cat_{k}_a"] for k in ("calico", "tux")}
SLEEP = {k: P[f"cat_{k}_c"] for k in ("calico", "tux")}

def drop(x, y): return f'<path class="wdrop" d="M{x},{y} q-4,7 0,9 q4,-2 0,-9 Z"/>'
def flake(x, y, r):
    return '<g class="wflake">' + ''.join(f'<line x1="{x}" y1="{y}" x2="{x+r*math.cos(math.radians(a)):.1f}" y2="{y+r*math.sin(math.radians(a)):.1f}"/>' for a in range(0, 360, 60)) + '</g>'
UMB = ('<path class="wumb" d="M-36,-62 Q-36,-96 0,-98 Q36,-96 36,-62 Q27,-68 18,-62 Q9,-68 0,-62 Q-9,-68 -18,-62 Q-27,-68 -36,-62 Z"/>'
       '<line class="wshaft" x1="0" y1="-98" x2="0" y2="-104"/><line class="wshaft" x1="0" y1="-62" x2="0" y2="-51"/>')
RAIN = ''.join(drop(x, y) for x, y in [(-50, -56), (48, -46), (-46, -24), (52, -16), (-58, -4), (60, -60)])
PUD = '<ellipse class="wpud" cx="0" cy="1" rx="44" ry="4"/>'
BOLT = '<path class="wbolt" transform="translate(46,-100) scale(0.5)" d="M10,0 L-6,34 L6,34 L-4,66 L24,24 L10,24 L20,0 Z"/>'
HAT = ('<path class="what" d="M-12,-41 Q-12,-59 0,-59 Q12,-59 12,-41 Z"/><rect class="whatb" x="-13" y="-45" width="26" height="5" rx="2"/>'
       '<circle class="wpom" cx="0" cy="-61" r="4"/>')
SCARF = '<rect class="wscarf" x="-12" y="-31" width="24" height="6" rx="3"/><rect class="wscarf" x="4" y="-31" width="6" height="15" rx="2"/>'
SNOWF = flake(-42, -62, 6) + flake(40, -84, 5) + flake(48, -30, 6) + flake(-50, -22, 5) + flake(20, -104, 4)
SHADES = ('<rect class="wshades" x="-11" y="-43" width="9" height="6" rx="2"/><rect class="wshades" x="2" y="-43" width="9" height="6" rx="2"/>'
          '<line class="wshadeb" x1="-2" y1="-41" x2="2" y2="-41"/>')
SUN = ('<g transform="translate(46,-86)">' + ''.join(f'<line class="wray" x1="{15*math.cos(math.radians(a)):.1f}" y1="{15*math.sin(math.radians(a)):.1f}" x2="{22*math.cos(math.radians(a)):.1f}" y2="{22*math.sin(math.radians(a)):.1f}"/>' for a in range(0, 360, 45))
       + '<circle class="wsun" r="11"/></g>')
WINDSCARF = ('<rect class="wscarf" x="-12" y="-31" width="24" height="6" rx="3"/>'
             '<path class="wscarfs" d="M-10,-28 q-14,-8 -26,1 q-12,8 -24,-1"/>'
             '<path class="wgust" d="M-70,-70 q20,-8 40,0"/><path class="wgust" d="M-76,-44 q16,-6 32,0"/>'
             '<g transform="translate(-58,-86) rotate(-25)"><path class="wleaf" d="M0,0 C4,-6 13,-6 16,0 C13,6 4,6 0,0 Z"/></g>')
FOG = '<path class="wfog" d="M-62,-58 q20,-6 40,0 t40,0 t40,0"/><path class="wfog" d="M-56,-22 q20,-6 40,0 t40,0 t40,0"/>'
MOON = ('<path class="wmoon" d="M112,-86 a18,18 0 1 0 12,32 a14,14 0 1 1 -12,-32 Z"/>'
        '<path class="wstar" d="M86,-96 l2,5 l5,2 l-5,2 l-2,5 l-2,-5 l-5,-2 l5,-2 Z"/><path class="wstar" d="M136,-70 l1.5,4 l4,1.5 l-4,1.5 l-1.5,4 l-1.5,-4 l-4,-1.5 l4,-1.5 Z"/>')

def sitcat(kind, before='', after='', cls=''):
    return f'<g class="wc {kind}{cls}">{before}<g class="wcat">{SIT[kind]}</g>{after}</g>'

def scenes():
    S = {}
    for k in ('calico', 'tux'):
        S.setdefault('rain', []).append(sitcat(k, RAIN, UMB))
        S.setdefault('pour', []).append(sitcat(k, RAIN + PUD + ''.join(drop(x, y) for x, y in [(-40, -80), (36, -86), (-64, -36), (66, -30)]), UMB))
        S.setdefault('storm', []).append(sitcat(k, RAIN + BOLT, UMB))
        S.setdefault('snow', []).append(sitcat(k, SNOWF, HAT + SCARF))
        S.setdefault('wind', []).append(sitcat(k, '', WINDSCARF))
        S.setdefault('fog', []).append(sitcat(k, '', FOG, ' foggy'))
        S.setdefault('sun', []).append(sitcat(k, SUN, SHADES))
        S.setdefault('night', []).append(f'<g class="wc {k}"><g transform="translate(-46,0)"><g class="wcat">{SLEEP[k]}</g></g>{MOON}</g>')
    return S

CSS_KINDLE = '''
.wumb { fill: #4a4a4a; } .wshaft { stroke: #4a4a4a; } .wdrop { fill: #8a8a8a; } .wpud { fill: #d0d0d0; } .wbolt { fill: #000; }
.what, .whatb { fill: #4a4a4a; } .wpom { fill: #fff; stroke: #4a4a4a; stroke-width: 1.5; } .wscarf { fill: #6a6a6a; } .wscarfs { stroke: #6a6a6a; }
.wflake line { stroke: #8a8a8a; } .wshades { fill: #000; } .wshadeb { stroke: #000; } .wray { stroke: #9a9a9a; } .wsun { fill: #c8c8c8; }
.wgust { stroke: #bdbdbd; } .wleaf { fill: #8a8a8a; } .wfog { stroke: #d8d8d8; } .wmoon { fill: #bdbdbd; } .wstar { fill: #8a8a8a; }
'''

CONDITIONS = {
    'rainy': 'rain', 'pouring': 'pour', 'lightning': 'storm',
    'lightning-rainy': 'storm', 'snowy': 'snow', 'snowy-rainy': 'snow',
    'hail': 'snow', 'fog': 'fog', 'windy': 'wind', 'windy-variant': 'wind',
    'sunny': 'sun', 'clear-night': 'night', 'exceptional': 'storm',
}
SPOTS = {'a': 'translate(400,726) scale(0.85)',
         'b': 'translate(905,974) scale(0.85)',
         'c': 'translate(330,1148) scale(0.85)'}
HATP = ('<path d="M-17,0 Q0,-5 17,0 Q0,4 -17,0 Z" fill="#000"/>'
        '<path d="M-11,-1 L11,-1 Q6,-14 5,-22 Q10,-27 17,-22 Q8,-22 2,-17 Q-6,-9 -11,-1 Z" fill="#000"/>'
        '<path d="M-8,-4 L8,-4 L7,-8 L-7,-8 Z" fill="#8a8a8a"/>')
HATS = {'a': 'translate(0,-46) rotate(-6)', 'b': 'translate(60,-39) rotate(8)',
        'c': 'translate(59,-27) rotate(12)'}

def selection(timestamp, condition, halloween=False):
    """Return weather scene, weather cat breed and visitor locations.

    Match the unsigned 32-bit arithmetic of the original JavaScript rule.
    The weather cat counts toward the limit of one of each breed.
    """
    scene = None if halloween else CONDITIONS.get(str(condition or '').lower().replace('_', '-').replace(' ', '-'))
    weather_cat = ('tux' if int(timestamp // 3600) % 2 else 'calico') if scene else None
    h = ((int(timestamp // 300) ^ 0x5bd1e995) * 0x2c1b3c6d) & 0xffffffff
    h = (h ^ (h >> 15)) & 0xffffffff
    h = (h * 0x297a2d39) & 0xffffffff
    h = (h ^ (h >> 12)) & 0xffffffff
    visitors = {}
    if h % 100 < 22:
        mode, first = (h >> 8) % 3, (h >> 12) % 3
        second = (first + 1 + ((h >> 16) % 2)) % 3
        spots = 'abc'
        if mode != 1 and weather_cat != 'calico': visitors['calico'] = spots[first]
        if mode == 1 and weather_cat != 'tux': visitors['tux'] = spots[first]
        if mode == 2 and weather_cat != 'tux': visitors['tux'] = spots[second]
    return scene, weather_cat, visitors

def render(timestamp, condition=None, halloween=False):
    """Return standalone SVG fragments for the Kindle's 1072 x 1448 view."""
    scene, weather_cat, visitors = selection(timestamp, condition, halloween)
    if not scene and not visitors: return ''
    result = ['<g id="kindle-cats"><style>' + CSS_KINDLE +
              '.wc.foggy .wcat{opacity:.45}.wshaft{stroke-width:2.5;stroke-linecap:round}'
              '.wflake line{stroke-width:1.8;stroke-linecap:round}.wshadeb{stroke-width:1.5}'
              '.wray{stroke-width:2.5;stroke-linecap:round}.wscarfs{fill:none;stroke-width:5;stroke-linecap:round}'
              '.wgust{fill:none;stroke-width:2;stroke-linecap:round}.wfog{fill:none;stroke-width:5;stroke-linecap:round}'
              '</style>']
    if scene:
        scene_art = scenes()[scene][0 if weather_cat == 'calico' else 1]
        result.append(f'<g id="weather-cat" data-scene="{scene}" data-breed="{weather_cat}" transform="translate(610,209) scale(0.85)">{scene_art}</g>')
    for kind, spot in visitors.items():
        hat = f'<g transform="{HATS[spot]}">{HATP}</g>' if halloween else ''
        result.append(f'<g id="cat-{kind}-{spot}" transform="{SPOTS[spot]}">{P[f"cat_{kind}_{spot}"]}{hat}</g>')
    result.append('</g>')
    return ''.join(result)
