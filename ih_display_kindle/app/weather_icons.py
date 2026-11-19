"""Weather icons for the Kindle picture, drawn as vector shapes in greys.

The original Kindle screen used colour emoji, which the e-ink picture shows in
greyscale. Symbola glyphs (0.5.1) were thinner and less readable at 125 px, so
every Home Assistant weather condition is now drawn here on a 100 x 100 grid.
No font is needed, so the icons look the same on every renderer.
"""

import math

INK, MID, LIGHT, SUN = '#222', '#555', '#e4e4e4', '#8c8c8c'

def _spokes(cx, cy, r0, r1, angles, width, color):
    d = ''.join(f'M{cx + r0 * math.sin(math.radians(a)):.1f} {cy - r0 * math.cos(math.radians(a)):.1f}L{cx + r1 * math.sin(math.radians(a)):.1f} {cy - r1 * math.cos(math.radians(a)):.1f}' for a in angles)
    return f'<path d="{d}" stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>'

def _sun(cx, cy, r, rays=True):
    out = _spokes(cx, cy, r + 6, r + 15, range(0, 360, 45), 5, MID) if rays else ''
    return out + f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{SUN}" stroke="{MID}" stroke-width="3"/>'

def _cloud(dx=0, dy=0, scale=1.0):
    return (f'<path transform="translate({dx} {dy}) scale({scale})" d="M18 70 C3 70 4 48 20 47 C24 27 50 27 58 42 C74 32 91 43 88 56 C99 60 96 72 82 72 Z" '
            f'fill="{LIGHT}" stroke="{INK}" stroke-width="3.5" stroke-linejoin="round"/>')

def _rain(xs, y0=78, y1=94):
    return ''.join(f'<path d="M{x} {y0}l-5 {y1 - y0}" stroke="{INK}" stroke-width="5" stroke-linecap="round"/>' for x in xs)

def _flake(cx, cy, r):
    arms = _spokes(cx, cy, -r, r, (0, 60, 120), 4, INK)
    pts = []
    for a in range(0, 360, 60):
        for side in (-35, 35):
            sx, sy = cx + r * .62 * math.sin(math.radians(a)), cy - r * .62 * math.cos(math.radians(a))
            ex, ey = sx + r * .3 * math.sin(math.radians(a + side)), sy - r * .3 * math.cos(math.radians(a + side))
            pts.append(f'M{sx:.1f} {sy:.1f}L{ex:.1f} {ey:.1f}')
    return arms + f'<path d="{"".join(pts)}" stroke="{INK}" stroke-width="3" stroke-linecap="round"/>'

def _bolt(dx=0, dy=0, scale=1.0):
    return (f'<path transform="translate({dx} {dy}) scale({scale})" d="M54 38 L34 68 L48 68 L40 96 L68 58 L53 58 L63 38 Z" '
            f'fill="{INK}" stroke="{INK}" stroke-width="2" stroke-linejoin="round"/>')

def _lines(ys, x0=14, x1=86):
    return ''.join(f'<path d="M{x0 + (i % 2) * 8} {y}H{x1 - (i % 2) * 8}" stroke="{MID}" stroke-width="5" stroke-linecap="round"/>' for i, y in enumerate(ys))

def _wind():
    return (f'<path d="M10 38H58a10 10 0 1 0-10-10" fill="none" stroke="{INK}" stroke-width="5" stroke-linecap="round"/>'
            f'<path d="M10 56H76a11 11 0 1 1-11 11" fill="none" stroke="{INK}" stroke-width="5" stroke-linecap="round"/>'
            f'<path d="M10 74H44a9 9 0 1 1-9 9" fill="none" stroke="{MID}" stroke-width="5" stroke-linecap="round"/>')

ICONS = {
    'sunny': lambda: _sun(50, 50, 22),
    'clear-night': lambda: (f'<path d="M62 14 A38 38 0 1 0 88 66 A31 31 0 1 1 62 14 Z" fill="{SUN}" stroke="{MID}" stroke-width="3" stroke-linejoin="round"/>'
                            f'<path d="M80 18l2 6 6 2-6 2-2 6-2-6-6-2 6-2z" fill="{INK}"/>'),
    'cloudy': lambda: _cloud(-2, -4, 1.1),
    'partlycloudy': lambda: _sun(34, 33, 17) + _cloud(12, 12, .88),
    'rainy': lambda: _sun(33, 30, 15) + _cloud(12, 4, .84) + _rain((44, 62, 80), 84, 98),
    'pouring': lambda: _cloud(-2, -8, 1.1) + _rain((26, 44, 62, 80)),
    'snowy': lambda: _flake(50, 50, 40),
    'snowy-rainy': lambda: _cloud(-2, -12, 1.05) + _rain((26, 70), 76, 94) + _flake(48, 85, 10),
    'lightning': lambda: _bolt(0, -4),
    'lightning-rainy': lambda: _cloud(-2, -20, 1.05) + _bolt(24, 38, .62) + _rain((20, 78), 74, 92),
    'fog': lambda: _cloud(-2, -14, .95) + _lines((74, 86, 98)),
    'hail': lambda: _cloud(-2, -12, 1.05) + ''.join(f'<circle cx="{x}" cy="{y}" r="4.5" fill="{INK}"/>' for x, y in ((26, 84), (46, 92), (66, 84), (84, 92))),
    'windy': _wind,
    'windy-variant': _wind,
    'exceptional': lambda: (f'<path d="M50 12 L92 86 H8 Z" fill="{LIGHT}" stroke="{INK}" stroke-width="5" stroke-linejoin="round"/>'
                            f'<path d="M50 38v26" stroke="{INK}" stroke-width="8" stroke-linecap="round"/><circle cx="50" cy="75" r="5" fill="{INK}"/>'),
}

def key(condition):
    return str(condition or '').lower().replace('_', '-').replace(' ', '-')

def known(condition):
    return key(condition) in ICONS

def svg(condition, x, y, size):
    draw = ICONS.get(key(condition))
    if not draw:
        return ''
    return f'<g aria-label="{key(condition)}" transform="translate({x:g} {y:g}) scale({size / 100:g})">{draw()}</g>'
