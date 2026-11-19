"""The main Kindle screen as an HTML + CSS page, rendered to a greyscale PNG by headless Chromium.

Blocks sit at the same fixed positions as the SVG picture (so the touch zones in device_surface still line up); inside a block the browser
does the text layout, wrapping, fonts and emoji. Anything this page does not draw yet (cooking, Halloween, an event-day header) is left to
kindle_render.svg(): supports() says False and Renderer.png() returns None, so the caller falls back.
"""
import datetime
import html
import math
import os
import re
import subprocess
import tempfile
import textwrap
import time
import cat_render
import festive_header
import kindle_render as K
import weather_icons

e = lambda v: html.escape(str(v if v is not None else ''), quote=True)
ART = K.ART

CSS = """*{box-sizing:border-box;margin:0;padding:0}html,body{width:1072px;height:1448px;background:#fff;overflow:hidden}
body{position:relative;font:28px Roboto,'DejaVu Sans',sans-serif;color:#000}.a{position:absolute}.n{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.b{font-weight:700}.r{text-align:right}.c{text-align:center}.ink{background:#000;color:#fff}.box{border:3px solid #000;border-radius:20px;background:#fff}.box.ink{background:#000}
.lb{font-size:25px;font-weight:700;letter-spacing:.08em}.sm{font-size:19px;font-weight:700;letter-spacing:.06em}.pill{border-radius:18px;background:#000;color:#fff;font-size:18px;font-weight:700;text-align:center;line-height:36px;letter-spacing:.04em}
.rule{height:3px;background:#000;left:48px;width:976px}.row{border:3px solid #000;border-radius:16px;display:flex;align-items:center;justify-content:space-between;padding:0 22px;font-weight:700;gap:16px}
.row.ink{background:#000;color:#fff}.row span:first-child{flex:1 1 auto;min-width:0}.row .d{font-size:18px;flex:0 1 auto;max-width:300px}.av{width:56px;height:56px;border-radius:50%;border:2px solid #000;background:#ddd;overflow:hidden;text-align:center;font:700 22px/52px Roboto,sans-serif}
.av img,.art{filter:grayscale(1);display:block}svg{display:block}"""


def supports(data, alerts=None, now=None):
    """True when this page can draw the picture; False sends the caller back to the SVG renderer."""
    if isinstance(data.get('cooking'), dict) and data['cooking'].get('running') is True:
        return False
    stamp = time.time() if now is None else now
    day = datetime.datetime.fromtimestamp(stamp).astimezone()
    if data.get('halloween_mode') == 'on' or (day.month, day.day) == (10, 31):
        return False
    event = next((x for x in (data.get('panels') or []) if isinstance(x, dict) and x.get('active') and x.get('slot') == 'occasion'), None)
    return not festive_header.style(event)[0]


def icon(condition, x, y, size):
    draw = weather_icons.ICONS.get(weather_icons.key(condition))
    return f'<svg class="a" style="left:{x}px;top:{y}px" viewBox="0 0 100 100" width="{size}" height="{size}">{draw()}</svg>' if draw else ''


def t(x, y, value, size=28, cls='', w=None, color=None):
    """A text line whose TOP is y (the SVG used baselines, so callers pass baseline - ascent)."""
    style = f'left:{x}px;top:{y}px;font-size:{size}px;line-height:1.15' + (f';width:{w}px' if w else '') + (f';color:{color}' if color else '')
    return f'<div class="a n {cls}" style="{style}">{e(value)}</div>'


def pill(x, y, w, label, size=18, h=36):
    return f'<div class="a pill" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;line-height:{h}px;font-size:{size}px">{e(label)}</div>'


