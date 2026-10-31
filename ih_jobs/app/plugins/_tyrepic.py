"""Top-down car picture with the four tyre pressures, drawn in pure Python (no Pillow, no fonts, no browser). Not a plugin (leading underscore).
Matches the house-car-card `mode: tyres` look (line-art car, a PSI number by each wheel, red below 80% of target, amber below 90%, 'TARGET n PSI' under it).
render_png(psi, target_psi, targets=None, photo=None) -> PNG bytes (8-bit RGB, 400x380).
  photo: optional PNG bytes of a transparent top-down car picture (front at top). When it decodes, it replaces the drawn car and is placed like the card's tyre_image
  (box x 56..144, y 8..164 of the 200x190 card, fitted inside, clear of the target line); any decode problem falls back to the drawn car.
  psi: {'front_left': 29, 'front_right': 33, ...}; target_psi: a number or None; keys may be any of front_left/front_right/rear_left/rear_right (others are ignored).
Method: shapes are measured as distance fields on a 2x grid, then averaged down (smooth edges). Digits use a small built-in 5x7 block font (digits and the letters of PSI / TARGET)."""
import math
import struct
import zlib

W, H, SS = 400, 380, 2           # output size, supersampling factor
U = 2.0 * SS                      # card units (200 x 190) -> supersampled pixels
BG = (255, 255, 255)
INK = (60, 64, 67)
RED = (219, 68, 55)
AMBER = (255, 166, 0)
GREY = (150, 154, 158)

FONT = {
    '0': '01110 10001 10011 10101 11001 10001 01110', '1': '00100 01100 00100 00100 00100 00100 01110',
    '2': '01110 10001 00001 00010 00100 01000 11111', '3': '11110 00001 00001 01110 00001 00001 11110',
    '4': '00010 00110 01010 10010 11111 00010 00010', '5': '11111 10000 11110 00001 00001 10001 01110',
    '6': '00110 01000 10000 11110 10001 10001 01110', '7': '11111 00001 00010 00100 01000 01000 01000',
    '8': '01110 10001 10001 01110 10001 10001 01110', '9': '01110 10001 10001 01111 00001 00010 01100',
    'P': '11110 10001 10001 11110 10000 10000 10000', 'S': '01111 10000 10000 01110 00001 00001 11110',
    'I': '01110 00100 00100 00100 00100 00100 01110', 'T': '11111 00100 00100 00100 00100 00100 00100',
    'A': '01110 10001 10001 11111 10001 10001 10001', 'R': '11110 10001 10001 11110 10100 10010 10001',
    'G': '01110 10001 10000 10111 10001 10001 01111', 'E': '11111 10000 10000 11110 10000 10000 11111',
    '-': '00000 00000 00000 11111 00000 00000 00000', ' ': '00000 00000 00000 00000 00000 00000 00000',
}


