"""SerpApi (Google results) for 'showtimes' and 'web_search'. The key is only used to build the request URL and is never logged, returned or put in /status.
Same as the old agent add-on: the search text is NOT scrubbed (it is a search query and the film or cinema name must stay readable); the request context is
only used to read film and cinema. Answers are cached (setting cache_hours); a cached answer costs nothing. Daily cap is counted BEFORE the call."""
import hashlib, json, urllib.parse, urllib.request
from .base import Provider
from . import showtimes_parse as sp

SERP_URL = 'https://serpapi.com/search.json'


class SerpApi(Provider):
    NAME, LABEL, VERSION = 'serpapi', 'SerpApi', '0.1.4'
    CAPABILITIES = ['showtimes', 'web_search']
    HAS_MODELS = False
    KEY_OPTION = 'serpapi_key'
    DEFAULT_DAILY_CAP = 5
    MAX_DAILY_CAP = 250

    @classmethod
    def extra_opts(cls, s, num):
        return {'cache_hours': num('cache_hours', 6, 0, 168), 'default_cinema': str(s.get('default_cinema') or '').strip()[:80]}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.cache_hits = 0

    def ask(self, obj, timeout=None):
        """-> dict {ok, answer, request_id, ...}. Never raises; never logs text."""
        rid, cap, text = obj.get('request_id'), obj.get('capability'), str(obj.get('text') or '').strip()
        ctx = obj.get('context') if isinstance(obj.get('context'), dict) else {}
        t0 = self.clock()

        def res(ok, answer, **kw):
            self.log(self.log_line(obj, cap, text, ok, answer, kw, t0))
            return dict({'ok': ok, 'answer': answer, 'request_id': rid, 'agent': self.agent_name(), 'free': self.is_free(), 'model': None}, **kw)
        self.served += 1
        if cap not in self.CAPABILITIES or not text:
            return res(False, 'This agent only does showtimes and web_search.', why='bad_request')
        if obj.get('model'):
            return res(False, 'This agent has no selectable models.', why='model_not_available', error='model_not_available')
        if self.stopped():
            return res(False, 'SerpApi provider is stopped (kill switch).', why='stopped')
        if not self.opts['key']:
            return res(False, 'SerpApi is not configured (no key for provider serpapi).', why='not_configured')
        q = self.build_query(cap, text, ctx)
        ck = hashlib.sha256(('%s|%s' % (cap, q.lower())).encode()).hexdigest()[:24]
        cache = self._read('cache.json')
        hit = cache.get(ck)
        if hit and self.clock() - hit['t'] <= self.opts['cache_hours'] * 3600:
            self.cache_hits += 1
            return res(True, hit['answer'], cached=True)
        with self.lock:
            if self.used_today() >= self.opts['daily_cap']:
                self.refused += 1
                return res(False, 'SerpApi daily limit reached (%d per day). Try tomorrow.' % self.opts['daily_cap'], why='cap')
            if not self.spend_ok():
                self.refused += 1
                return res(False, 'Paid calls are not allowed (spend_cap %d paid calls per day, 0 = free only).' % self.opts.get('spend_cap', 0), why='spend_cap', error='spend_cap')
            self._count()
        try:
            data = self.search(q, timeout)
        except Exception as e:
            self.log('serpapi failed: %s' % type(e).__name__)      # type only: the message could contain the URL with the key
            return res(False, 'SerpApi did not answer.', why='upstream_error')
        self.live_calls += 1
        answer = self.build_answer(cap, data, ctx)
        if not answer.startswith(('No showtimes found', 'No results found')):      # never cache an empty answer (a parser fix must not be hidden by it)
            cache = {k: v for k, v in cache.items() if self.clock() - v['t'] <= self.opts['cache_hours'] * 3600}
            cache[ck] = {'t': self.clock(), 'answer': answer}
            self._write('cache.json', cache)
        return res(True, answer, cached=False)

    def build_query(self, cap, text, ctx):
        if cap == 'showtimes':
            cinema = str(ctx.get('cinema') or self.opts['default_cinema'] or '').strip()
            film = str(ctx.get('film') or '').strip()
            if cinema or film:
                return ('%s showtimes %s' % (film, cinema)).strip()
        return text[:300]

    def build_answer(self, cap, data, ctx):
        if cap == 'showtimes':
            txt = sp.format_showtimes(sp.parse_showtimes(data, str(ctx.get('film') or '') or None))
            return txt or 'No showtimes found in the SerpApi response (the layout may differ from what the parser expects).'
        return sp.format_organic(sp.parse_organic(data)) or 'No results found.'

    def search(self, q, timeout=None):
        """The ONLY network call. Key goes into the URL here and nowhere else."""
        url = SERP_URL + '?' + urllib.parse.urlencode({'engine': 'google', 'q': q, 'gl': 'uk', 'hl': 'en', 'api_key': self.opts['key']})
        with self.opener(urllib.request.Request(url), timeout=min(30, timeout) if timeout else 30) as x:
            return json.loads(x.read(2000000))

    def status(self):
        s = super().status()
        s['cache_hits'] = self.cache_hits
        return s
