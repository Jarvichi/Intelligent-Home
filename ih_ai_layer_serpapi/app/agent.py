"""SerpApi agent core: answers signed /ask requests for 'showtimes' and 'web_search'. stdlib only.
The SerpApi key (option serpapi_key) is only used to build the request URL and is never logged, returned or put in /status.
Daily cap is counted per local date in /data/usage.json BEFORE the call is made; cached answers cost nothing."""
import hashlib, json, os, threading, time, urllib.parse, urllib.request
import jobs_link
import showtimes_parse as sp

VERSION = '0.1.1'
CAPABILITIES = ['showtimes', 'web_search']
SERP_URL = 'https://serpapi.com/search.json'
STOP_FILE = 'STOP'


def load_options(path):
    try:
        with open(path) as f:
            o = json.load(f)
    except (OSError, ValueError):
        o = {}

    def num(k, d, lo, hi):
        try:
            return max(lo, min(hi, int(o.get(k, d))))
        except (TypeError, ValueError):
            return d
    return {'serpapi_key': str(o.get('serpapi_key') or '').strip(), 'register_key': str(o.get('register_key') or '').strip(),
            'parent_url': str(o.get('parent_url') or 'http://172.30.32.1:8801').strip().rstrip('/'),
            'callback_url': str(o.get('callback_url') or '').strip().rstrip('/'),
            'daily_cap': num('daily_cap', 5, 0, 250), 'cache_hours': num('cache_hours', 6, 0, 168),
            'default_cinema': str(o.get('default_cinema') or '').strip()[:80]}


class Agent:
    def __init__(self, opts, data='/data', log=None, opener=None, clock=time.time, today=lambda: time.strftime('%Y-%m-%d')):
        self.opts, self.data = opts, data
        self.log = log or (lambda m: None)
        self.opener, self.clock, self.today = opener or urllib.request.urlopen, clock, today
        self.lock = threading.Lock()
        self.started = clock()
        self.served = self.live_calls = self.cache_hits = self.refused = 0
        self.registered_at, self.register_error = None, ''

    def _p(self, n):
        return os.path.join(self.data, n)

    def _read(self, n):
        try:
            with open(self._p(n)) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, n, d):
        try:
            with open(self._p(n) + '.tmp', 'w') as f:
                json.dump(d, f)
            os.replace(self._p(n) + '.tmp', self._p(n))
        except OSError:
            self.log('write %s failed' % n)

    def used_today(self):
        u = self._read('usage.json')
        return int(u.get(self.today(), 0))

    def _count(self):
        u = {self.today(): self.used_today() + 1}      # old days are dropped
        self._write('usage.json', u)

    def stopped(self):
        return os.path.exists(self._p(STOP_FILE))

    # ---- answering ----
    def ask(self, obj):
        """obj: {text, capability, context, request_id}. -> dict {ok, answer, request_id, ...}. Never raises; never logs text."""
        rid, cap, text = obj.get('request_id'), obj.get('capability'), str(obj.get('text') or '').strip()
        ctx = obj.get('context') if isinstance(obj.get('context'), dict) else {}

        def res(ok, answer, **kw):
            self.log('ask id=%s cap=%s ok=%s %s' % (rid, cap, ok, kw.get('why', '')))
            return dict({'ok': ok, 'answer': answer, 'request_id': rid, 'agent': 'ih_ai_layer_serpapi'}, **kw)
        self.served += 1
        if cap not in CAPABILITIES or not text:
            return res(False, 'This agent only does showtimes and web_search.', why='bad_request')
        if self.stopped():
            return res(False, 'SerpApi agent is stopped (kill switch).', why='stopped')
        if not self.opts['serpapi_key']:
            return res(False, 'SerpApi is not configured (no serpapi_key option).', why='not_configured')
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
            self._count()
        try:
            data = self.search(q)
        except Exception as e:
            self.log('serpapi failed: %s' % type(e).__name__)      # type only: the message could contain the URL with the key
            return res(False, 'SerpApi did not answer.', why='upstream_error')
        self.live_calls += 1
        answer = self.build_answer(cap, data, ctx)
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

    def search(self, q):
        """The ONLY network call. Key goes into the URL here and nowhere else."""
        url = SERP_URL + '?' + urllib.parse.urlencode({'engine': 'google', 'q': q, 'gl': 'uk', 'hl': 'en', 'api_key': self.opts['serpapi_key']})
        with self.opener(urllib.request.Request(url), timeout=30) as x:
            return json.loads(x.read(2000000))

    # ---- status / registration ----
    def status(self):
        now = self.clock()
        return {'name': 'ih_ai_layer_serpapi', 'version': VERSION, 'capabilities': CAPABILITIES, 'configured': bool(self.opts['serpapi_key']),
                'kill_switch': self.stopped(), 'uptime_s': int(now - self.started), 'used_today': self.used_today(), 'daily_cap': self.opts['daily_cap'],
                'served': self.served, 'live_calls': self.live_calls, 'cache_hits': self.cache_hits, 'refused_by_cap': self.refused,
                'parent': {'link_enabled': bool(self.opts['register_key']), 'registered_s_ago': None if self.registered_at is None else int(now - self.registered_at),
                           'last_error': self.register_error}}

    def register(self, opener=None, now=None):
        """Signed POST to the parent /register. Never raises, never sends the SerpApi key. -> bool."""
        o = self.opts
        if not o['register_key']:
            self.register_error = 'no register_key'
            return False
        try:
            body, headers = jobs_link.pack(o['register_key'], {'name': 'ih_ai_layer_serpapi', 'version': VERSION, 'capabilities': CAPABILITIES,
                                                               'url': o['callback_url'] or 'http://172.30.32.1:8802', 'healthy': True}, now=now)
            req = urllib.request.Request(o['parent_url'] + '/register', data=body, method='POST', headers=headers)
            with (opener or self.opener)(req, timeout=5) as x:
                good = x.status == 200
            self.register_error = '' if good else 'parent refused'
            if good:
                self.registered_at = self.clock()
            return good
        except Exception as e:
            self.register_error = type(e).__name__
            return False
