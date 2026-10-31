"""car_tyres: warn one person by Discord DM when a car tyre is low, with a picture. Runs every 10 minutes but acts only inside a daytime window.
A tyre is LOW when its pressure is below `low_fraction` of its own target pressure. Only COLD tyres count: the odometer must not have changed for
`parked_hours`. Cooldown `cooldown_days` between warnings, unless a low tyre dropped `drop_psi` or more since the last warning.
Every house value comes from settings (JSON in plugins[].settings):
  user_id               Discord user id to DM
  token_option          add-on option holding the bot token (default discord_token)
  pressure_entity       entity id of ONE tyre's pressure sensor with the position replaced by {pos}, e.g. sensor.car_tire_pressure_{pos}
  target_entity         same for the target pressure, e.g. sensor.car_tire_pressure_target_{pos}   (both sensors in kPa)
  positions             {"front_left": "front left", ...}: key = {pos} in the entity ids, value = words used in the message
  odometer_entity       sensor that changes while the car is driven
  picture_url           optional URL of a picture (PNG) fetched fresh at send time. Empty (default) = the plugin DRAWS the picture itself (_tyrepic.py, pure Python, no Pillow/browser):
                        top-down car, the four pressures in PSI (amber below 90% of target, red below 80%), 'TARGET n PSI' under it. A failed fetch also falls back to the drawn one.
  tyre_image_url        optional URL of a transparent top-down car PNG (front at top, e.g. the one a dashboard card uses). When set, the drawn picture puts the four pressures
                        and the target line on THIS picture instead of the line-art car. A failed fetch or an undecodable file falls back to the line-art car. Empty (default) = line-art car.
  draw_picture          true (default): draw as above; false: send no picture unless picture_url is set
  recipient_name, car_name, signoff   words used in the message ("Hi <recipient_name>. The <car_name>'s tyres ...", "- <signoff>")
  window_start, window_end   hours of the day when it may send (default 8 and 20)
  low_fraction (0.9), parked_hours (2), cooldown_days (3), drop_psi (2)
  ignore_gates          true = ignore window, parked check and cooldown (use with a manual dry run to see the text now)
State (plugin state 'tyres'): {"last_sent", "next_ok", "last_psi": {pos: psi}, "hold_until"}. Same shape as the old script's json.
Dry: logs the message it would send; no DM, no picture fetch, no state change."""
import datetime as dt

from . import _tyrepic

NAME = 'car_tyres'
SCHEDULE = {'kind': 'every', 'minutes': 10}
PSI = 0.1450377   # kPa -> PSI
DEFAULTS = {'user_id': '', 'token_option': 'discord_token', 'pressure_entity': '', 'target_entity': '', 'odometer_entity': '',
            'positions': {'front_left': 'front left', 'front_right': 'front right', 'rear_left': 'rear left', 'rear_right': 'rear right'},
            'picture_url': '', 'tyre_image_url': '', 'draw_picture': True, 'recipient_name': '', 'car_name': 'car', 'signoff': '', 'window_start': 8, 'window_end': 20,
            'low_fraction': 0.9, 'parked_hours': 2, 'cooldown_days': 3, 'drop_psi': 2, 'ignore_gates': False}


def find_low(s, readings):
    """readings: {pos: (pressure_kpa, target_kpa)} (numbers). -> (low {pos: (psi, target_psi)}, now_psi {pos: psi})"""
    low, now_psi = {}, {}
    for k in s['positions']:
        if k not in readings:
            continue
        kpa, tk = readings[k]
        now_psi[k] = round(kpa * PSI)
        if kpa < s['low_fraction'] * tk:
            low[k] = (round(kpa * PSI), round(tk * PSI))
    return low, now_psi


def draw(s, readings, photo=None):
    """PNG bytes of the car with the pressures (PSI) from readings {pos: (kpa, target_kpa)}. photo = PNG bytes of a car picture to use instead of the line art."""
    psi = {k: kpa * PSI for k, (kpa, tk) in readings.items()}
    tgt = {k: tk * PSI for k, (kpa, tk) in readings.items()}
    return _tyrepic.render_png(psi, next(iter(tgt.values())) if tgt else None, tgt, photo)