def page(data, alerts=None, now=None):
    stamp = time.time() if now is None else now
    today = datetime.datetime.fromtimestamp(stamp).astimezone()
    p = []
    device = data.get('device') if isinstance(data.get('device'), dict) else {}
    charging = device.get('charging') is True
    panels = [x for x in (data.get('panels') or []) if isinstance(x, dict)]
    active = [x for x in panels if x.get('active')]
    event = next((x for x in active if x.get('slot') == 'occasion'), None)
    # header
    p.append(t(48, 24, today.strftime('%A'), 96, 'b') + t(48, 150, str(today.day) + ' ' + today.strftime('%B'), 52))
    info = 'Updated ' + today.strftime('%H:%M')
    if device.get('fresh') and device.get('battery') is not None:
        info += ' · ' + ('⚡ Plugged in' if charging else '🔋 Kindle ' + str(device['battery']) + '%')
    p.append(t(500, 38, info, 25, 'r', 524 if not charging else 470))
    if charging:
        p.append('<svg class="a" style="left:986px;top:36px" width="38" height="38" viewBox="0 0 38 38"><circle cx="19" cy="19" r="19"/><path d="M12 19a8 8 0 1 0 8-8M18 7l5 4-5 4" fill="none" stroke="#fff" stroke-width="3"/></svg>')
    for i, line in enumerate(festive_header.lines(event)):
        p.append(t(300, 86 + i * 30, line, min(25, max(12, int(380 / max(1, len(line)) * 1.5))), 'b r', 724))
    p.append(t(500, 144, data.get('sun') or 'Sun unavailable', 34, 'b r', 524))
    p.append('<div class="a rule" style="top:213px"></div>')
    # weather
    weather = data.get('weather')
    game = next((x for x in active if x.get('kind') == 'game'), None)
    media = next((x for x in active if x.get('kind') == 'media'), None)
    if weather:
        cur = weather.get('current') or {}
        facts = weather.get('facts') or {}
        daily = weather.get('daily') or []
        hours = weather.get('hours') or []
        cond = str(cur.get('condition') or 'Unavailable')
        cond = K.CONDITION_NAMES.get(cond, cond.replace('-', ' ').replace('_', ' ').capitalize())
        p.append(icon(cur.get('condition'), 48, 248, 125) + t(200, 250, str(cur.get('temperature', '–')) + '°', 110, 'b') + t(48, 362, cond, 37, 'b', 340))
        today_row = next((d for d in daily if d.get('label') == 'Today'), {})
        if today_row:
            p.append(t(48, 408, 'Today ' + str(today_row.get('low', '–')) + '° – ' + str(today_row.get('high', '–')) + '°', 28, '', 340))
        sky = str(facts.get('sky', 'Unavailable'))
        label = 'UV INDEX' if sky.startswith('UV ') else 'CLOUD COVER'
        for i, (key, value) in enumerate((('WIND', facts.get('wind', 'Unavailable')), ('NEXT RAIN', K.next_rain_text(facts.get('next_rain', 'Unavailable'))), (label, sky.removeprefix('UV ').removeprefix('Cloud ')))):
            p.append(t(425, 252 + i * 74, key, 19, 'sm', 200) + t(425, 272 + i * 74, value, 28, 'b', 200))
        bearing = facts.get('bearing')
        if isinstance(bearing, (int, float)) and not isinstance(bearing, bool) and math.isfinite(bearing):
            p.append(f'<svg class="a" style="left:560px;top:264px;transform:rotate({(bearing + 180) % 360:g}deg)" width="50" height="50" viewBox="-25 -25 50 50"><path d="M0,-16 L11,0 L4,0 L4,16 L-4,16 L-4,0 L-11,0 Z"/></svg>')
        if game:
            art = game.get('art')
            p.append('<div class="a box" style="left:640px;top:238px;width:384px;height:216px"></div>')
            if isinstance(art, str) and ART.fullmatch(art):
                p.append(f'<img class="a art" style="left:653px;top:251px;width:140px;height:190px;object-fit:contain" src="{art}">')
            lines = textwrap.wrap(str(game.get('detail') or game.get('label') or 'Game'), width=14)
            if len(lines) > 3:
                lines = lines[:2] + [' '.join(lines[2:])]
            p.append(t(806, 262, 'NOW PLAYING', 18, 'b', 200) + ''.join(t(806, 292 + i * 32, ln, 24, 'b', 200) for i, ln in enumerate(lines)))
        else:
            for i, day in enumerate([d for d in daily if d.get('label') != 'Today'][:2]):
                y = 238 + i * 118
                name = day.get('label', '')
                if name == 'Next day':
                    name = (today + datetime.timedelta(days=2)).strftime('%A')
                rain = day.get('precipitation')
                text = str(day.get('low', '–')) + '–' + str(day.get('high', '–')) + '°'
                text += (' ' + K.num(rain) + ' mm rain') if isinstance(rain, (int, float)) and not isinstance(rain, bool) and rain >= 0.1 else ' dry'
                p.append(f'<div class="a box" style="left:640px;top:{y}px;width:384px;height:98px"></div>' + t(662, y + 14, name, 30, 'b', 250) + t(662, y + 56, text, 23, '', 250) + icon(day.get('condition'), 935, y + 10, 70))
    else:
        p.append(t(48, 300, 'Weather unavailable' if data.get('weather_configured') else 'Weather not configured', 30))
    # warning banner, media, or the hourly strip
    entries = [x for x in (alerts.get('alerts') or []) if isinstance(x, dict) and x.get('title')] if isinstance(alerts, dict) else []
    life = [str(x['title']) for x in entries if x.get('kind') in ('smoke', 'co')]
    motion = [str(x.get('label')) + (': ' + str(x.get('detail')) if x.get('detail') else '') for x in active if x.get('label') == 'Motion while away' and x.get('urgent') is True]
    other = motion + [str(x['title']) for x in entries if x.get('kind') not in ('smoke', 'co')]
    status = 'Alert sources not configured' if isinstance(alerts, dict) and alerts.get('configured') is False else 'Alert sources unavailable' if alerts is None or (isinstance(alerts, dict) and not alerts.get('complete')) else ''
    slots = max(0, 4 - min(len(life), 2) - (1 if status else 0))
    pages = max(1, (len(other) + slots - 1) // slots) if slots else 1
    page_no = int(stamp // 300) % pages
    warning = life[:2] + (other[page_no * slots:page_no * slots + slots] if slots else []) + ([status] if status else [])
    if warning:
        p.append('<div class="a box ink" style="left:48px;top:478px;width:976px;height:190px"></div><div class="a" style="left:60px;top:490px;width:952px;height:166px;border:3px dashed #fff;border-radius:16px"></div>')
        for x in (135, 937):
            p.append(f'<svg class="a" style="left:{x - 56}px;top:515px" width="112" height="90" viewBox="-56 -58 112 98"><path d="M0,-50 L48,35 L-48,35 Z" fill="#fff" stroke="#fff" stroke-width="7" stroke-linejoin="round"/><text y="25" text-anchor="middle" font-family="Roboto,sans-serif" font-size="58" font-weight="bold">!</text></svg>')
        few = len(warning) <= 2
        for i, item in enumerate(warning):
            if few:
                x, y, w, size = 211, (565 + i * 48 if len(warning) == 2 else 579), 650, 30   # y = the text baseline, as in the SVG
            else:
                x, y, w, size = (195 if i % 2 == 0 else 567), 555 + (i // 2) * 48, 310, 23
            p.append(f'<div class="a n b c" style="left:{x}px;top:{y - round(size * 0.9)}px;width:{w}px;font-size:{size}px;color:#fff">{e(item)}</div>')
        if pages > 1:
            p.append(t(900, 632, f'{page_no + 1}/{pages}', 18, 'b r', 95, '#fff'))
        if data.get('allow_alert_dismiss') is True and K.dismissible_alert(data, alerts, stamp):
            p.append('<div class="a" style="left:872px;top:612px;width:142px;height:52px;border-radius:20px;background:#fff;color:#000;font:700 20px/52px Roboto,sans-serif;text-align:center">DISMISS</div>')
    elif media:
        art = media.get('art')
        p.append('<div class="a box" style="left:48px;top:478px;width:976px;height:190px"></div>')
        if isinstance(art, str) and ART.fullmatch(art):
            p.append(f'<img class="a art" style="left:62px;top:493px;width:160px;height:160px;object-fit:contain" src="{art}">')
        parts = str(media.get('detail') or '').split(' · ', 1)
        p.append(t(245, 504, 'NOW PLAYING', 20, 'b') + t(245, 540, parts[0] or str(media.get('label') or 'Playing'), 30, 'b', 755) + t(245, 595, parts[1] if len(parts) > 1 else '', 23, '', 755))
    elif weather:
        p.append('<div class="a box" style="left:48px;top:478px;width:976px;height:190px"></div>')
        for i, h in enumerate((weather.get('hours') or [])[:6]):
            x = 130 + i * 163
            rain = h.get('precipitation')
            p.append(t(x - 70, 497, h.get('time', ''), 25, 'b c', 140) + icon(h.get('condition'), x - 30, 532, 60) + t(x - 70, 598, str(h.get('temperature', '–')) + '°', 36, 'b c', 140))
            if isinstance(rain, (int, float)) and not isinstance(rain, bool) and rain > 0.1:
                p.append(t(x - 70, 640, K.num(rain) + ' mm', 17, 'c', 140))
            if i:
                p.append(f'<div class="a" style="left:{x - 80}px;top:496px;width:2px;height:152px;background:#aaa"></div>')
    # house tiles
    p.append(t(48, 688, 'HOUSE', 25, 'lb'))
    alarm = next((x for x in panels if x.get('label') == 'Alarm'), {})
    home = next((x for x in panels if x.get('label') == 'Home'), {})
    doors = next((x for x in panels if str(x.get('label', '')).startswith('Doors')), {})
    heat = next((x for x in panels if str(x.get('label', '')).startswith(('Heat', 'Heating', 'Floors'))), {})
    for i, (title, value) in enumerate((('ALARM', alarm.get('detail')), ('HOME', home.get('detail')), ('DOORS', K.cap(doors.get('label', '').removeprefix('Doors '))), ('HEAT', K.cap(heat.get('label', '').removeprefix('Heat '))))):
        x = 48 + i * 248
        ink = title == 'DOORS' and doors.get('urgent') is True
        p.append(f'<div class="a box{" ink" if ink else ""}" style="left:{x}px;top:726px;width:232px;height:140px"></div>' + t(x + 20, 742, title, 21, 'sm', 192, '#fff' if ink else None))
        persons = (home.get('persons') or []) if title == 'HOME' else []
        if persons:
            for j, person in enumerate(persons[:3]):
                art = person.get('art') if isinstance(person, dict) else None
                inner = f'<img src="{art}" width="56" height="56" style="object-fit:cover">' if isinstance(art, str) and ART.fullmatch(art) else e((person.get('name') or '?')[:1])
                p.append(f'<div class="a av" style="left:{x + 20 + j * 64}px;top:788px">{inner}</div>')
        else:
            color = ';color:#fff' if ink else ''
            p.append(f'<div class="a n b" style="left:{x + 20}px;top:787px;width:192px;font-size:23px{color}">{e(value or "Unavailable")}</div>')
        sub = heat.get('detail') if title == 'HEAT' else (doors.get('detail') or ('all locked' if doors.get('label') == 'Doors locked' else '')) if title == 'DOORS' else ''
        if sub:
            p.append(f'<div class="a n" style="left:{x + 20}px;top:827px;width:192px;font-size:18px{";color:#fff" if ink else ""}">{e(sub)}</div>')
    if charging and isinstance(data.get('cooking'), dict) and 16 <= today.hour < 19:
        p.append('<div class="a box ink c" style="left:48px;top:726px;width:232px;height:140px;font-size:30px;font-weight:700;padding-top:36px;line-height:1.4">COOK<br>DINNER</div>')
    # lights
    lights = next((x for x in panels if x.get('label') == 'Lights on'), {})
    p.append(t(48, 892, 'LIGHTS ON', 25, 'lb'))
    floors = lights.get('floors')
    if isinstance(floors, list) and floors:
        for i, floor in enumerate(floors[:4]):
            rooms = ', '.join(floor.get('rooms') or []) or '–'
            x, y = 48 + (i % 2) * 488, 932 + (i // 2) * 44
            p.append(t(x, y, str(floor.get('floor', '')), 26, 'b', 138) + t(x + 140, y, rooms if len(rooms) <= 26 else rooms[:25] + '…', 26, '', 340))
    else:
        p.append(t(48, 934, lights.get('detail') or 'Unavailable', 26, '', 950))
    if charging:
        p.append(pill(270, 884, 140, 'ALL OFF') + pill(880, 884, 144, 'MENU'))
    # energy
    p.append('<div class="a rule" style="top:1030px"></div>')
    energy = next((x for x in panels if x.get('label') == 'Energy'), {})
    detail = energy.get('detail') or 'Energy unavailable'
    left, cost = energy.get('kindle_left') or detail, energy.get('kindle_cost') or ''
    if str(detail).startswith('Saving'):
        p.append(f'<div class="a box ink n b" style="left:48px;top:1040px;width:976px;height:48px;padding:7px 17px;font-size:22px">{e(left)}</div>')
    else:
        p.append(t(48, 1042, left, 25, 'b', 560 if cost else 976) + (t(624, 1042, cost, 25, 'b r', 400) if cost else ''))
    if data.get('cooking_unavailable') is True:
        p.append(t(500, 1070, 'Cooking unavailable · controls paused', 18, 'b r', 524))
    # to-do
    tasks = data.get('tasks')
    offset = device.get('page_offset') if type(device.get('page_offset')) is int else 0
    rows, page_idx, page_cnt, grouped = K.task_page(tasks, offset, stamp)
    partial = data.get('tasks_partial') is True
    p.append(t(48, 1117, 'TO DO' + (f' · {page_idx + 1}/{page_cnt}' if page_cnt > 1 else ''), 25, 'lb'))
    if partial:
        p.append(t(500, 1120, 'Some task sources unavailable', 18, 'r', 524))
    elif isinstance(tasks, list) and tasks and not grouped:
        p.append(t(500, 1120, 'Task grouping unavailable', 18, 'r', 524))
    elif charging and page_cnt > 1:
        p.append(pill(800, 1106, 108, 'PREV') + pill(916, 1106, 108, 'NEXT'))
    if tasks is None:
        p.append(t(48, 1158, 'Task sources unavailable' if data.get('tasks_configured') else 'Task sources not configured', 25))
    elif not rows and partial:
        p.append(t(48, 1158, 'Task sources unavailable', 25))
    elif len(rows) <= 2:
        if not rows:
            p.append(t(70, 1183, 'No tasks', 23, 'b'))
        for i, task in enumerate(rows):
            x = 48 + i * 494
            ink = task.get('urgent') is True
            title = (K.ENVELOPE if task.get('display_group') == 'message' else '') + str(task.get('title', ''))
            p.append(f'<div class="a box{" ink" if ink else ""}" style="left:{x}px;top:1150px;width:482px;height:144px;padding:17px 21px;font-size:23px;font-weight:700;line-height:32px;{"color:#fff" if ink else ""}">'
                     f'<div style="max-height:64px;overflow:hidden">{e(title)}</div><div style="position:absolute;left:21px;top:104px;font-size:19px;width:430px" class="n">{e(K.detail_text(task.get("detail") or ""))}</div></div>')
        rooms = [x for x in panels if x.get('kind') == 'room'][:10]
        if rooms:
            p.append(t(48, 1315, 'ROOMS', 25, 'lb'))
            for i, room in enumerate(rooms):
                x = 97 + i * 97
                p.append(t(x - 46, 1362, room.get('label', ''), 14, 'c', 92) + t(x - 46, 1403, str(room.get('detail', '–')), 20, 'b c', 92))
    else:
        h, pitch, font = {3: (87, 99, 29), 4: (61, 75, 27), 5: (50, 59, 24)}[min(len(rows), 5)]
        for i, task in enumerate(rows[:5]):
            y = 1150 + i * pitch
            ink = task.get('urgent') is True
            title = (K.ENVELOPE if task.get('display_group') == 'message' else '') + str(task.get('title', ''))
            d = K.detail_text(task.get('detail') or '')
            p.append(f'<div class="a row{" ink" if ink else ""}" style="left:48px;top:{y}px;width:976px;height:{h}px;font-size:{font}px"><span class="n">{e(title)}</span>' + (f'<span class="d n r">{e(d)}</span>' if d else '') + '</div>')
    weather_now = (data.get('weather') or {}).get('current') or {}
    overlay = cat_render.render(stamp, weather_now.get('condition'), False)
    if overlay:
        p.append(f'<svg class="a" style="left:0;top:0" width="1072" height="1448" viewBox="0 0 1072 1448">{overlay}</svg>')
    return '<!doctype html><meta charset="utf-8"><style>' + CSS + '</style><body>' + ''.join(p) + '</body>'


def to_grey(path):
    """Greyscale PNG (the Kindle shows 8-bit grey) from a Chromium screenshot; None if Pillow is missing."""
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(path) as image:
        out = os.path.splitext(path)[0] + '-grey.png'
        image.convert('L').save(out, optimize=True)
    return out


class Renderer:
    """Chromium is started per picture (about 200 MB while it runs), so nothing stays resident between renders."""
    ARGS = ('--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--hide-scrollbars', '--force-device-scale-factor=1',
            '--window-size=1072,1448', '--virtual-time-budget=3000', '--run-all-compositor-stages-before-draw')

    def __init__(self, chromium=None, run=subprocess.run, grey=to_grey, timeout=30):
        self.chromium = chromium or next((c for c in ('/usr/bin/chromium', '/usr/bin/chromium-browser') if os.path.exists(c)), 'chromium')
        self.run, self.grey, self.timeout = run, grey, timeout

    def png(self, data, alerts=None, now=None):
        """-> PNG bytes, or None when the page cannot draw this state or Chromium fails (the caller then uses the SVG)."""
        if not supports(data, alerts, now):
            return None
        try:
            with tempfile.TemporaryDirectory(prefix='kindle-html-') as folder:
                source, shot = os.path.join(folder, 'page.html'), os.path.join(folder, 'shot.png')
                with open(source, 'w', encoding='utf-8') as f:
                    f.write(page(data, alerts, now))
                self.run([self.chromium, *self.ARGS, '--screenshot=' + shot, 'file://' + source], capture_output=True, timeout=self.timeout, check=True,
                         env=dict(os.environ, HOME=folder))
                grey = self.grey(shot)
                if not grey:
                    return None
                with open(grey, 'rb') as f:
                    value = f.read()
            return value if value.startswith(b'\x89PNG\r\n\x1a\n') and 4096 < len(value) < 8 * 1024 * 1024 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
