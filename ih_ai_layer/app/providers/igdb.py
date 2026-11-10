"""IGDB (Twitch's game database) for 'game_art_search'. Uses a Twitch developer app: the client id is the setting `client_id`, the client secret is the provider key
(flat option igdb_key). A short-lived app token is fetched from Twitch and kept in the provider's data folder; the secret and token are never logged or returned.
Only the game title leaves the house. Answer: JSON list of {title, image_url, source_url, source, width, height} (portrait cover, same shape as the other art providers).
Errors and empty answers are never cached. The daily cap is counted BEFORE the search call."""
import json, time, urllib.parse, urllib.request
from .base import Provider, USER_AGENT
from . import art
from .steamstore import pick

TOKEN_URL = 'https://id.twitch.tv/oauth2/token'
GAMES_URL = 'https://api.igdb.com/v4/games'
IMAGE_URL = 'https://images.igdb.com/igdb/image/upload/%s/%s.jpg'
COVER_SIZE = 't_cover_big_2x'          # IGDB's large portrait cover size
SEARCH_LIMIT = 5                       # games asked for per search
SEARCH_TIMEOUT_S = 15
TOKEN_TIMEOUT_S = 15
TOKEN_FILE = 'token.json'
TOKEN_SAFETY_S = 3600                  # fetch a new token when less than this is left
MAX_RESPONSE_BYTES = 1000000
DEFAULT_CACHE_HOURS, MAX_CACHE_HOURS = 168, 720
DEFAULT_DAILY_CAP_IGDB, MAX_DAILY_CAP_IGDB = 500, 5000
MAX_CLIENT_ID_CHARS = 80


def build_query(title):
    safe = title.replace('\\', ' ').replace('"', ' ')
    return 'search "%s"; fields name,url,cover.image_id,cover.width,cover.height; limit %d;' % (safe, SEARCH_LIMIT)


def build_rows(title, items):
    """Best matching IGDB game that has a cover -> one row, else []."""
    withcover = [x for x in items or [] if isinstance(x, dict) and isinstance(x.get('cover'), dict) and x['cover'].get('image_id')]
    m = pick(title, withcover)
    if not m:
        return []
    game = next(x for x in withcover if x.get('id') == m[0])
    cover, page = game['cover'], str(game.get('url') or '')
    if not page.startswith('https://'):
        return []
    return [art.make_row(m[1], IMAGE_URL % (COVER_SIZE, cover['image_id']), page, 'IGDB', cover.get('width'), cover.get('height'))]


class Igdb(Provider):
    NAME, LABEL, VERSION = 'igdb', 'IGDB', '0.1.0'
    CAPABILITIES = ['game_art_search']
    HAS_MODELS = False
    KEY_OPTION = 'igdb_key'
    DEFAULT_FREE = True
    DEFAULT_DAILY_CAP = DEFAULT_DAILY_CAP_IGDB
    MAX_DAILY_CAP = MAX_DAILY_CAP_IGDB

    @classmethod
    def extra_opts(cls, s, num):
        return {'cache_hours': num('cache_hours', DEFAULT_CACHE_HOURS, 0, MAX_CACHE_HOURS),
                'client_id': str(s.get('client_id') or '').strip()[:MAX_CLIENT_ID_CHARS]}

    def configured(self):
        return bool(self.opts['key'] and self.opts['client_id'])

    def ask(self, obj, timeout=None):
        rid, cap, text = obj.get('request_id'), obj.get('capability'), str(obj.get('text') or '').strip()
        ctx = obj.get('context') if isinstance(obj.get('context'), dict) else {}
        title = str(ctx.get('title') or text).strip()[:art.MAX_TITLE_CHARS]
        t0 = self.clock()

        def res(ok, answer, **kw):
            self.log(self.log_line(obj, cap, text, ok, answer, kw, t0))
            return dict({'ok': ok, 'answer': answer, 'request_id': rid, 'agent': self.agent_name(), 'free': self.is_free(), 'model': None}, **kw)
        self.served += 1
        if cap not in self.CAPABILITIES or not title:
            return res(False, 'This agent only does game_art_search.', why='bad_request')
        if obj.get('model'):
            return res(False, 'This agent has no selectable models.', why='model_not_available', error='model_not_available')
        if self.stopped():
            return res(False, 'IGDB provider is stopped (kill switch).', why='stopped')
        if not self.configured():
            return res(False, 'IGDB is not configured (needs the Twitch client id and secret).', why='not_configured')
        ck = art.cache_key(self.NAME, title.lower(), ctx)
        cache = self._read('cache.json')
        hit = cache.get(ck)
        if hit and self.clock() - hit['t'] <= self.opts['cache_hours'] * 3600:
            return res(True, hit['answer'], cached=True)
        if self.opts['shadow']:
            return res(True, "[dry] shadow mode: would ask IGDB for '%s'. Nothing was sent." % cap, why='shadow', mode='dry')
        with self.lock:
            if self.used_today() >= self.opts['daily_cap']:
                self.refused += 1
                return res(False, 'IGDB daily limit reached (%d per day). Try tomorrow.' % self.opts['daily_cap'], why='cap')
            self._count()
        try:
            items = self.search(title, timeout)
        except Exception as e:
            self.log('igdb failed: %s' % type(e).__name__)      # type only: never the message
            return res(False, 'IGDB did not answer.', why='upstream_error')
        self.live_calls += 1
        rows = art.prefer_portrait(art.keep_accepted(build_rows(title, items), ctx))
        if not rows:
            return res(False, 'IGDB has no matching cover art.', why='empty')      # never cached; the router tries the next provider
        answer = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
        cache = {k: v for k, v in cache.items() if self.clock() - v['t'] <= self.opts['cache_hours'] * 3600}
        cache[ck] = {'t': self.clock(), 'answer': answer}
        self._write('cache.json', cache)
        return res(True, answer, cached=False)

    def token(self, timeout=None):
        """Twitch app token, reused until nearly expired."""
        saved = self._read(TOKEN_FILE)
        if saved.get('token') and saved.get('client_id') == self.opts['client_id'] and saved.get('expires', 0) - self.clock() > TOKEN_SAFETY_S:
            return saved['token']
        params = urllib.parse.urlencode({'client_id': self.opts['client_id'], 'client_secret': self.opts['key'], 'grant_type': 'client_credentials'})
        req = urllib.request.Request(TOKEN_URL + '?' + params, data=b'', method='POST', headers={'User-Agent': USER_AGENT})
        with self.opener(req, timeout=min(TOKEN_TIMEOUT_S, timeout) if timeout else TOKEN_TIMEOUT_S) as x:
            data = json.loads(x.read(MAX_RESPONSE_BYTES))
        token = str(data['access_token'])
        self._write(TOKEN_FILE, {'token': token, 'client_id': self.opts['client_id'], 'expires': self.clock() + int(data.get('expires_in') or 0)})
        return token

    def search(self, title, timeout=None):
        """The ONLY search call. Client id and token go into headers here and nowhere else."""
        req = urllib.request.Request(GAMES_URL, data=build_query(title).encode(), method='POST',
                                     headers={'Client-ID': self.opts['client_id'], 'Authorization': 'Bearer ' + self.token(timeout),
                                              'Accept': 'application/json', 'User-Agent': USER_AGENT})
        with self.opener(req, timeout=min(SEARCH_TIMEOUT_S, timeout) if timeout else SEARCH_TIMEOUT_S) as x:
            data = json.loads(x.read(MAX_RESPONSE_BYTES))
        return data if isinstance(data, list) else []
