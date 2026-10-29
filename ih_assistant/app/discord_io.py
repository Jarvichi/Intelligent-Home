"""Minimal Discord REST client for one bot (private messages only). opener / sleep are injectable for tests.
Honours 429 retry_after. Never logs message text or the token."""
import json, time, urllib.error, urllib.request

API = 'https://discord.com/api/v10'
MAX_RETRY_WAIT = 60.0
MAX_TRIES = 3


class DiscordError(Exception):
    pass


class Discord:
    def __init__(self, token, opener=None, sleep=time.sleep):
        self._h = {'Authorization': 'Bot ' + token, 'User-Agent': 'IntelligentHomeAssistant (local, 1.0)', 'Content-Type': 'application/json'}
        self.opener, self.sleep = opener or urllib.request.urlopen, sleep
        self.rate_waits = 0

    def call(self, method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        for attempt in range(MAX_TRIES):
            req = urllib.request.Request(API + path, method=method, headers=self._h, data=body)
            try:
                with self.opener(req, timeout=15) as r:
                    raw = r.read()
                return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < MAX_TRIES - 1:
                    try:
                        wait = float(json.loads(e.read() or b'{}').get('retry_after', 1))
                    except (ValueError, TypeError, AttributeError):
                        wait = 1.0
                    self.rate_waits += 1
                    self.sleep(min(max(wait, 0.0), MAX_RETRY_WAIT))
                    continue
                raise DiscordError('http %d' % e.code)
            except (urllib.error.URLError, OSError) as e:
                raise DiscordError(type(e).__name__)
        raise DiscordError('rate limited')

    def open_dm(self, user_id):
        return str(self.call('POST', '/users/@me/channels', {'recipient_id': user_id})['id'])

    def messages_after(self, channel, after=None, limit=50):
        q = '?limit=%d' % limit + ('&after=%s' % after if after else '')
        return self.call('GET', '/channels/%s/messages%s' % (channel, q)) or []

    def send(self, channel, text, reply_to=None):
        d = {'content': text[:1900]}
        if reply_to:
            d['message_reference'] = {'message_id': reply_to, 'fail_if_not_exists': False}
        r = self.call('POST', '/channels/%s/messages' % channel, d)
        return bool(r and r.get('id'))

    def typing(self, channel):
        self.call('POST', '/channels/%s/typing' % channel)
