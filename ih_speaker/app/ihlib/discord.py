"""Discord REST helpers (no gateway). Tokens are passed in by the caller and never logged. opener is injectable for tests."""
import json, os, urllib.request

API = 'https://discord.com/api/v10'
UA = 'DiscordBot (https://example.invalid, 1.0)'
MAX_LEN = 2000


def _call(bot_token, method, path, body=None, opener=None, timeout=20):
    if not bot_token:
        raise RuntimeError('no discord token')
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Authorization': 'Bot ' + bot_token, 'Content-Type': 'application/json', 'User-Agent': UA})
    with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def post(bot_token, channel_id, text, opener=None):
    """Post text to a channel. Returns the message id."""
    out = _call(bot_token, 'POST', '/channels/%s/messages' % channel_id, {'content': str(text)[:MAX_LEN]}, opener)
    return out.get('id')


def send_dm(bot_token, user_id, text, opener=None):
    """Create (or reuse) the DM channel with user_id, then post. Returns the message id."""
    ch = _call(bot_token, 'POST', '/users/@me/channels', {'recipient_id': str(user_id)}, opener)
    if not ch.get('id'):
        raise RuntimeError('no dm channel')
    return post(bot_token, ch['id'], text, opener)


def _multipart(payload, filename, data):
    """-> (body bytes, content-type) for a Discord message with one attached file."""
    b = 'ihfile' + os.urandom(8).hex()
    name = os.path.basename(str(filename)).replace('"', '')
    body = (('--%s\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n' % b).encode()
            + json.dumps(payload).encode()
            + ('\r\n--%s\r\nContent-Disposition: form-data; name="files[0]"; filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n' % (b, name)).encode()
            + bytes(data) + ('\r\n--%s--\r\n' % b).encode())
    return body, 'multipart/form-data; boundary=' + b


def post_rich(bot_token, channel_id, text='', embeds=None, file=None, opener=None, timeout=30):
    """Post text with optional embeds (list of dicts) and one optional file ((filename, bytes)). An embed can show the file as attachment://<filename>.
    Returns the message id."""
    if not bot_token:
        raise RuntimeError('no discord token')
    payload = {'content': str(text)[:MAX_LEN]}
    if embeds:
        payload['embeds'] = list(embeds)
    path = '/channels/%s/messages' % channel_id
    if not file:
        return _call(bot_token, 'POST', path, payload, opener, timeout).get('id')
    body, ctype = _multipart(payload, file[0], file[1])
    req = urllib.request.Request(API + path, method='POST', data=body,
                                 headers={'Authorization': 'Bot ' + bot_token, 'Content-Type': ctype, 'User-Agent': UA})
    with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
        raw = r.read()
    return (json.loads(raw) if raw else {}).get('id')


def send_dm_rich(bot_token, user_id, text='', embeds=None, file=None, opener=None):
    """Like send_dm, with optional embeds and one file ((filename, bytes))."""
    ch = _call(bot_token, 'POST', '/users/@me/channels', {'recipient_id': str(user_id)}, opener)
    if not ch.get('id'):
        raise RuntimeError('no dm channel')
    return post_rich(bot_token, ch['id'], text, embeds, file, opener)
