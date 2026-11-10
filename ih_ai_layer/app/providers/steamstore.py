"""Steam Store (no key) for 'game_art_search'. Looks the title up with Steam's public store search and returns the official store art
(portrait library image first, then the header image) in the same answer shape as the other art providers:
JSON list of {title, image_url, source_url, source}. Only the game title leaves the house. The daily cap is counted BEFORE the call.
Errors and empty answers are never cached. A title with no close match returns '[]' (so the router can say nothing was found)."""
import difflib, json, re, urllib.parse, urllib.request
from .base import Provider, USER_AGENT
from . import art

SEARCH_URL = 'https://store.steampowered.com/api/storesearch/'
ART_URL = 'https://cdn.cloudflare.steamstatic.com/steam/apps/%d/%s'
STORE_URL = 'https://store.steampowered.com/app/%d/'
MIN_MATCH = 0.82           # name similarity (0..1) needed to accept a Steam search hit
ART_FILES = (('library_600x900.jpg', 600, 900), ('header.jpg', 460, 215))      # (file, width, height): portrait cover first, then the wide header
SEARCH_TIMEOUT_S = 15      # upper limit for one Steam search
MAX_TITLE_CHARS = art.MAX_TITLE_CHARS
MAX_RESPONSE_BYTES = 1000000
DEFAULT_CACHE_HOURS, MAX_CACHE_HOURS = 168, 720
DEFAULT_DAILY_CAP_STEAM = 200
MAX_DAILY_CAP_STEAM = 2000
REGION = 'gb'


def _norm(s):
    s = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', str(s or '').lower())
    s = re.sub(r"[^a-z0-9 ]+", ' ', s.replace('&', ' and '))
    return ' '.join(s.split())


def pick(title, items):
    """Best close match of the wanted title among Steam search items -> (id, name) or None."""
    want = _norm(title)
    best = None
    for it in items or []:
        if not isinstance(it, dict) or not isinstance(it.get('id'), int):
            continue
        r = 1.0 if _norm(it.get('name')) == want else difflib.SequenceMatcher(None, want, _norm(it.get('name'))).ratio()
        if r >= MIN_MATCH and (best is None or r > best[0]):
            best = (r, it['id'], str(it.get('name') or ''))
    return (best[1], best[2]) if best else None


def build_rows(title, items):
    m = pick(title, items)
    if not m:
        return []
    appid, name = m
    src = STORE_URL % appid
    return [art.make_row(name, ART_URL % (appid, f), src, 'Steam', w, h) for f, w, h in ART_FILES]


class SteamStore(Provider):
    NAME, LABEL, VERSION = 'steamstore', 'Steam Store', '0.1.0'
    CAPABILITIES = ['game_art_search']
    HAS_MODELS = False
    KEY_OPTION = ''
    KEY_REQUIRED = False
    DEFAULT_FREE = True
    DEFAULT_DAILY_CAP = DEFAULT_DAILY_CAP_STEAM
    MAX_DAILY_CAP = MAX_DAILY_CAP_STEAM

    @classmethod
    def extra_opts(cls, s, num):
        return {'cache_hours': num('cache_hours', DEFAULT_CACHE_HOURS, 0, MAX_CACHE_HOURS)}

    def ask(self, obj, timeout=None):
        rid, cap, text = obj.get('request_id'), obj.get('capability'), str(obj.get('text') or '').strip()
        ctx = obj.get('context') if isinstance(obj.get('context'), dict) else {}
        title = str(ctx.get('title') or text).strip()[:MAX_TITLE_CHARS]
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
            return res(False, 'Steam Store provider is stopped (kill switch).', why='stopped')
        ck = art.cache_key(self.NAME, _norm(title), ctx)
        cache = self._read('cache.json')
        hit = cache.get(ck)
        if hit and self.clock() - hit['t'] <= self.opts['cache_hours'] * 3600:
            return res(True, hit['answer'], cached=True)
        if self.opts['shadow']:
            return res(True, "[dry] shadow mode: would ask Steam Store for '%s'. Nothing was sent." % cap, why='shadow', mode='dry')
        with self.lock:
            if self.used_today() >= self.opts['daily_cap']:
                self.refused += 1
                return res(False, 'Steam Store daily limit reached (%d per day). Try tomorrow.' % self.opts['daily_cap'], why='cap')
            self._count()
        try:
            data = self.search(title, timeout)
        except Exception as e:
            self.log('steamstore failed: %s' % type(e).__name__)
            return res(False, 'Steam Store did not answer.', why='upstream_error')
        self.live_calls += 1
        rows = art.keep_accepted(build_rows(title, data.get('items') if isinstance(data, dict) else None), ctx)
        if not rows:
            return res(False, 'Steam Store has no matching cover art.', why='empty')      # never cached; the router tries the next provider
        answer = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
        cache = {k: v for k, v in cache.items() if self.clock() - v['t'] <= self.opts['cache_hours'] * 3600}
        cache[ck] = {'t': self.clock(), 'answer': answer}
        self._write('cache.json', cache)
        return res(True, answer, cached=False)

    def search(self, title, timeout=None):
        """The ONLY network call."""
        url = SEARCH_URL + '?' + urllib.parse.urlencode({'term': title, 'cc': REGION, 'l': 'english'})
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with self.opener(req, timeout=min(SEARCH_TIMEOUT_S, timeout) if timeout else SEARCH_TIMEOUT_S) as x:
            return json.loads(x.read(MAX_RESPONSE_BYTES))