def message(s, low):
    lines = ['- %s: %s PSI, needs %s PSI' % (s['positions'][k], n, t) for k, (n, t) in low.items()]
    return ("Hi %s. The %s's tyres are a bit low (checked while it was parked, so cold):\n" % (s['recipient_name'], s['car_name']) + '\n'.join(lines)
            + "\nThe picture shows which ones to top up. Any garage air pump will do.\n- %s" % s['signoff'])


def run(ctx):
    s = ctx.settings
    if not (s['user_id'] and s['pressure_entity'] and s['target_entity'] and s['odometer_entity'] and s['recipient_name'] and s['signoff']):
        ctx.log('not configured (needs user_id, pressure_entity, target_entity, odometer_entity, recipient_name, signoff in settings)')
        return False
    now, gates = ctx.now, not s['ignore_gates']
    if gates and not (int(s['window_start']) <= now.hour < int(s['window_end'])):
        return True
    st = ctx.state.load('tyres')
    if gates and st.get('hold_until') and now < dt.datetime.fromisoformat(st['hold_until']):
        return True
    odo = ctx.ha.get('states/' + s['odometer_entity'])
    parked_h = (now.astimezone(dt.timezone.utc) - dt.datetime.fromisoformat(odo['last_changed'])).total_seconds() / 3600
    if gates and parked_h < float(s['parked_hours']):
        ctx.log('car not parked for %g h yet (%.1f h): skip' % (float(s['parked_hours']), parked_h))
        return True
    readings = {}
    for k in s['positions']:
        try:
            readings[k] = (float(ctx.ha.state(s['pressure_entity'].replace('{pos}', k))), float(ctx.ha.state(s['target_entity'].replace('{pos}', k))))
        except (TypeError, ValueError):
            continue
    low, now_psi = find_low(s, readings)
    if not low:
        ctx.log('all tyres fine')
        return True
    last = st.get('last_psi', {})
    dropped = any(now_psi.get(k, 99) <= last.get(k, 99) - float(s['drop_psi']) for k in low)
    if gates and st.get('next_ok') and now < dt.datetime.fromisoformat(st['next_ok']) and not dropped:
        ctx.log('cooldown until ' + st['next_ok'])
        return True
    text = message(s, low)
    file = None
    if s['picture_url'] and not ctx.dry:
        try:
            file = ('tyres.png', ctx.http.get_bytes(s['picture_url'], timeout=30))
        except Exception as e:
            ctx.log('picture fetch failed (%s): drawing it instead' % type(e).__name__)
    elif s['picture_url']:
        ctx.log('[DRY] would attach the picture from picture_url')
    if file is None and s['draw_picture'] and not (ctx.dry and s['picture_url']):
        photo = None
        if s['tyre_image_url'] and not ctx.dry:
            try:
                photo = ctx.http.get_bytes(s['tyre_image_url'], timeout=20)
            except Exception as e:
                ctx.log('tyre image fetch failed (%s): drawing the line-art car' % type(e).__name__)
        elif s['tyre_image_url']:
            ctx.log('[DRY] would put the pressures on the picture from tyre_image_url')
        try:
            file = ('tyres.png', draw(s, readings, photo))
            if ctx.dry:
                ctx.log('[DRY] would attach a drawn picture (%d bytes)' % len(file[1]))
        except Exception as e:
            ctx.log('could not draw the picture (%s): sending text only' % type(e).__name__)
    if not ctx.discord.dm(s['user_id'], text, token_option=s['token_option'], file=file):
        return True   # dry (logged by the helper)
    st.update(last_sent=now.isoformat(), next_ok=(now + dt.timedelta(days=int(s['cooldown_days']))).isoformat(), last_psi=now_psi)
    st.pop('hold_until', None)
    ctx.state.save('tyres', st)
    ctx.log('sent: %d low tyre(s)' % len(low))
    return True