def decode_png(data):
    """-> (w, h, [(r, g, b, a), ...] row-major, 0..255). Non-interlaced PNG, colour types 0/2/3/4/6, bit depths 1/2/4/8/16. Raises ValueError otherwise."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    pos, idat, plte, trns, hdr = 8, [], None, None, None
    while pos + 8 <= len(data):
        n, t = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if t == b'IHDR':
            hdr = struct.unpack('>IIBBBBB', body)
        elif t == b'PLTE':
            plte = body
        elif t == b'tRNS':
            trns = body
        elif t == b'IDAT':
            idat.append(body)
        elif t == b'IEND':
            break
    if not hdr or not idat:
        raise ValueError('bad PNG')
    w, h, depth, ctype, _c, _f, interlace = hdr
    if interlace or ctype not in (0, 2, 3, 4, 6) or depth not in (1, 2, 4, 8, 16) or w * h > 4000000 or (ctype == 3 and not plte):
        raise ValueError('unsupported PNG')
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bits = chans * depth
    bpp = max(1, bits // 8)
    stride = (w * bits + 7) // 8
    raw = zlib.decompress(b''.join(idat))
    if len(raw) < (stride + 1) * h:
        raise ValueError('short PNG data')
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        cur = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                cur[i] = (cur[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                cur[i] = (cur[i] + ((cur[i - bpp] if i >= bpp else 0) + prev[i]) // 2) & 255
        elif f == 4:
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                cur[i] += a if pa <= pb and pa <= pc else b if pb <= pc else c
                cur[i] &= 255
        elif f != 0:
            raise ValueError('bad filter')
        rows.append(cur)
        prev = cur
    maxv = (1 << depth) - 1
    tkey = None
    if trns and ctype == 0 and len(trns) >= 2:
        tkey = (struct.unpack('>H', trns[:2])[0],)
    elif trns and ctype == 2 and len(trns) >= 6:
        tkey = struct.unpack('>HHH', trns[:6])
    out = []
    for cur in rows:
        if depth == 8:
            vals = cur
        elif depth == 16:
            vals = [(cur[i] << 8) | cur[i + 1] for i in range(0, len(cur) - 1, 2)]
        else:
            vals = []
            per = 8 // depth
            for byte in cur:
                for k in range(per):
                    vals.append((byte >> (8 - depth * (k + 1))) & maxv)
        sc = (lambda v: v >> 8) if depth == 16 else (lambda v: v * 255 // maxv) if depth < 8 and ctype != 3 else (lambda v: v)
        for x in range(w):
            if ctype == 6:
                v = vals[x * 4:x * 4 + 4]
                out.append((sc(v[0]), sc(v[1]), sc(v[2]), sc(v[3])))
            elif ctype == 2:
                v = tuple(vals[x * 3:x * 3 + 3])
                out.append((sc(v[0]), sc(v[1]), sc(v[2]), 0 if tkey == v else 255))
            elif ctype == 4:
                g = sc(vals[x * 2])
                out.append((g, g, g, sc(vals[x * 2 + 1])))
            elif ctype == 0:
                g = sc(vals[x])
                out.append((g, g, g, 0 if tkey == (vals[x],) else 255))
            else:
                i = vals[x]
                out.append((plte[i * 3], plte[i * 3 + 1], plte[i * 3 + 2], trns[i] if trns and i < len(trns) else 255))
    return w, h, out


PHOTO_BOX = (56, 8, 88, 156)   # x, y, w, h in card units: like the card's .timg (left 28%, width 44%) but a bit shorter, because here the TARGET line sits inside the picture, not below it


class Canvas:
    def __init__(self):
        self.w, self.h = W * SS, H * SS
        self.px = [list(BG) for _ in range(self.w * self.h)]

    def photo(self, data):
        """Composite a transparent PNG (fitted inside PHOTO_BOX, bilinear) onto the canvas. Raises on bad data."""
        iw, ih, pix = decode_png(data)
        bx, by, bw, bh = [v * U for v in PHOTO_BOX]
        sc = min(bw / iw, bh / ih)
        dw, dh = max(1, int(iw * sc)), max(1, int(ih * sc))
        ox, oy = int(bx + (bw - dw) / 2), int(by + (bh - dh) / 2)
        px, w = self.px, self.w
        for y in range(dh):
            fy = min(ih - 1.001, max(0.0, (y + 0.5) / sc - 0.5))
            y0 = int(fy)
            wy = fy - y0
            for x in range(dw):
                fx = min(iw - 1.001, max(0.0, (x + 0.5) / sc - 0.5))
                x0 = int(fx)
                wx = fx - x0
                p00, p10, p01, p11 = pix[y0 * iw + x0], pix[y0 * iw + x0 + 1], pix[(y0 + 1) * iw + x0], pix[(y0 + 1) * iw + x0 + 1]
                ws = ((1 - wx) * (1 - wy), wx * (1 - wy), (1 - wx) * wy, wx * wy)
                a = sum(q[3] * k for q, k in zip((p00, p10, p01, p11), ws)) / 255.0
                if a <= 0.003:
                    continue
                dst = px[(oy + y) * w + ox + x]
                for ch in range(3):   # premultiplied average, then over the background
                    c = sum(q[ch] * q[3] * k for q, k in zip((p00, p10, p01, p11), ws)) / 255.0
                    dst[ch] = c + dst[ch] * (1 - a)

    def _paint(self, x0, y0, x1, y1, cov, col, alpha=1.0):
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(self.w - 1, int(x1) + 1), min(self.h - 1, int(y1) + 1)
        px, w = self.px, self.w
        for y in range(y0, y1 + 1):
            row = y * w
            for x in range(x0, x1 + 1):
                c = cov(x + 0.5, y + 0.5) * alpha
                if c > 0:
                    p = px[row + x]
                    p[0] += (col[0] - p[0]) * c
                    p[1] += (col[1] - p[1]) * c
                    p[2] += (col[2] - p[2]) * c

    # ---- shapes, all in card units (200 x 190) ----
    def rrect(self, x, y, w, h, r, col, stroke=None, alpha=1.0):
        """Filled (stroke None) or outlined rounded rectangle."""
        cx, cy, hw, hh = (x + w / 2) * U, (y + h / 2) * U, w / 2 * U, h / 2 * U
        r = min(r * U, hw, hh)

        def sdf(px, py):
            qx, qy = abs(px - cx) - (hw - r), abs(py - cy) - (hh - r)
            return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
        self._shape(sdf, (x - 2) * U, (y - 2) * U, (x + w + 2) * U, (y + h + 2) * U, col, stroke, alpha)

    def ellipse(self, cx, cy, rx, ry, col, stroke=None, alpha=1.0):
        cx, cy, rx, ry = cx * U, cy * U, rx * U, ry * U

        def sdf(px, py):
            return (math.hypot((px - cx) / rx, (py - cy) / ry) - 1) * min(rx, ry)
        self._shape(sdf, cx - rx - 2 * U, cy - ry - 2 * U, cx + rx + 2 * U, cy + ry + 2 * U, col, stroke, alpha)

    def poly(self, pts, col, stroke=1.5, closed=False, alpha=1.0):
        """Stroked polyline in card units."""
        pts = [(a * U, b * U) for a, b in pts]
        segs = list(zip(pts, pts[1:])) + ([(pts[-1], pts[0])] if closed else [])
        hw = stroke * U / 2

        def cov(px, py):
            best = 1e9
            for (ax, ay), (bx, by) in segs:
                dx, dy = bx - ax, by - ay
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
                d = math.hypot(px - ax - t * dx, py - ay - t * dy)
                if d < best:
                    best = d
            return max(0.0, min(1.0, hw - best + 0.5))
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        self._paint(min(xs) - hw - 2, min(ys) - hw - 2, max(xs) + hw + 2, max(ys) + hw + 2, cov, col, alpha)

    def _shape(self, sdf, x0, y0, x1, y1, col, stroke, alpha):
        if stroke is None:
            def cov(px, py):
                return max(0.0, min(1.0, 0.5 - sdf(px, py)))
        else:
            hw = stroke * U / 2

            def cov(px, py):
                return max(0.0, min(1.0, hw - abs(sdf(px, py)) + 0.5))
        self._paint(x0, y0, x1, y1, cov, col, alpha)

    def text(self, s, x, y, cell, col, anchor='start'):
        """Block text; x, y in OUTPUT pixels (top-left of the first glyph), cell = output pixels per font dot."""
        cell *= SS
        width = (len(s) * 6 - 1) * cell
        x0 = x * SS - (width if anchor == 'end' else width / 2 if anchor == 'middle' else 0)
        for i, ch in enumerate(s.upper()):
            rows = FONT.get(ch, FONT[' ']).split()
            for r, bits in enumerate(rows):
                for c, b in enumerate(bits):
                    if b == '1':
                        bx, by = x0 + (i * 6 + c) * cell, y * SS + r * cell
                        self._paint(bx, by, bx + cell, by + cell, lambda a, b_: 1.0, col)

    def png(self):
        raw = bytearray()
        for y in range(H):
            raw.append(0)
            for x in range(W):
                r = g = b = 0.0
                for dy in range(SS):
                    base = (y * SS + dy) * self.w + x * SS
                    for dx in range(SS):
                        p = self.px[base + dx]
                        r += p[0]; g += p[1]; b += p[2]
                n = SS * SS
                raw += bytes((int(r / n + .5), int(g / n + .5), int(b / n + .5)))

        def chunk(t, d):
            c = struct.pack('>I', len(d)) + t + d
            return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
        return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(bytes(raw), 6)) + chunk(b'IEND', b''))


def _bez(p0, p1, p2, n=8):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0], (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
            for t in [i / n for i in range(n + 1)]]


CORNERS = {  # key -> (x, y of the wheel rectangle, side)  in card units
    'front_left': (51, 38, 'l'), 'front_right': (140, 38, 'r'), 'rear_left': (51, 122, 'l'), 'rear_right': (140, 122, 'r')}
LABEL = {'front_left': (14, 22), 'front_right': (186, 22), 'rear_left': (14, 150), 'rear_right': (186, 150)}


def colour_for(psi, target):
    if not target:
        return INK
    r = psi / float(target)
    return RED if r < 0.8 else AMBER if r < 0.9 else INK


def _draw_car(c):
    for k, (x, y, _s) in CORNERS.items():
        c.rrect(x, y, 9, 30, 4, GREY, alpha=0.7)
    c.rrect(60, 14, 80, 162, 24, INK, stroke=1.5, alpha=0.7)
    for pts in (_bez((68, 33), (73, 26), (84, 28)), _bez((132, 33), (127, 26), (116, 28)), _bez((67, 60), (100, 52), (133, 60))):
        c.poly(pts, INK, 1.5, alpha=0.7)
    c.poly([(76, 66), (124, 66), (132, 94), (68, 94)], INK, 1.5, closed=True, alpha=0.7)
    c.ellipse(60, 92, 4.5, 3, INK, stroke=1.5, alpha=0.7)
    c.ellipse(140, 92, 4.5, 3, INK, stroke=1.5, alpha=0.7)
    c.rrect(72, 98, 56, 38, 9, INK, stroke=1.5, alpha=0.7)
    c.poly([(72, 112), (128, 112)], INK, 1.5, alpha=0.35)
    c.poly([(78, 141), (122, 141), (126, 153), (74, 153)], INK, 1.5, closed=True, alpha=0.7)
    c.poly([(66, 166), (78, 166)], INK, 1.5, alpha=0.7)
    c.poly([(122, 166), (134, 166)], INK, 1.5, alpha=0.7)


def render_png(psi, target_psi=None, targets=None, photo=None):
    """psi {pos: number}; target_psi one number for all (shown under the car) or None; targets {pos: number} per wheel colouring (default target_psi).
    photo: optional PNG bytes of a transparent top-down car; used instead of the drawn car when it decodes."""
    c = Canvas()
    done = False
    if photo:
        try:
            c.photo(photo)
            done = True
        except Exception:
            c = Canvas()   # bad picture: start clean and draw the car
    if not done:
        _draw_car(c)
    for k, (lx, ly) in LABEL.items():
        anchor = 'start' if k.endswith('left') else 'end'
        v = psi.get(k)
        tg = (targets or {}).get(k, target_psi)
        col = colour_for(v, tg) if v is not None else GREY
        # 7 dots high at 5 px a dot = 35 px; the card's number is ~26 units (52 px) tall incl. leading, so 7 px per dot would be too big
        c.text(str(int(round(v))) if v is not None else '-', lx * 2, (ly - 14) * 2, 5, col, anchor)
        c.text('PSI', lx * 2, (ly + 4) * 2, 2, GREY, anchor)
    if target_psi:
        c.text('TARGET %d PSI' % int(round(target_psi)), W / 2, 346, 3, INK, 'middle')
    return c.png()
