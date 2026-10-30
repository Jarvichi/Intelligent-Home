"""eufy_person: a person at an outdoor Eufy camera -> the event picture in a Discord channel (port of the old host script eufy_person.py).
An `event` plugin: no timer. Home Assistant POSTs one JSON object to /plugin/eufy_person/event (Authorization: Bearer <files_token>) when a person sensor turns on
(after the automation has waited for the new event picture). Body: {"cam": "front_door", "trig": <epoch of the detection>, "ts": <image entity state>, "pic": <entity_picture path>}.
Only cam is needed: missing ts / pic / trig are read from Home Assistant (the camera's event image entity and person sensor).
Same rules as the host script: only cameras listed in `cams`; one post per camera per `rate_s` (300) seconds; a picture older than the detection by more than `fresh_s` (60),
the same image timestamp as the last send, a picture that cannot be fetched or is under 1000 bytes, or the same picture (md5) as one of the last sends is NOT posted:
the message then goes out as text only with the note "(No fresh picture available from the camera: <why>. Not sending an old one.)".
Message (identical to the host): '**Person at the <name>** at HH:MM on Mon 30 Oct', plus the picture <cam>.jpg. No quiet hours (the host has none).
Settings (JSON in plugins[].settings):
  channel      channel NUMBER (used as it is) or channel name (default camera)
  token_option name of the add-on option holding the bot token (default discord_token = the Discord bot)
  cams         {key: {name, entity, sensor}} (defaults: front_door, side_door, back_garden, back_garden_2)
  rate_s [300], fresh_s [60], keep_days [7], snap_dir [/media/eufy_snapshots]
Dry: fetches the picture, logs '[DRY] would post camera=<key> image=<bytes> bytes' (no text, no picture content), posts and saves nothing.
Rate limit and duplicate checks still run in dry (their memory is kept in /data/eufy_person), so the dry lines match what the host posts.
Live: posts the same message as the host, saves the picture in snap_dir as <key>_<YYYYmmdd_HHMMSS>.jpg and deletes pictures older than keep_days."""
import glob
import hashlib
import json
import os
from datetime import datetime

import ihlib.discord
from plugins import brain_inbox

NAME = 'eufy_person'
SCHEDULE = {'kind': 'event'}
DEFAULTS = {'channel': 'camera', 'token_option': 'discord_token', 'rate_s': 300, 'fresh_s': 60, 'keep_days': 7, 'snap_dir': '/media/eufy_snapshots',
            'cams': {'front_door': {'name': 'Front door', 'entity': 'image.front_door_event_image', 'sensor': 'binary_sensor.front_door_person_detected'},
                     'side_door': {'name': 'Side door', 'entity': 'image.side_door_event_image', 'sensor': 'binary_sensor.side_door_person_detected'},
                     'back_garden': {'name': 'Back garden', 'entity': 'image.back_garden_event_image', 'sensor': 'binary_sensor.back_garden_person_detected'},
                     'back_garden_2': {'name': 'Back garden (2)', 'entity': 'image.back_garden_event_image_2', 'sensor': 'binary_sensor.back_garden_person_detected_2'}}}
BAD = ('', 'None', 'unknown', 'unavailable')
SEEN_KEEP = 20


def run(ctx):
    """Nothing to do on a timer; events arrive at POST /plugin/eufy_person/event."""
    return True


def _epoch(text):
    return datetime.fromisoformat(str(text).replace('Z', '+00:00')).timestamp()


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def event(ctx, body):
    s, cam = ctx.settings, str(body.get('cam') or '')
    cams = s['cams']
    if cam not in cams:
        ctx.log('ignored unknown or indoor camera')
        return False
    c, now = cams[cam], ctx.now.timestamp()
    st = ctx.state.load('state', {}) or {}
    if now - _num(st.get(cam), 0) < _num(s['rate_s'], 300):
        ctx.log('rate-limited camera=%s' % cam)
        return True
    ts, pic, trig = [str(body.get(k) or '') for k in ('ts', 'pic')] + [_num(body.get('trig'), None)]
    try:
        if ts in BAD or not pic or pic in BAD:
            stt = ctx.ha.get('states/' + c['entity']) or {}
            ts, pic = str(stt.get('state') or ''), str((stt.get('attributes') or {}).get('entity_picture') or '')
        if trig is None:
            trig = now
            try:
                lc = _epoch((ctx.ha.get('states/' + c['sensor']) or {})['last_changed'])
                if now - lc < 600:
                    trig = lc
            except Exception:
                pass
    except Exception as e:
        ctx.log('cannot read %s (%s)' % (c['entity'], type(e).__name__))
        return False
    if ts in BAD:
        ctx.log('no event image yet camera=%s: ignoring' % cam)
        return True
    data, why = None, ''
    try:
        tse = _epoch(ts)
    except ValueError:
        ctx.log('bad image timestamp camera=%s' % cam)
        return False
    if tse < trig - _num(s['fresh_s'], 60):
        why = 'latest event image is from %s, older than this detection' % ts
    elif ts == st.get(cam + '_ts'):
        why = 'event image unchanged since the last send'
    elif not str(pic).startswith('/api/image_proxy/'):
        why = 'could not fetch the image (no picture path)'
    else:
        try:
            data = ctx.http.get_bytes(ctx.ha.rest_base.rsplit('/api/', 1)[0] + pic, {'Authorization': 'Bearer ' + ctx.ha.token})
            if len(data) < 1000:
                raise ValueError('tiny image')
            h = hashlib.md5(data).hexdigest()
            if h in (st.get(cam + '_seen') or []):
                data, why = None, 'same picture as an earlier send'
        except Exception as e:
            data, why = None, 'could not fetch the image (%s)' % type(e).__name__
    size = len(data) if data else 0
    st[cam] = now
    if data:
        st[cam + '_ts'] = ts
        st[cam + '_seen'] = ((st.get(cam + '_seen') or []) + [h])[-SEEN_KEEP:]
    if ctx.dry:
        ctx.log('[DRY] would post camera=%s image=%d bytes' % (cam, size))
        ctx.state.save('state', st)
        return True
    text = '**Person at the %s** at %s' % (c['name'], ctx.now.strftime('%H:%M on %a %d %b'))
    if not data:
        text += '\n(No fresh picture available from the camera: %s. Not sending an old one.)' % why
    token = ctx.option(s['token_option'])
    chan = brain_inbox.find_channel(ctx, token, s['channel'])
    if not chan:
        ctx.log('channel not found')
        return False
    ihlib.discord.post_rich(token, chan, text, None, (cam + '.jpg', data) if data else None, ctx.discord.opener)
    ctx.state.save('state', st)
    ctx.log('posted camera=%s image=%d bytes' % (cam, size))
    if data:
        _save(ctx, s, cam, data, ctx.now)
    return True


def _save(ctx, s, cam, data, now):
    """Keep the picture like the host does (<snap_dir>/<cam>_<stamp>.jpg) and delete old ones. Failure never stops the post."""
    d = str(s['snap_dir'])
    try:
        os.makedirs(d, exist_ok=True)
        with open('%s/%s_%s.jpg' % (d, cam, now.strftime('%Y%m%d_%H%M%S')), 'wb') as f:
            f.write(data)
        limit = now.timestamp() - _num(s['keep_days'], 7) * 86400
        for f in glob.glob(d + '/*.jpg'):
            if os.path.getmtime(f) < limit and not os.path.basename(f).startswith('test_'):
                os.remove(f)
    except OSError as e:
        ctx.log('could not save the picture (%s)' % type(e).__name__)
