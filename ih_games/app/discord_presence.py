"""Discord presence listener (Andrew 27 Nov 2026): which game a watched person is playing on their PC, from Discord's "Playing X".

One Gateway websocket (dgateway_base: the same client the assistant add-on uses) with the GUILDS and GUILD_PRESENCES intents.
GUILD_PRESENCES is a PRIVILEGED intent: it must be switched on for the bot in the Discord developer portal (Bot page), or Discord
closes the connection with code 4014 and this listener stops for good (the log says so).

Privacy: only the Discord user ids listed in the players' `discord_user_id` option are looked at; presence of anybody else is dropped
on arrival and never stored or logged. Only the game NAME is kept (no status, no other activity, no user id in logs or files).
Writes data/presence_<player key>.json = {"game": name|None, "since": iso|None, "updated": iso}; providers/discord_presence.py turns
that into sessions and the now-playing state. Pure parts (extract_game, PresenceSession) take no network and are unit tested.
"""
import datetime, json, os, tempfile, threading

import dgateway_base as G

INTENT_GUILDS = 1 << 0
INTENT_GUILD_PRESENCES = 1 << 8
PRESENCE_INTENTS = INTENT_GUILDS | INTENT_GUILD_PRESENCES
ACTIVITY_PLAYING = 0
A_PRESENCE = 'presence'        # (A_PRESENCE, user_id, game name or None)
CLOSE_NO_INTENT = 4014         # "disallowed intents": the Presence Intent is not switched on in the developer portal
MAX_NAME = 80


def extract_game(activities):
    """The name of the first 'Playing' activity, or None. Streaming, listening, custom status and the rest are ignored."""
    for a in activities or []:
        if isinstance(a, dict) and a.get('type') == ACTIVITY_PLAYING and isinstance(a.get('name'), str) and a['name'].strip():
            return a['name'].strip()[:MAX_NAME]
    return None


class PresenceSession(G.Session):
    """The Gateway protocol state machine with presence dispatch: only the watched user ids produce actions."""
    def __init__(self, token, watched, rng=G.random.random):
        super().__init__(token, '0', intents=PRESENCE_INTENTS, rng=rng)
        self.watched = {str(u) for u in watched}

    def _dispatch(self, t, d):
        if t in ('READY', 'RESUMED'):
            return super()._dispatch(t, d)
        if t == 'PRESENCE_UPDATE':
            uid = str((d.get('user') or {}).get('id'))
            return [(A_PRESENCE, uid, extract_game(d.get('activities')))] if uid in self.watched else []
        if t == 'GUILD_CREATE':      # the first view of a server lists everybody's presence at once
            out = []
            for pr in d.get('presences') or []:
                uid = str(((pr or {}).get('user') or {}).get('id'))
                if uid in self.watched:
                    out.append((A_PRESENCE, uid, extract_game(pr.get('activities'))))
            return out
        return []


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), text=True)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f)
    os.replace(tmp, path)


class Tracker:
    """user id -> player key; keeps the current game and when it started; writes presence_<key>.json when it changes."""
    def __init__(self, data_dir, players, now=None):
        self.dir, self.now = data_dir, now or (lambda: datetime.datetime.now().astimezone())
        self.by_user = {str(p['discord_user_id']): p['key'] for p in players if p.get('discord_user_id') and p.get('key')}
        self.state = {k: {'game': None, 'since': None} for k in self.by_user.values()}
        self.lock = threading.Lock()

    def watched(self):
        return list(self.by_user)

    def update(self, uid, game):
        key = self.by_user.get(str(uid))
        if not key:
            return False
        with self.lock:
            cur = self.state[key]
            if game == cur['game']:
                return False
            iso = self.now().isoformat(timespec='seconds')
            cur['game'], cur['since'] = game, (iso if game else None)
            _write(os.path.join(self.dir, 'presence_%s.json' % key), {'game': game, 'since': cur['since'], 'updated': iso})
        return True


class PresenceListener(G.GatewayClient):
    def __init__(self, token, tracker, log=print, **kw):
        super().__init__('presence', token, '0', lambda kind: None, log=log, **kw)   # message / reaction wake-ups mean nothing here
        self.tracker = tracker
        self.session = PresenceSession(token, tracker.watched(), rng=kw.get('rng') or G.random.random)

    def on_presence(self, uid, game):
        self.tracker.update(uid, game)

    def run(self):
        super().run()
        if self.state == G.STATE_FAILED:
            self.log('discord presence: stopped for good. Most likely the Presence Intent is off: Discord developer portal, the bot, Bot page, '
                     'Privileged Gateway Intents, Presence Intent ON, Save; then restart this add-on')


def start(data_dir, players, token, log=print):
    """Start the listener thread if there is a token and at least one watched player; returns it (or None)."""
    tracker = Tracker(data_dir, players)
    if not token or not tracker.watched():
        return None
    L = PresenceListener(token, tracker, log=log)
    L.start()
    return L
