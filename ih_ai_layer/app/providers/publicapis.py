"""PublicAPIs.io directory over its free MCP server (streamable HTTP, JSON-RPC 2.0, no key) for 'api_search', 'api_info', 'api_quickstart', 'api_compare'
and 'api_offerings'. Only the read-only tools are used (search_apis, get_api, get_quickstart, compare_apis, list_offerings). The owner-side tools of the server
(start_deal, send_message, get_deal: buying listings) are deliberately NOT implemented.
The request text is a search query or an API slug, so it is NOT scrubbed (same reasoning as SerpApi) and it is never logged. Answers are cached
(setting cache_hours); errors are never cached. One MCP session is kept per process and re-created once if the server says it expired."""
import hashlib, json, re, threading, urllib.error, urllib.request
from .base import Provider, USER_AGENT

MCP_URL = 'https://publicapis.io/mcp'
PROTOCOL = '2025-06-18'
SLUG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
AUTHS = ('none', 'api_key', 'oauth')
LANGS = ('javascript', 'python', 'curl')
TOOL = {'api_search': 'search_apis', 'api_info': 'get_api', 'api_quickstart': 'get_quickstart', 'api_compare': 'compare_apis', 'api_offerings': 'list_offerings'}


class BadRequest(Exception):
    pass


class PublicApis(Provider):
    NAME, LABEL, VERSION = 'publicapis', 'PublicAPIs.io', '0.1.0'
    CAPABILITIES = ['api_search', 'api_info', 'api_quickstart', 'api_compare', 'api_offerings']
    HAS_MODELS = False
    KEY_OPTION = ''
    KEY_REQUIRED = False
    DEFAULT_FREE = True
    DEFAULT_DAILY_CAP = 200
    MAX_DAILY_CAP = 1000

    @classmethod
    def extra_opts(cls, s, num):
        return {'cache_hours': num('cache_hours', 24, 0, 168), 'max_chars': num('max_chars', 6000, 100, 12000)}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.cache_hits = 0
        self.sid = None                       # MCP session id (one per process)
        self.sess_lock = threading.Lock()
        self.rpc_id = 0

    # ---- argument mapping ----
    @staticmethod
    def _slug(v):
        v = str(v or '').strip().lower()
        if not SLUG_RE.match(v):
            raise BadRequest('A valid API slug is needed (letters, digits, . _ -).')
        return v

    def build_args(self, cap, text, ctx):
        """-> arguments dict for the MCP tool. Raises BadRequest."""
        if cap == 'api_offerings':
            return {}
        if cap == 'api_search':
            a = {'query': text[:300]}
            if isinstance(ctx.get('category'), str) and ctx['category'].strip():
                a['category'] = ctx['category'].strip()[:60]
            if ctx.get('auth') in AUTHS:
                a['auth'] = ctx['auth']
            for k in ('free', 'https'):
                if isinstance(ctx.get(k), bool):
                    a[k] = ctx[k]
            lim = ctx.get('limit')
            if isinstance(lim, int) and not isinstance(lim, bool) and 1 <= lim <= 20:
                a['limit'] = lim
            return a
        if cap == 'api_compare':
            raw = ctx.get('slugs')
            parts = raw if isinstance(raw, list) else re.split(r'[\s,]+', text)
            slugs = [self._slug(p) for p in parts if str(p).strip()]
            if not 2 <= len(slugs) <= 5:
                raise BadRequest('api_compare needs 2 to 5 API slugs.')
            return {'slugs': slugs}
        slug = self._slug(ctx.get('slug') or text)
        if cap == 'api_quickstart':
            lang = ctx.get('language')
            return {'slug': slug, 'language': lang if lang in LANGS else 'python'}
        return {'slug': slug}

    # ---- MCP transport ----
    def _post(self, payload, timeout, sid=None, want_body=True):
        h = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'User-Agent': USER_AGENT}
        if sid:
            h['Mcp-Session-Id'] = sid
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), method='POST', headers=h)
        with self.opener(req, timeout=timeout) as x:
            raw = x.read(2000000)
            hd = getattr(x, 'headers', None)
            new_sid = hd.get('mcp-session-id') if hd is not None else None
        if not want_body:
            return None, new_sid
        return self._parse(raw), new_sid

    @staticmethod
    def _parse(raw):
        t = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
        t = t.strip()
        if t.startswith('{'):
            return json.loads(t)
        last = None
        for line in t.splitlines():
            if line.startswith('data:') and line[5:].strip():
                last = line[5:].strip()
        if last is None:
            raise ValueError('no data')
        return json.loads(last)

    def _next_id(self):
        self.rpc_id += 1
        return self.rpc_id

    def _init_session(self, timeout):
        body, sid = self._post({'jsonrpc': '2.0', 'id': self._next_id(), 'method': 'initialize',
                                'params': {'protocolVersion': PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': 'intelligent-home-ai-layer', 'version': self.VERSION}}}, timeout)
        if not isinstance(body, dict) or 'result' not in body:
            raise ValueError('initialize failed')
        self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, timeout, sid, want_body=False)
        self.sid = sid
        return sid

    def call_tool(self, name, args, timeout):
        """-> the JSON-RPC response dict. One session per process; re-initialised once if the server rejects it (HTTP 400/404)."""
        with self.sess_lock:
            for attempt in (0, 1):
                if not self.sid:
                    self._init_session(timeout)
                try:
                    body, _ = self._post({'jsonrpc': '2.0', 'id': self._next_id(), 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}, timeout, self.sid)
                    return body
                except urllib.error.HTTPError as e:
                    if attempt == 0 and e.code in (400, 404):
                        self.sid = None
                        continue
                    raise

    @staticmethod
    def extract(result):
        """-> (text, is_error) from a tools/call result."""
        if not isinstance(result, dict):
            return '', True
        parts = [c.get('text', '') for c in result.get('content') or [] if isinstance(c, dict) and c.get('type', 'text') == 'text' and isinstance(c.get('text'), str)]
        txt = '\n'.join(p for p in parts if p).strip()
        if not txt and result.get('structuredContent') is not None:
            txt = json.dumps(result['structuredContent'], ensure_ascii=False)
        return txt, bool(result.get('isError'))

    # ---- answering ----
    def ask(self, obj, timeout=None):
        """-> dict {ok, answer, request_id, ...}. Never raises; never logs text."""
        rid, cap, text = obj.get('request_id'), obj.get('capability'), str(obj.get('text') or '').strip()
        ctx = obj.get('context') if isinstance(obj.get('context'), dict) else {}
        t0 = self.clock()

        def res(ok, answer, **kw):
            self.log(self.log_line(obj, cap, text, ok, answer, kw, t0))
            return dict({'ok': ok, 'answer': answer, 'request_id': rid, 'agent': self.agent_name(), 'free': True, 'model': None}, **kw)
        self.served += 1
        if cap not in self.CAPABILITIES or (not text and cap != 'api_offerings' and not ctx.get('slug') and not ctx.get('slugs')):
            return res(False, 'This agent only does %s.' % ', '.join(self.CAPABILITIES), why='bad_request')
        if obj.get('model'):
            return res(False, 'This agent has no selectable models.', why='model_not_available', error='model_not_available')
        if self.stopped():
            return res(False, 'PublicAPIs.io provider is stopped (kill switch).', why='stopped')
        try:
            args = self.build_args(cap, text, ctx)
        except BadRequest as e:
            return res(False, str(e), why='bad_request')
        ck = hashlib.sha256(json.dumps([cap, args], sort_keys=True).encode()).hexdigest()[:24]
        ttl = self.opts['cache_hours'] * 3600
        cache = self._read('cache.json')
        hit = cache.get(ck)
        if hit and self.clock() - hit['t'] <= ttl:
            self.cache_hits += 1
            return res(True, hit['answer'], cached=True)
        if self.opts['shadow']:
            return res(True, "[dry] shadow mode: would ask PublicAPIs.io for '%s'. Nothing was sent." % cap, why='shadow', mode='dry')
        with self.lock:
            if self.used_today() >= self.opts['daily_cap']:
                self.refused += 1
                return res(False, 'PublicAPIs.io daily limit reached (%d per day). Try tomorrow.' % self.opts['daily_cap'], why='cap')
            self._count()
        to = self.opts['timeout_s'] if not timeout else max(1, min(self.opts['timeout_s'], timeout))
        try:
            body = self.call_tool(TOOL[cap], args, to)
            if not isinstance(body, dict) or body.get('error') or 'result' not in body:
                raise ValueError('rpc error')
            answer, is_err = self.extract(body['result'])
        except Exception as e:
            self.log('publicapis failed: %s' % type(e).__name__)      # type only
            return res(False, 'PublicAPIs.io did not answer.', why='upstream_error')
        self.live_calls += 1
        if is_err:
            return res(False, 'PublicAPIs.io reported an error.', why='upstream_error')
        if not answer:
            return res(False, 'PublicAPIs.io returned no text.', why='empty')
        answer = answer[:self.opts['max_chars']]
        if ttl > 0:
            cache = {k: v for k, v in cache.items() if self.clock() - v['t'] <= ttl}
            cache[ck] = {'t': self.clock(), 'answer': answer}
            self._write('cache.json', cache)
        return res(True, answer, cached=False)

    def status(self):
        s = super().status()
        s['cache_hits'] = self.cache_hits
        return s
