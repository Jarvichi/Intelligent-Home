"""The Kindle's other screens (dinner menu, who's home, birthdays, the Kindle menu, cooking) as HTML pages.

Same fixed positions as popup_render.py / device_menu.py / cooking_render.py, so the touch zones in device_surface.py still line up.
"""
import datetime
import re
from kindle_html import CSS, ART, e, t, pill

EATS = ('ASAP', '12:30', '13:30', '18:30')
CAPTIONS = ('As soon as ready', '12:30 lunch', '13:30 lunch', '18:30 dinner')
MENU_ROWS = ('COOK DINNER', 'WHO IS HOME', 'ALL LIGHTS OFF', 'REFRESH', 'WI-FI', 'BIRTHDAYS', 'BACK')
STRIPES = 'background:repeating-linear-gradient(45deg,#fff 0,#fff 9px,#bbb 9px,#bbb 13px)'
EXTRA = '.btn{border:3px solid #000;border-radius:52px;background:#fff;font-size:40px;font-weight:700;text-align:center;line-height:98px}.card{border:3px solid #000;border-radius:18px;background:#fff}.card.ink{background:#000;color:#fff}.chip{border:3px solid #000;border-radius:32px;font-size:27px;font-weight:700;text-align:center;line-height:58px}.chip.on{background:#000;color:#fff}'


def doc(parts, style=''):
    return '<!doctype html><meta charset="utf-8"><style>' + CSS + EXTRA + '</style><body' + (f' style="{style}"' if style else '') + '>' + ''.join(parts) + '</body>'


def div(x, y, w, h, cls='', inner='', style=''):
    return f'<div class="a {cls}" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;{style}">{inner}</div>'


def button(x, w, label, ink=False):
    return div(x, 1240, w, 104, 'btn' + (' ink' if ink else ''), e(label))


def dinner(data, now=None):
    p = [t(48, 30, "🍽 What's for dinner?", 56, 'b')]
    selected = data.get('selected_eat', 'ASAP') if isinstance(data, dict) else 'ASAP'
    selected = selected if selected in EATS else 'ASAP'
    for i, caption in enumerate(CAPTIONS):
        p.append(div(48 + i * 247, 112, 235, 64, 'chip' + (' on' if EATS[i] == selected else ''), e(caption), 'font-size:22px' if i == 0 else ''))
    items = data.get('items') if isinstance(data, dict) and isinstance(data.get('items'), list) else []
    if not isinstance(data, dict):
        p.append(t(76, 228, 'Cooking menu unavailable', 26))
    elif not items:
        p.append(t(76, 228, 'No recipes available', 26))
    for i, item in enumerate(items[:8]):
        if not isinstance(item, dict):
            continue
        y = 200 + i * 128
        tonight = item.get('tonight') is True
        sub = ('TONIGHT’S PLAN · ' if tonight else '') + 'ready from ' + str(item.get('eat') or '—') + ' · ' + str(item.get('lead') or 0) + ' min'
        p.append(div(48, y, 976, 104, 'card' + (' ink' if tonight else '')) + t(76, y + 12, str(item.get('title') or ''), 38, 'b', 720) + t(76, y + 58, sub, 26, '', 720) + t(700, y + 38, 'COOK ›', 30, 'b r', 300))
    pages = data.get('pages', 1) if isinstance(data, dict) else 1
    page = data.get('page', 0) if isinstance(data, dict) else 0
    if type(pages) is int and pages > 1:
        p.append(t(800, 36, f'{int(page) + 1}/{pages}', 34, 'b r', 224))
        p.append(button(48, 240, '‹ PREV') + button(312, 448, 'BACK') + button(784, 240, 'NEXT ›'))
    else:
        p.append(button(48, 976, 'BACK'))
    return doc(p)


def home(panel, now=None):
    now = now or datetime.datetime.now().astimezone()
    p = [div(88, 300, 896, 860, 'card', '', 'border-radius:40px;border-width:6px'), t(140, 336, "Who's home", 64, 'b')]
    members = panel.get('members') if isinstance(panel, dict) and isinstance(panel.get('members'), list) else []
    if not members:
        p.append(t(140, 460, 'Presence unavailable', 28))
    members = sorted(members, key=lambda x: 0 if isinstance(x, dict) and x.get('home') is True else 1)
    for i, member in enumerate(members[:3]):
        if not isinstance(member, dict):
            continue
        y = 450 + i * 180
        art = member.get('art')
        if isinstance(art, str) and ART.fullmatch(art):
            p.append(f'<img class="a art" style="left:140px;top:{y}px;width:132px;height:132px;object-fit:cover" src="{art}">')
        else:
            p.append(div(140, y, 132, 132, 'card c', e(str(member.get('name') or '?')[:1]), 'background:#ddd;font-size:44px;font-weight:700;line-height:126px'))
        status = 'Home' if member.get('home') is True else 'Out' if member.get('home') is False else 'Unavailable'
        try:
            when = datetime.datetime.fromisoformat(str(member.get('transition') or '').replace('Z', '+00:00')).astimezone()
            if when.date() == now.date():
                shown = 'today ' + when.strftime('%H:%M')
            elif (now - when).total_seconds() < 6 * 86400:
                shown = when.strftime('%a %H:%M')
            else:
                shown = when.strftime('%a %d %b %H:%M')
            status += ' · ' + ('arrived ' if member.get('home') is True else 'left ') + shown
        except (ValueError, TypeError, OverflowError, OSError):
            pass
        p.append(t(306, y + 10, member.get('name') or 'Unknown', 48, 'b', 640, '#666' if member.get('home') is False else None) + t(306, y + 70, status, 32, '', 640))
    p.append(div(140, 1010, 792, 104, 'btn ink', 'DONE'))
    return doc(p, STRIPES)


