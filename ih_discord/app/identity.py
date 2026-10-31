"""Bot identity admin (ported from earlier host scripts): avatar, nickname, say and GIF search for the main bot
and for extra persona bots, behind one bearer admin_token. One set of persona identities: persona "house" = discord_token, others come from the
`personas` option. DRY by default: nothing is sent to Discord unless identity_live is on and the STOP_LIVE file is absent.
Tokens and keys are never returned or logged. stdlib only."""
import base64, hmac, json, re, time, urllib.error, urllib.parse, urllib.request

API = 'https://discord.com/api/v10'
UA = 'DiscordBot (ih-discord-identity, 1)'
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{0,30}$')
MAX_IMAGE = 6 * 1024 * 1024
AVATAR_WINDOW_S, AVATAR_MAX = 600, 2          # Discord allows about 2 avatar changes per 10 minutes per bot
MAGIC = [(b'\x89PNG\r\n\x1a\n', 'image/png'), (b'\xff\xd8\xff', 'image/jpeg'), (b'GIF8', 'image/gif')]


def parse_personas(raw):
    """raw = "name=token,name2=token2" (one secret string option) or a list of {name, token}."""
    out = {}
    items = [x.partition('=')[::2] for x in raw.split(',')] if isinstance(raw, str) else [((p or {}).get('name'), (p or {}).get('token')) for p in raw or []]
    for n, t in items:
        n, t = str(n or '').strip().lower(), str(t or '').strip()
        if NAME_RE.match(n) and t and n != 'house':
            out[n] = t
    return out


class Identity:
    def __init__(self, opts, live_check, channel_ids=None, opener=None, clock=time.time):
        """live_check() -> bool (identity_live on and no kill switch). channel_ids: {key: id} for channel keys."""
        self.admin = str(opts.get('admin_token') or '').strip()
        self.giphy = str(opts.get('giphy_api_key') or '').strip()
        self.tokens = dict(parse_personas(opts.get('personas')))
        if opts.get('token'): self.tokens['house'] = opts['token']
        self.live_check, self.channels = live_check, channel_ids or {}
        self.opener, self.clock = opener or urllib.request.urlopen, clock
        self.avatar_log = {}

    def authed(self, header):
        if not self.admin: return False
        h = str(header or '')
        return h.startswith('Bearer ') and hmac.compare_digest(h[7:].strip().encode(), self.admin.encode())

    def personas(self): return sorted(self.tokens)

    def _call(self, persona, method, path, body=None):
        r = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                   headers={'Authorization': 'Bot ' + self.tokens[persona], 'Content-Type': 'application/json', 'User-Agent': UA})
        try:
            with self.opener(r, timeout=20) as x:
                raw = x.read()
                return x.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, None

    def run(self, op, body):
        """-> (http status, dict). Never includes a token."""
        body = body if isinstance(body, dict) else {}
        if op == 'gif': return self.gif(body)
        persona = str(body.get('persona') or 'house').strip().lower()
        if persona not in self.tokens:
            return 404, {'ok': False, 'why': 'unknown persona', 'personas': self.personas()}
        fn = {'avatar': self.avatar, 'nick': self.nick, 'say': self.say}.get(op)
        if not fn: return 404, {'ok': False, 'why': 'unknown op'}
        return fn(persona, body)

    def avatar(self, persona, b):
        try: img = base64.b64decode(re.sub(r'\s', '', str(b.get('image_b64') or '')), validate=True)
        except Exception: return 422, {'ok': False, 'why': 'image_b64 is not valid base64'}
        mime = next((m for sig, m in MAGIC if img.startswith(sig)), None)
        if not mime or len(img) > MAX_IMAGE: return 422, {'ok': False, 'why': 'png, jpeg or gif under 6 MB needed'}
        now = self.clock()
        recent = [t for t in self.avatar_log.get(persona, []) if now - t < AVATAR_WINDOW_S]
        if len(recent) >= AVATAR_MAX: return 429, {'ok': False, 'why': 'avatar rate limit (2 per 10 min per bot)'}
        if not self.live_check(): return 200, {'ok': True, 'dry': True, 'would': 'set %s avatar (%s, %d bytes)' % (persona, mime, len(img))}
        st, js = self._call(persona, 'PATCH', '/users/@me', {'avatar': 'data:%s;base64,%s' % (mime, base64.b64encode(img).decode())})
        if st == 200: self.avatar_log[persona] = recent + [now]
        return (200 if st == 200 else 502), {'ok': st == 200, 'dry': False, 'status': st}

    def nick(self, persona, b):
        nick = str(b.get('nick') if b.get('nick') is not None else '').strip()[:32]    # '' resets to the username
        if not self.live_check(): return 200, {'ok': True, 'dry': True, 'would': 'set %s nickname to %r in every server' % (persona, nick)}
        st, guilds = self._call(persona, 'GET', '/users/@me/guilds')
        if st != 200: return 502, {'ok': False, 'status': st}
        done = {}
        for g in guilds or []:
            s, _ = self._call(persona, 'PATCH', '/guilds/%s/members/@me' % g['id'], {'nick': nick or None})
            done[str(g.get('name', g['id']))[:40]] = s
        return 200, {'ok': all(v == 200 for v in done.values()), 'dry': False, 'servers': done}

    def say(self, persona, b):
        ch = str(b.get('channel') or '').strip()
        ch = self.channels.get(ch, ch)
        text = str(b.get('text') or '')[:1900]
        if not ch.isdigit() or not text: return 422, {'ok': False, 'why': 'channel (number or configured key) and text needed'}
        if not self.live_check(): return 200, {'ok': True, 'dry': True, 'would': 'post %d chars as %s' % (len(text), persona)}
        st, js = self._call(persona, 'POST', '/channels/%s/messages' % ch, {'content': text})
        return (200 if st == 200 else 502), {'ok': st == 200, 'dry': False, 'status': st, 'id': (js or {}).get('id')}

    def gif(self, b):
        """Search GIPHY (PG only, family server). Returns one page link; with persona + channel it also posts the link (dry-aware)."""
        q = str(b.get('query') or '').strip()[:80]
        if not q: return 422, {'ok': False, 'why': 'query needed'}
        if not self.giphy: return 503, {'ok': False, 'why': 'giphy_api_key not set'}
        url = 'https://api.giphy.com/v1/gifs/search?' + urllib.parse.urlencode({'api_key': self.giphy, 'q': q, 'limit': 10, 'rating': 'pg', 'lang': 'en'})
        try:
            with self.opener(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=10) as x: data = json.load(x)['data']
        except Exception as e:
            return 502, {'ok': False, 'why': 'giphy error: ' + type(e).__name__}
        if not data: return 404, {'ok': False, 'why': 'no gif found'}
        link = data[int(self.clock()) % min(5, len(data))]['url']
        out = {'ok': True, 'url': link}
        if b.get('channel'):
            st, r = self.say(str(b.get('persona') or 'house').strip().lower(), {'channel': b['channel'], 'text': link}) if str(b.get('persona') or 'house').strip().lower() in self.tokens else (404, {'ok': False})
            out['post'] = r
        return 200, out
