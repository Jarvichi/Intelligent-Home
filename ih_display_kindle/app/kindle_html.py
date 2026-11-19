"""The main Kindle screen: this module prepares the values, templates/main.html says what is on the screen and templates/kindle.css says where.

Headless Chromium renders the page to a greyscale PNG. A folder of your own templates (see DOCS.md) is searched before the built-in ones, so the
layout can be changed without touching Python. Anything the page does not draw yet (Halloween, an event-day header) is left to kindle_render.svg():
supports() says False and Renderer.png() returns None, so the caller falls back. The same happens if a template is broken or Chromium fails.
"""
import datetime
import math
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import time
import cat_render
import festive_header
import kindle_render as K
import weather_icons

ART = K.ART
BUILTIN = pathlib.Path(__file__).resolve().parent / 'templates'


class Safe(str):
    """Markup the template must not escape (our own inline SVG); no markupsafe import needed."""
    def __html__(self):
        return str(self)


class Templates:
    """Jinja2 environment: your template folder first (if given), then the built-in templates. jinja2 is imported only when first used."""
    def __init__(self, override=None):
        import jinja2
        loaders = [jinja2.FileSystemLoader(str(override))] if override and os.path.isdir(str(override)) else []
        loaders.append(jinja2.FileSystemLoader(str(BUILTIN)))
        self.loader = jinja2.ChoiceLoader(loaders)
        self.env = jinja2.Environment(loader=self.loader, autoescape=True, undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True)

    def render(self, name, **context):
        css = Safe(self.loader.get_source(self.env, 'kindle.css')[0])   # read each time, so an edited stylesheet applies at once
        return self.env.get_template(name).render(css=css, **context)


_templates = None
_override = None


def configure(override=None):
    """Set the folder searched before the built-in templates (None = built-in only)."""
    global _templates, _override
    _override, _templates = override, None


def templates():
    global _templates
    if _templates is None:
        _templates = Templates(_override)
    return _templates


def render(name, **context):
    return templates().render(name, **context)


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