def birthdays(data, now=None):
    p = [t(48, 30, '🎂 Birthdays', 56, 'b')]
    items = data.get('items') if isinstance(data, dict) and isinstance(data.get('items'), list) else []
    if not isinstance(data, dict):
        p.append(t(76, 168, 'Birthdays unavailable', 26))
    elif not items:
        p.append(t(76, 168, 'No upcoming birthdays', 26))
    for i, item in enumerate(items[:8]):
        if not isinstance(item, dict):
            continue
        y = 140 + i * 128
        today = item.get('today') is True
        p.append(div(48, y, 976, 104, 'card' + (' ink' if today else '')) + t(76, y + 12, item.get('name') or '', 38, 'b', 700) + t(76, y + 58, item.get('date') or '', 26, '', 700) + t(700, y + 38, item.get('countdown') or '', 30, 'b r', 300))
    p.append(button(48, 976, 'BACK'))
    return doc(p)


def menu(data=None, now=None):
    p = [t(48, 36, 'Menu', 48, 'b')]
    for i, label in enumerate(MENU_ROWS):
        p.append(div(140, 300 + i * 140, 792, 116, 'ink c', e(label), 'border-radius:30px;font-size:30px;font-weight:700;line-height:116px'))
    return doc(p)


def cooking(cook, alerts=None):
    p = [t(48, 52, cook.get('title') or 'Cooking', 46, 'b', 560), t(48, 118, 'Eating at ' + str(cook.get('eat') or '—'), 30, 'b'), pill(360, 122, 232, 'STOP COOKING', 22, 46)]
    target = cook.get('oven_target')
    p.append(div(640, 40, 384, 140, 'card', '', 'border-radius:20px') + t(640, 56, 'OVEN', 22, 'b c', 384) + t(640, 112, (str(target) + '° target') if isinstance(target, (float, int)) and not isinstance(target, bool) else 'Unavailable', 30, 'b c', 384))
    rows = cook.get('next') if isinstance(cook.get('next'), list) else []
    n = min(3, len(rows))
    height = max(150, 150 + max(0, n - 1) * 60)
    p.append(div(40, 206, 992, height, 'card ink', '', 'border-radius:20px') + t(76, 232, 'NEXT', 22, 'b', 200, '#fff'))
    if not n:
        p.append(t(76, 294, 'No upcoming steps', 28, 'b', 800, '#fff'))
    for i, row in enumerate(rows[:3]):
        if not isinstance(row, dict):
            continue
        p.append(t(76, 294 + i * 60, str(row.get('at') or '') + ' ' + str(row.get('text') or ''), 24, 'b', 500, '#fff'))
        for x, w, caption in ((596, 92, 'DONE'), (696, 92, 'DID IT'), (796, 84, 'ON IT'), (888, 60, '−5'), (956, 60, '+5')):
            p.append(div(x, 286 + i * 60, w, 40, 'card c', e(caption), 'border-radius:20px;color:#000;font-size:18px;font-weight:700;line-height:34px'))
    line = max(392, 206 + height + 36)
    p.append(f'<div class="a" style="left:48px;top:{line}px;width:976px;height:3px;background:#000"></div>')
    for i, row in enumerate((cook.get('steps') if isinstance(cook.get('steps'), list) else [])[:9]):
        if not isinstance(row, dict):
            continue
        y = line + 28 + i * 54
        if y + 20 >= 990:
            break
        p.append(t(60, y, row.get('at', ''), 23, 'b', 140) + t(210, y, ('✓ ' if row.get('done') else '') + str(row.get('text') or ''), 23, '', 800))
    p.append('<div class="a" style="left:48px;top:1000px;width:976px;height:3px;background:#000"></div>' + t(48, 1008, 'PREPARATION', 22, 'b'))
    for i, row in enumerate((cook.get('prep') if isinstance(cook.get('prep'), list) else [])[:5]):
        if not isinstance(row, dict):
            continue
        y = 1046 + i * 75
        p.append(div(48, y, 976, 61, 'card', '', 'border-radius:14px') + div(66, y + 14, 32, 32, 'card', '', 'border-radius:6px') + t(118, y + 14, row.get('text', ''), 24, '', 875))
    life = [str(x.get('title') or '') for x in (alerts.get('alerts') or []) if isinstance(x, dict) and x.get('kind') in ('smoke', 'co')] if isinstance(alerts, dict) else []
    status = 'Alert sources not configured' if isinstance(alerts, dict) and alerts.get('configured') is False else 'Alert sources unavailable' if alerts is None or (isinstance(alerts, dict) and not alerts.get('complete')) else ''
    warnings = life[:2] + ([status] if status else [])
    if warnings:   # safety stays above the cooking layer
        p.append(div(0, 0, 1072, 30 * len(warnings) + 6, 'ink') + ''.join(t(0, 3 + i * 30, w, 20, 'b c', 1072, '#fff') for i, w in enumerate(warnings)))
    return doc(p)
