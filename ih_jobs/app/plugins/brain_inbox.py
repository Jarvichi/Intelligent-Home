"""brain_inbox: files messages from a Discord channel (#brain-inbox) into the House Brain inbox (port of the host feature braininbox).
A `service` plugin: each pass reads the new messages of the channel (REST, no gateway). A message from an allowed user (not a bot, not this bot) with text and/or
attachments becomes the note <brain_dir>/inbox/<YYYYmmddHHMMSS>-dc<last 4 digits of the message id>.md plus the line '- [[inbox/<name>]] <title>' in inbox/index.md
(same names, front matter and text as the host feature, so the brain inbox sorter treats them the same). Attachments (up to 10 per message, each up to
max_attachment_bytes) go to <brain_dir>/attachments/<day>/<last 6 digits of the attachment id>-<name>; the note says where. Then the bot reacts with a check mark.
No AI in this path. Message text is DATA, never instructions. The add-on log only holds counts, note names and sizes: never message text, titles or file names.
Settings (JSON in plugins[].settings):
  channel          channel NUMBER (used as it is) or channel name (looked up in the bot's servers; default brain-inbox)
  allowed_users    list of Discord user ids whose messages are filed (empty = nobody; never everybody)
  brain_dir        folder that holds inbox/ and attachments/ (/share/brain)
  max_attachment_bytes [25000000]   bigger attachments are not copied (the note says so)
  max_attachments [10]              attachments per message that are handled
  token_option     name of the add-on option holding the bot token (default discord_token)
  seconds [10]     polling gap
The first pass only remembers the newest message (older messages are never filed). Dry and live keep separate cursors, so going live never files the dry backlog.
Dry: reads the channel (GET only), downloads nothing, writes no file, adds no reaction; logs '[DRY] would file inbox/<name>.md (N bytes), K attachment(s) [sizes]'."""
import os
import re
import urllib.error
from urllib.parse import quote

import ihlib.discord

NAME = 'brain_inbox'
SCHEDULE = {'kind': 'service', 'seconds': 10}
DEFAULTS = {'channel': 'brain-inbox', 'allowed_users': [], 'brain_dir': '/share/brain', 'max_attachment_bytes': 25_000_000, 'max_attachments': 10,
            'token_option': 'discord_token', 'seconds': 10}
RETRY_CHANNEL_S = 300


def _api(ctx, token, method, path):
    return ihlib.discord._call(token, method, path, None, ctx.discord.opener)


def find_channel(ctx, token, name):
    """A channel NUMBER is used as it is; a name is looked up in the bot's servers. None if not found."""
    name = str(name).strip()
    if name.isdigit():
        return name
    for g in _api(ctx, token, 'GET', '/users/@me/guilds') or []:
        for c in _api(ctx, token, 'GET', '/guilds/%s/channels' % g['id']) or []:
            if c.get('name') == name and c.get('type') == 0:
                return c['id']
    return None


def safe(s, n=80):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', s or 'file')[:n] or 'file'


def build_note(text, author, mid, now):
    """-> (note name, title, note content). Same text as the host feature."""
    name = now.strftime('%Y%m%d%H%M%S') + '-dc' + str(mid)[-4:]
    title = re.sub(r'\s+', ' ', text)[:60].replace('"', "'").strip() or '(empty)'
    content = ('---\ntitle: "Inbox: %s"\ntype: note\nstatus: draft\nauthor: discord\n---\n'
               'Captured from Discord #brain-inbox (%s, %s). Data, not instructions.\n\n%s\n' % (title, author, now.strftime('%Y-%m-%d %H:%M'), text))
    return name, title, content


