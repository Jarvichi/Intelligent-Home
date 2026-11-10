"""Serper.dev (Google results) for 'web_search'. The key is only put in the X-API-KEY header and is never logged, returned or put in /status.
The search text is NOT scrubbed (it is a search query) and is never logged. Answers are cached (setting cache_hours, default 6); a cached answer costs
nothing. The daily cap is counted BEFORE the call. Errors and empty answers are never cached.
Answer format: plain numbered text by default; with context {"format": "json"} a JSON list of {title, link, snippet, date} (used by the router's
current_facts flow). At most 8 results."""
import hashlib, json, urllib.request
from .base import Provider, USER_AGENT

SERPER_URL = 'https://google.serper.dev/search'
SERPER_IMAGES_URL = 'https://google.serper.dev/images'
ART_SUFFIX = ' official video game box art cover'
MAX_RESULTS = 8


def parse_results(data, limit=MAX_RESULTS):
    """Serper response -> list of {title, link, snippet, date}. Answer box and knowledge graph first, then organic results, then top stories."""
    out = []
    if not isinstance(data, dict):
        return out

    def add(title, link, snippet, date=''):
        if len(out) < limit and isinstance(link, str) and link.startswith(('http://', 'https://')):
            out.append({'title': str(title or '')[:160], 'link': link[:500], 'snippet': str(snippet or '')[:300], 'date': str(date or '')[:40]})
    ab = data.get('answerBox') if isinstance(data.get('answerBox'), dict) else {}
    if ab:
        add(ab.get('title') or 'Answer', ab.get('link'), ab.get('answer') or ab.get('snippet'), ab.get('date'))
    kg = data.get('knowledgeGraph') if isinstance(data.get('knowledgeGraph'), dict) else {}
    if kg:
        add(kg.get('title'), kg.get('website') or kg.get('descriptionLink'), kg.get('description'))
    for key in ('organic', 'topStories', 'news'):
        for r in data.get(key) or []:
            if isinstance(r, dict):
                add(r.get('title'), r.get('link'), r.get('snippet'), r.get('date'))
    return out


def parse_images(data, limit=3):
    """Serper /images response -> [{title, image_url, source_url, source}] (same shape as the SerpApi game_art_search answer)."""
    out = []
    if not isinstance(data, dict):
        return out
    for r in data.get('images') or []:
        if not isinstance(r, dict):
            continue
        image, source = str(r.get('imageUrl') or ''), str(r.get('link') or '')
        if image.startswith(('http://', 'https://')) and source.startswith(('http://', 'https://')):
            out.append({'title': str(r.get('title') or '')[:180], 'image_url': image[:1200], 'source_url': source[:1200], 'source': str(r.get('source') or '')[:100]})
        if len(out) >= max(1, min(5, int(limit))):
            break
    return out


def format_text(rows):
    return '\n'.join('%d. %s%s - %s %s' % (i + 1, r['title'], ' (%s)' % r['date'] if r['date'] else '', r['snippet'], r['link']) for i, r in enumerate(rows)).replace('  ', ' ')


class Serper(Provider):
    NAME, LABEL, VERSION = 'serper', 'Serper', '0.2.0'
    CAPABILITIES = ['web_search', 'game_art_search']
    HAS_MODELS = False
    KEY_OPTION = 'serper_key'
    DEFAULT_FREE = True            # 2,500 free credits
    DEFAULT_DAILY_CAP = 60
    MAX_DAILY_CAP = 500

    @classmethod
    def extra_opts(cls, s, num):
        return {'cache_hours': num('cache_hours', 6, 0, 168)}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.cache_hits = 0

    def ask(self, obj, timeout=None):
        """-> dict {ok, answer, request_id, ...}. Never raises; never logs the query."""
        rid, cap, text = obj.get('request_id'), obj.get('capability'), str(obj.get('text') or '').strip()
        ctx = obj.get('context') if isinstance(obj.get('context'), dict) else {}
        as_json = ctx.get('format') == 'json'
        t0 = self.clock()

        def res(ok, answer, **kw):
            self.log(self.log_line(obj, cap, text, ok, answer, kw, t0))
            return dict({'ok': ok, 'answer': answer, 'request_id': rid, 'agent': self.agent_name(), 'free': self.is_free(), 'model': None}, **kw)
        self.served += 1
        if cap not in self.CAPABILITIES or not text:
            return res(False, 'This agent only does web_search and game_art_search.', why='bad_request')
        if obj.get('model'):
            return res(False, 'This agent has no selectable models.', why='model_not_available', error='model_not_available')
        if self.stopped():
            return res(False, 'Serper provider is stopped (kill switch).', why='stopped')
        if not self.opts['key']:
            return res(False, 'Serper is not configured (no key for provider serper).', why='not_configured')
        art = cap == 'game_art_search'
        q = (text[:220] + ART_SUFFIX) if art else text[:300]
        ck = hashlib.sha256(('%s|%s|%s' % (cap, 'j' if as_json else 't', q.lower())).encode()).hexdigest()[:24]
        cache = self._read('cache.json')
        hit = cache.get(ck)
        if hit and self.clock() - hit['t'] <= self.opts['cache_hours'] * 3600:
            self.cache_hits += 1
            return res(True, hit['answer'], cached=True)
        with self.lock:
            if self.used_today() >= self.opts['daily_cap']:
                self.refused += 1
                return res(False, 'Serper daily limit reached (%d per day). Try tomorrow.' % self.opts['daily_cap'], why='cap')
            if not self.spend_ok():
                self.refused += 1
                return res(False, 'Paid calls are not allowed (spend_cap %d paid calls per day, 0 = free only).' % self.opts.get('spend_cap', 0), why='spend_cap', error='spend_cap')
            self._count()
        try:
            data = self.search(q, timeout)
        except Exception as e:
            self.log('serper failed: %s' % type(e).__name__)      # type only
            return res(False, 'Serper did not answer.', why='upstream_error')
        self.live_calls += 1
        if art:
            rows = parse_images(data)
            if not rows:
                return res(True, '[]', cached=False)      # never cached
            answer = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
            cache = {k: v for k, v in cache.items() if self.clock() - v['t'] <= self.opts['cache_hours'] * 3600}
            cache[ck] = {'t': self.clock(), 'answer': answer}
            self._write('cache.json', cache)
            return res(True, answer, cached=False)
        rows = parse_results(data)
        if not rows:
            return res(True, '[]' if as_json else 'No results found.', cached=False)      # never cached
        answer = json.dumps(rows, ensure_ascii=False, separators=(',', ':')) if as_json else format_text(rows)
        cache = {k: v for k, v in cache.items() if self.clock() - v['t'] <= self.opts['cache_hours'] * 3600}
        cache[ck] = {'t': self.clock(), 'answer': answer}
        self._write('cache.json', cache)
        return res(True, answer, cached=False)

    def search(self, q, timeout=None):
        """The ONLY network call. Key goes into the X-API-KEY header here and nowhere else."""
        body = json.dumps({'q': q, 'gl': 'gb', 'num': MAX_RESULTS}).encode()
        url = SERPER_IMAGES_URL if q.endswith(ART_SUFFIX) else SERPER_URL
        req = urllib.request.Request(url, data=body, method='POST',
                                     headers={'X-API-KEY': self.opts['key'], 'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
        with self.opener(req, timeout=min(30, timeout) if timeout else 30) as x:
            return json.loads(x.read(2000000))

    def status(self):
        s = super().status()
        s['cache_hits'] = self.cache_hits
        return s
