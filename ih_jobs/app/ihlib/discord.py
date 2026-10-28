"""Discord REST helpers (no gateway). Tokens are passed in by the caller and never logged. opener is injectable for tests."""
import json, urllib.request

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