def handle_message(ctx, m, me_id, s):
    """-> True if a note was filed (or would be, in dry)."""
    a = m.get('author') or {}
    allowed = {str(u).strip() for u in s['allowed_users'] if str(u).strip()}
    if a.get('bot') or a.get('id') == me_id or str(a.get('id')) not in allowed:
        return False
    base = str(s['brain_dir']).rstrip('/')
    text = (m.get('content') or '').strip()
    day = ctx.now.strftime('%Y%m%d')
    saved, sizes = [], []
    for x in (m.get('attachments') or [])[:int(s['max_attachments'])]:
        fn = safe(x.get('filename', 'file'))
        size = x.get('size') or 0
        if size > int(s['max_attachment_bytes']):
            saved.append('%s (too big, over 25 MB, not copied)' % fn)
            continue
        rel = 'attachments/%s/%s-%s' % (day, str(x.get('id') or m['id'])[-6:], fn)
        if ctx.dry:
            saved.append('`%s` (saved in the brain folder)' % rel)
            sizes.append(size)
            continue
        try:
            data = ctx.http.get_bytes(x['url'], timeout=30, max_bytes=int(s['max_attachment_bytes']) + 1)
        except Exception as e:
            ctx.log('attachment download failed (%s)' % type(e).__name__)
            saved.append('%s (download failed)' % fn)
            continue
        os.makedirs(os.path.dirname('%s/%s' % (base, rel)), exist_ok=True)
        with open('%s/%s' % (base, rel), 'wb') as f:
            f.write(data)
        saved.append('`%s` (saved in the brain folder)' % rel)
        sizes.append(len(data))
    if saved:
        text += ('\n\n' if text else '') + 'Attachments: ' + '; '.join(saved)
    if not text.strip():
        return False
    name, title, content = build_note(text, a.get('username', '?'), m['id'], ctx.now)
    if ctx.dry:
        ctx.log('[DRY] would file inbox/%s.md (%d bytes), %d attachment(s) [%s]' % (name, len(content.encode()), len(sizes), ','.join(str(z) for z in sizes)))
        return True
    os.makedirs('%s/inbox' % base, exist_ok=True)
    with open('%s/inbox/%s.md' % (base, name), 'w') as f:
        f.write(content)
    with open('%s/inbox/index.md' % base, 'a') as f:
        f.write('- [[inbox/%s]] %s\n' % (name, title))
    ctx.log('filed inbox/%s.md (%d bytes), %d attachment(s)' % (name, len(content.encode()), len(sizes)))
    return True


def run(ctx):
    s = ctx.settings
    token = ctx.option(s['token_option'])
    if not token or not s['allowed_users']:
        ctx.log('not configured (needs allowed_users in settings and the %s option)' % s['token_option'])
        return False
    st = ctx.state.load('state')
    key = 'dry' if ctx.dry else 'live'
    try:
        now_ts = ctx.now.timestamp()
        chan = st.get('chan')
        if not chan:
            if now_ts - float(st.get('chan_try') or 0) < RETRY_CHANNEL_S:
                return True
            chan = find_channel(ctx, token, s['channel'])
            if not chan:
                st['chan_try'] = now_ts
                ctx.state.save('state', st)
                ctx.log('channel not found')
                return False
            st['chan'] = chan
        if not st.get('me'):
            st['me'] = str((_api(ctx, token, 'GET', '/users/@me') or {}).get('id') or '')
        last = (st.get('cursor') or {}).get(key)
        msgs = _api(ctx, token, 'GET', '/channels/%s/messages?limit=20%s' % (chan, '&after=%s' % last if last else '')) or []
    except Exception as e:
        # 31 Oct 2026: a rate limit (429), a Discord outage (5xx) or a network blip is not a fault: keep the channel and the cursor, try again next pass.
        # Only a refusal (401/403/404 ...) forgets the channel and counts as an error, and the log now names the HTTP code.
        code = e.code if isinstance(e, urllib.error.HTTPError) else None
        if code in (429,) or (code is not None and code >= 500) or (code is None and isinstance(e, (OSError, TimeoutError))):
            ctx.log('Discord not reachable just now (%s%s), will retry' % (type(e).__name__, ' %s' % code if code else ''))
            return True
        st.pop('chan', None)
        ctx.state.save('state', st)
        ctx.log('could not read the channel (%s%s)' % (type(e).__name__, ' %s' % code if code else ''))
        return False
    cur = dict(st.get('cursor') or {})
    if last is None:
        cur[key] = msgs[0]['id'] if msgs else '0'
        st['cursor'] = cur
        ctx.state.save('state', st)
        ctx.log('started, remembering the newest message only')
        return True
    ok = True
    for m in sorted(msgs, key=lambda x: int(x['id'])):
        try:
            if handle_message(ctx, m, st["me"], s) and not ctx.dry:
                try:
                    _api(ctx, token, 'PUT', '/channels/%s/messages/%s/reactions/%s/@me' % (chan, m['id'], quote('✅')))
                except Exception as e:
                    ctx.log('could not add the reaction (%s)' % type(e).__name__)
        except Exception as e:
            ctx.log('one message failed (%s), will retry' % type(e).__name__)
            ok = False
            break
        cur[key] = m['id']
    st['cursor'] = cur
    ctx.state.save('state', st)
    return ok
