"""Halloween drawings for the Kindle, ported from the original dashboard.

The caller selects when to render these fragments and draws an inverted header
behind header_decor(). Fragments include their own SVG styles.
"""
# header_decor(): white-on-black pieces inside the (inverted) header; body_decor(): black-on-white pumpkin by the lights.
import math

BAT = ('M0,-6 L-4,-14 L-7,-6 Q-18,-14 -32,-8 Q-26,-2 -30,6 Q-20,0 -14,8 Q-8,2 0,10 Q8,2 14,8 Q20,0 30,6 Q26,-2 32,-8 Q18,-14 7,-6 L4,-14 Z')


def bat(x, y, s, flip=False):
    sx = -s if flip else s
    return f'<path class="hwf" transform="translate({x},{y}) scale({sx},{s})" d="{BAT}"/>'


def web(cx, cy, R=64):
    """Cobweb in the top-right corner: radial threads + sagging arcs."""
    ang = [0, 22.5, 45, 67.5, 90]
    pt = lambda r, a: (cx - r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
    out = []
    for a in ang:
        x, y = pt(R, a); out.append(f'M{cx},{cy} L{x:.1f},{y:.1f}')
    for r in (22, 42, R):
        d = 'M%.1f,%.1f' % pt(r, ang[0])
        for a0, a1 in zip(ang, ang[1:]):
            c = pt(r * 0.86, (a0 + a1) / 2); p = pt(r, a1)
            d += f' Q{c[0]:.1f},{c[1]:.1f} {p[0]:.1f},{p[1]:.1f}'
        out.append(d)
    return f'<path class="hwl" d="{" ".join(out)}"/>'


def spider(x, y):
    legs = ''.join(f'M{x},{y} L{x + sx * dx},{y + dy}' for sx in (-1, 1) for dx, dy in ((12, -8), (14, 0), (12, 9), (7, 15)))
    return (f'<path class="hwl" d="M{x},12 L{x},{y - 6} {legs}"/>'
            f'<circle class="hwf" cx="{x}" cy="{y}" r="8"/><circle class="hwf" cx="{x}" cy="{y - 9}" r="4.5"/>')


HEADER_VARIANT = 'A'   # 'A' = spider + bats + moon, fangs along the bar edge; 'B' = haunted house + bats, dripping edge


def fangs(y0=203, x0=40, x1=1032):
    """Black stalactite fangs hanging under the header bar (irregular heights), drawn black so the bar edge looks jagged."""
    hs = (14, 24, 12, 20, 28, 12, 18, 26, 14, 22)
    d, x, i, w = f'M{x0},{y0 - 2} ', x0, 0, 31
    while x < x1:
        d += f'L{x + w / 2:.0f},{y0 + hs[i % len(hs)]} L{x + w},{y0 - 2} '
        x += w; i += 1
    return f'<path class="hwd" d="{d}Z"/>'


def drips(y0=203, x0=40, x1=1032):
    """Black slime drips under the bar: round-ended blobs of different lengths."""
    out, x, i = [], x0 + 20, 0
    for ln in (14, 30, 12, 22, 36, 12, 26, 16, 32, 12, 24):
        if x > x1: break
        out.append(f'<rect class="hwd" x="{x - 6}" y="{y0 - 2}" width="12" height="{ln + 2}" rx="6"/><circle class="hwd" cx="{x}" cy="{y0 + ln}" r="9"/>' if ln > 20 else f'<rect class="hwd" x="{x - 7}" y="{y0 - 2}" width="14" height="{ln + 2}" rx="7"/>')
        x += 90 + (i % 3) * 8; i += 1
    return ''.join(out)


def house(x, y):
    """Haunted-house silhouette (white) standing on y, with black windows, a tower and a bent chimney. About 150 wide, 100 tall."""
    return (f'<g transform="translate({x},{y})">'
            '<path class="hwf" d="M0,0 L0,-46 L10,-46 L10,-60 L26,-60 L26,-46 L34,-46 L34,-70 L44,-96 L54,-70 L54,-46 L118,-46 L118,-62 L126,-62 L126,-46 L150,-46 L150,0 Z"/>'
            '<path class="hwf" d="M66,-46 L66,-80 L78,-80 L78,-46 Z"/>'
            '<path class="hwd" d="M40,-62 h8 v12 h-8 Z M16,-30 h10 v14 h-10 Z M92,-30 h10 v14 h-10 Z M120,-30 h10 v14 h-10 Z M64,0 v-24 q9,-12 18,0 v24 Z"/>'
            '</g>')


def moon_full(cx, cy, r):
    return (f'<circle class="hwf" cx="{cx}" cy="{cy}" r="{r}"/>'
            f'<circle class="hwd" cx="{cx - r * 0.3:.0f}" cy="{cy - r * 0.25:.0f}" r="{r * 0.16:.0f}"/><circle class="hwd" cx="{cx + r * 0.35:.0f}" cy="{cy + r * 0.2:.0f}" r="{r * 0.22:.0f}"/>'
            f'<circle class="hwd" cx="{cx - r * 0.1:.0f}" cy="{cy + r * 0.5:.0f}" r="{r * 0.1:.0f}"/>')


def header_decor():
    # the top row (y < 72) of the bar belongs to the status line, so the middle decor sits below it (x 470..690, y 90..200)
    if HEADER_VARIANT == 'B':
        p = [web(1054, 12, 56), spider(1010, 70), bat(500, 100, 0.6), bat(596, 112, 0.5, True), house(462, 203)]
        p += [f'<circle class="hwf" cx="{x}" cy="{y}" r="{r}"/>' for x, y, r in ((620, 92, 2.5), (470, 80, 2), (610, 150, 2))]
        p.append(drips())
    else:
        p = [web(1054, 12, 56), spider(1010, 70)]
        p += [bat(500, 108, 0.8), bat(568, 98, 0.55, True), bat(510, 170, 0.5)]
        p += ['<circle class="hwf" cx="572" cy="150" r="28"/>', '<circle cx="585" cy="140" r="24" fill="#000"/>']
        p += [f'<circle class="hwf" cx="{x}" cy="{y}" r="{r}"/>' for x, y, r in ((610, 96, 2.5), (612, 130, 2), (600, 180, 2.5), (480, 140, 2))]
        p.append(fangs())
    return ('<g id="k_hw_h"><style>#k_hw_h .hwf{fill:#fff} #k_hw_h .hwd{fill:#000} #k_hw_h .hwl{fill:none;stroke:#fff;stroke-width:2;stroke-linecap:round}</style>' + ''.join(p) + '</g>')


def pumpkin(x, y):
    return (f'<g transform="translate({x},{y})">'
            '<ellipse class="hwg" cx="0" cy="0" rx="50" ry="40"/>'
            '<path class="hwr" d="M-17,-38 Q-34,0 -17,38 M17,-38 Q34,0 17,38"/>'
            '<path class="hwk" d="M-5,-40 Q-4,-55 9,-57 L11,-50 Q3,-48 4,-40 Z"/>'
            '<path class="hwk" d="M-28,-8 L-13,-8 L-21,-22 Z M13,-8 L28,-8 L20,-22 Z M-4,3 L4,3 L0,-6 Z"/>'
            '<path class="hwk" d="M-30,10 L-22,22 L-14,12 L-6,24 L2,12 L10,24 L18,12 L30,10 Q0,40 -30,10 Z"/>'
            '</g>')


def body_decor():
    return ('<g id="k_hw_b"><style>#k_hw_b .hwg{fill:#d4d4d4;stroke:#000;stroke-width:4} #k_hw_b .hwr{fill:none;stroke:#000;stroke-width:3;stroke-linecap:round} #k_hw_b .hwk{fill:#000}</style>' + pumpkin(944, 984) + '</g>')