def icon(condition, size):
    draw = weather_icons.ICONS.get(weather_icons.key(condition))
    return Safe(f'<svg viewBox="0 0 100 100" width="{size}" height="{size}">{draw()}</svg>') if draw else Safe('')


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def context(data, alerts=None, now=None):
    """Everything main.html shows, as plain values. No layout here."""
    stamp = time.time() if now is None else now
    today = datetime.datetime.fromtimestamp(stamp).astimezone()
    device = data.get('device') if isinstance(data.get('device'), dict) else {}
    charging = device.get('charging') is True
    panels = [x for x in (data.get('panels') or []) if isinstance(x, dict)]
    active = [x for x in panels if x.get('active')]
    event = next((x for x in active if x.get('slot') == 'occasion'), None)
    info = 'Updated ' + today.strftime('%H:%M')
    if device.get('fresh') and device.get('battery') is not None:
        info += ' · ' + ('⚡ Plugged in' if charging else '🔋 Kindle ' + str(device['battery']) + '%')
    c = {'day': today.strftime('%A'), 'date': str(today.day) + ' ' + today.strftime('%B'), 'info': info, 'mains': charging, 'refresh': charging,
         'festive': [{'text': line, 'size': min(25, max(12, int(380 / max(1, len(line)) * 1.5)))} for line in festive_header.lines(event)],
         'sun': data.get('sun') or 'Sun unavailable', 'house_label': 'HOUSE', 'buttons': charging, 'cook_unavailable': data.get('cooking_unavailable') is True}
    # weather
    weather = data.get('weather')
    game = next((x for x in active if x.get('kind') == 'game'), None)
    media = next((x for x in active if x.get('kind') == 'media'), None)
    c['weather_msg'] = 'Weather unavailable' if data.get('weather_configured') else 'Weather not configured'
    c['wx'] = None
    if weather:
        cur, facts = weather.get('current') or {}, weather.get('facts') or {}
        daily, hours = weather.get('daily') or [], weather.get('hours') or []
        cond = str(cur.get('condition') or 'Unavailable')
        cond = K.CONDITION_NAMES.get(cond, cond.replace('-', ' ').replace('_', ' ').capitalize())
        today_row = next((d for d in daily if d.get('label') == 'Today'), {})
        sky = str(facts.get('sky', 'Unavailable'))
        label = 'UV INDEX' if sky.startswith('UV ') else 'CLOUD COVER'
        bearing = facts.get('bearing')
        wx = {'icon': icon(cur.get('condition'), 125), 'temp': str(cur.get('temperature', '–')) + '°', 'cond': cond,
              'range': ('Today ' + str(today_row.get('low', '–')) + '° – ' + str(today_row.get('high', '–')) + '°') if today_row else '',
              'facts': [{'l': k, 'v': v} for k, v in (('WIND', facts.get('wind', 'Unavailable')), ('NEXT RAIN', K.next_rain_text(facts.get('next_rain', 'Unavailable'))),
                                                       (label, sky.removeprefix('UV ').removeprefix('Cloud ')))],
              'arrow': None, 'game': None, 'days': []}
        if number(bearing) and math.isfinite(bearing):
            wx['arrow'] = f'{(bearing + 180) % 360:g}'
        if game:
            art = game.get('art')
            lines = textwrap.wrap(str(game.get('detail') or game.get('label') or 'Game'), width=14)
            wx['game'] = {'art': art if isinstance(art, str) and ART.fullmatch(art) else None, 'lines': lines[:2] + [' '.join(lines[2:])] if len(lines) > 3 else lines}
        else:
            for day in [d for d in daily if d.get('label') != 'Today'][:2]:
                name = day.get('label', '')
                if name == 'Next day':
                    name = (today + datetime.timedelta(days=2)).strftime('%A')
                rain = day.get('precipitation')
                text = str(day.get('low', '–')) + '–' + str(day.get('high', '–')) + '°'
                text += (' ' + K.num(rain) + ' mm rain') if number(rain) and rain >= 0.1 else ' dry'
                wx['days'].append({'name': name, 'text': text, 'icon': icon(day.get('condition'), 70)})
        c['wx'] = wx
    # the band under the weather: warning banner, now playing, or the hourly strip
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
        few = len(warning) <= 2
        lines = [{'text': w, 'cls': ('warn-1' if len(warning) == 1 else 'warn-2') if few else 'warn-n', 'i': i, 'c': i % 2, 'r': i // 2} for i, w in enumerate(warning)]
        c['band'] = {'kind': 'warn', 'lines': lines, 'page': f'{page_no + 1}/{pages}' if pages > 1 else '',
                     'dismiss': data.get('allow_alert_dismiss') is True and bool(K.dismissible_alert(data, alerts, stamp))}
    elif media:
        art = media.get('art')
        parts = str(media.get('detail') or '').split(' · ', 1)
        c['band'] = {'kind': 'media', 'art': art if isinstance(art, str) and ART.fullmatch(art) else None,
                     'title': parts[0] or str(media.get('label') or 'Playing'), 'sub': parts[1] if len(parts) > 1 else ''}
    elif weather:
        c['band'] = {'kind': 'hours', 'hours': [{'time': h.get('time', ''), 'icon': icon(h.get('condition'), 60), 'temp': str(h.get('temperature', '–')) + '°',
                                                  'rain': (K.num(h['precipitation']) + ' mm') if number(h.get('precipitation')) and h['precipitation'] > 0.1 else ''}
                                                 for h in (weather.get('hours') or [])[:6]]}
    else:
        c['band'] = {'kind': 'none'}
    # house tiles
    alarm = next((x for x in panels if x.get('label') == 'Alarm'), {})
    home = next((x for x in panels if x.get('label') == 'Home'), {})
    doors = next((x for x in panels if str(x.get('label', '')).startswith('Doors')), {})
    heat = next((x for x in panels if str(x.get('label', '')).startswith(('Heat', 'Heating', 'Floors'))), {})
    ink = doors.get('urgent') is True
    persons = [{'art': p.get('art') if isinstance(p, dict) and isinstance(p.get('art'), str) and ART.fullmatch(p['art']) else None,
                'initial': ((p.get('name') or '?')[:1]) if isinstance(p, dict) else '?'} for p in (home.get('persons') or [])[:3]]
    c['tiles'] = [
        {'title': 'ALARM', 'value': alarm.get('detail') or 'Unavailable', 'sub': '', 'ink': False, 'persons': None},
        {'title': 'HOME', 'value': home.get('detail') or 'Unavailable', 'sub': '', 'ink': False, 'persons': persons or None},
        {'title': 'DOORS', 'value': K.cap(doors.get('label', '').removeprefix('Doors ')) or 'Unavailable',
         'sub': doors.get('detail') or ('all locked' if doors.get('label') == 'Doors locked' else ''), 'ink': ink, 'persons': None},
        {'title': 'HEAT', 'value': K.cap(heat.get('label', '').removeprefix('Heat ')) or 'Unavailable', 'sub': heat.get('detail') or '', 'ink': False, 'persons': None}]
    c['cook_tile'] = charging and isinstance(data.get('cooking'), dict) and 16 <= today.hour < 19
    # lights
    lights = next((x for x in panels if x.get('label') == 'Lights on'), {})
    floors = lights.get('floors')
    c['lights'] = {'flat': lights.get('detail') or 'Unavailable', 'floors': None}
    if isinstance(floors, list) and floors:
        c['lights']['floors'] = [{'floor': str(f.get('floor', '')), 'rooms': (lambda r: r if len(r) <= 26 else r[:25] + '…')(', '.join(f.get('rooms') or []) or '–')} for f in floors[:4]]
    # energy
    energy = next((x for x in panels if x.get('label') == 'Energy'), {})
    detail = energy.get('detail') or 'Energy unavailable'
    c['energy'] = {'saving': str(detail).startswith('Saving'), 'left': energy.get('kindle_left') or detail, 'cost': energy.get('kindle_cost') or ''}
    # to do
    tasks = data.get('tasks')
    offset = device.get('page_offset') if type(device.get('page_offset')) is int else 0
    rows, page_idx, page_cnt, grouped = K.task_page(tasks, offset, stamp)
    partial = data.get('tasks_partial') is True
    note = 'Some task sources unavailable' if partial else 'Task grouping unavailable' if isinstance(tasks, list) and tasks and not grouped else ''
    def title(t):
        return (K.ENVELOPE if t.get('display_group') == 'message' else '') + str(t.get('title', ''))
    todo = {'label': 'TO DO' + (f' · {page_idx + 1}/{page_cnt}' if page_cnt > 1 else ''), 'note': note, 'paging': not note and charging and page_cnt > 1,
            'msg': '', 'empty': False, 'cards': [], 'rooms': [], 'rows': []}
    if tasks is None:
        todo['msg'] = 'Task sources unavailable' if data.get('tasks_configured') else 'Task sources not configured'
    elif not rows and partial:
        todo['msg'] = 'Task sources unavailable'
    elif len(rows) <= 2:
        todo['empty'] = not rows
        todo['cards'] = [{'ink': t.get('urgent') is True, 'title': title(t), 'detail': K.detail_text(t.get('detail') or '')} for t in rows]
        todo['rooms'] = [{'l': r.get('label', ''), 'v': str(r.get('detail', '–'))} for r in [x for x in panels if x.get('kind') == 'room'][:10]]
    else:
        todo['rows'] = [{'ink': t.get('urgent') is True, 'title': title(t), 'detail': K.detail_text(t.get('detail') or '')} for t in rows[:5]]
    c['todo'] = todo
    weather_now = (data.get('weather') or {}).get('current') or {}
    overlay = cat_render.render(stamp, weather_now.get('condition'), False)
    c['overlay'] = Safe(overlay) if overlay else ''
    return c


def page(data, alerts=None, now=None):
    return render('main.html', **context(data, alerts, now))


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
        self.last_error = ''

    def note(self, exc):
        text = type(exc).__name__ + ': ' + str(exc)[:200]
        if text != self.last_error:
            print('kindle html renderer fell back to SVG: ' + text, file=sys.stderr, flush=True)
        self.last_error = text

    def shoot(self, html_text):
        """One Chromium picture from a page -> greyscale PNG bytes, or None on any failure."""
        try:
            with tempfile.TemporaryDirectory(prefix='kindle-html-') as folder:
                source, shot = os.path.join(folder, 'page.html'), os.path.join(folder, 'shot.png')
                with open(source, 'w', encoding='utf-8') as f:
                    f.write(html_text)
                self.run([self.chromium, *self.ARGS, '--screenshot=' + shot, 'file://' + source], capture_output=True, timeout=self.timeout, check=True,
                         env=dict(os.environ, HOME=folder))
                grey = self.grey(shot)
                if not grey:
                    return None
                with open(grey, 'rb') as f:
                    value = f.read()
            return value if value.startswith(b'\x89PNG\r\n\x1a\n') and 4096 < len(value) < 8 * 1024 * 1024 else None
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.note(exc)
            return None

    def build(self, make):
        """Run a page builder; a broken template or odd data means 'use the SVG', never an error."""
        try:
            return self.shoot(make())
        except Exception as exc:   # jinja2.TemplateError, KeyError, TypeError ...: the SVG picture covers it
            self.note(exc)
            return None

    def screen(self, name, payload, alerts=None, now=None):
        """The other screens: 'dinner', 'home', 'birthdays', 'menu', 'cooking'. None = use the SVG picture."""
        import screens_html
        if name == 'cooking':
            return self.build(lambda: screens_html.cooking(payload, alerts)) if isinstance(payload, dict) else None
        make = {'dinner': screens_html.dinner, 'home': screens_html.home, 'birthdays': screens_html.birthdays, 'menu': screens_html.menu}.get(name)
        return self.build(lambda: make(payload, now)) if make else None

    def png(self, data, alerts=None, now=None):
        """-> PNG bytes, or None when the page cannot draw this state or Chromium fails (the caller then uses the SVG)."""
        if not supports(data, alerts, now):
            return None
        return self.build(lambda: page(data, alerts, now))
