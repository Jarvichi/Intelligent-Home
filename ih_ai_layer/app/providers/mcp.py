"""Generic remote-MCP provider ('mcp'). One `providers` entry with name "mcp" connects ONE MCP server (streamable HTTP, https only); the entry can be
listed many times. The `settings` JSON describes the server and, above all, an ALLOWLIST of tools: only tools named in `tools` can ever be called.
  {"id": "short_id", "url": "https://example.com/mcp", "label": "Friendly name", "auth_header": "Authorization" (secret = the entry `key`),
   "tools": {"<tool_name>": {"capability": "cap_name", "arg": "query", "context_args": ["a", "b"]}},
   "keywords": {"cap_name": ["word", ...]}, "daily_cap", "cache_hours", "timeout_s", "max_chars", "shadow", "scrub"}
The allowlist is the human decision: a tool that is not listed is refused before any network call. The server's tools/list is read on first use and every
24 hours; only listed tools that the server really offers are callable. Request text, argument values and keys are never logged. Answers are the text
content of the tool result. Answers are cached (cache_hours) only for tools that the server marks readOnlyHint true; errors are never cached."""
import hashlib, json, re, threading, urllib.parse, urllib.request
from .base import Provider, parse_settings, scrub, MAX_IN
from .mcp_client import McpClient, extract, no_redirect_opener

ID_RE = re.compile(r'^[a-z0-9_]{1,24}$')
TOOL_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
CAP_RE = re.compile(r'^[a-z0-9_]{1,40}$')
ARG_RE = re.compile(r'^[A-Za-z0-9_.-]{1,40}$')
HEADER_RE = re.compile(r'^[A-Za-z0-9-]{1,40}$')
RESERVED_CAPS = frozenset(['general_qa', 'summarize', 'showtimes', 'web_search', 'current_facts', 'api_search', 'api_info', 'api_quickstart', 'api_compare', 'api_offerings'])
LIST_EVERY_S = 24 * 3600


class NotAllowed(Exception):
    pass


class BadRequest(Exception):
    pass


def _scalar(v):
    if isinstance(v, str):
        return v[:500]
    if isinstance(v, (bool, int, float)):
        return v
    return None


def parse_spec(entry):
    """entry: {name, key, settings...} -> (spec dict, None) or (None, reason). The reason never contains setting values."""
    s = parse_settings(entry.get('settings'))
    i = s.get('id')
    if not isinstance(i, str) or not ID_RE.match(i):
        return None, 'bad or missing id (a-z, 0-9, _ up to 24)'
    url = s.get('url')
    if not isinstance(url, str) or not url.startswith('https://') or not urllib.parse.urlparse(url).hostname or re.search(r'\s', url):
        return None, 'bad or missing url (https only)'
    label = s.get('label') if isinstance(s.get('label'), str) and s.get('label').strip() else i
    label = re.sub(r'[^A-Za-z0-9 ._-]', '', label)[:40].strip() or i
    hdr = s.get('auth_header')
    if hdr not in (None, '') and (not isinstance(hdr, str) or not HEADER_RE.match(hdr)):
        return None, 'bad auth_header'
    tools, caps = {}, {}
    raw = s.get('tools')
    if not isinstance(raw, dict) or not raw:
        return None, 'no tools allowed (the allowlist is empty)'
    for tn, ts in raw.items():
        if not isinstance(tn, str) or not TOOL_RE.match(tn) or not isinstance(ts, dict):
            return None, 'bad tool entry'
        cap = ts.get('capability')
        if not isinstance(cap, str) or not CAP_RE.match(cap) or cap in RESERVED_CAPS:
            return None, 'bad or reserved capability for a tool'
        if cap in caps:
            return None, 'two tools share one capability'
        arg = ts.get('arg')
        if arg not in (None, '') and (not isinstance(arg, str) or not ARG_RE.match(arg)):
            return None, 'bad arg name'
        ca = ts.get('context_args') or []
        if not isinstance(ca, list) or len(ca) > 10 or not all(isinstance(a, str) and ARG_RE.match(a) for a in ca):
            return None, 'bad context_args'
        tools[tn] = {'capability': cap, 'arg': arg or None, 'context_args': list(ca)}
        caps[cap] = tn
    kw = {}
    for cap, words in (s.get('keywords') or {}).items() if isinstance(s.get('keywords'), dict) else []:
        if cap in caps and isinstance(words, list):
            ws = [w.strip().lower() for w in words if isinstance(w, str) and 1 <= len(w.strip()) <= 40][:30]
            if ws:
                kw[cap] = ws
    return {'id': i, 'url': url, 'label': label, 'auth_header': hdr or None, 'tools': tools, 'cap_tool': caps, 'keywords': kw}, None


class Mcp(Provider):
    NAME, LABEL, VERSION = 'mcp', 'MCP', '0.1.0'
    CAPABILITIES = []
    HAS_MODELS = False
    KEY_OPTION = ''
    KEY_REQUIRED = False
    DEFAULT_FREE = True
    DEFAULT_DAILY_CAP = 100
    MAX_DAILY_CAP = 1000

    @classmethod
    def extra_opts(cls, s, num):
        return {'cache_hours': num('cache_hours', 1, 0, 168), 'max_chars': num('max_chars', 6000, 100, 12000),
                'scrub': s.get('scrub', True) not in (False, 'false', 'False', '0', 0)}

    @classmethod
    def create(cls, entry, flat_key, data, log, opener, clock, today=None):
        """-> Mcp instance, or None (reason logged, no setting values). `data` is the providers folder."""
        spec, err = parse_spec(entry)
        if spec is None:
            log('mcp entry skipped: %s' % err)
            return None
        import os
        kw = {'today': today} if today else {}
        return cls(cls.make_opts(entry, flat_key), os.path.join(data, 'mcp_' + spec['id']), spec=spec, log=log, opener=opener, clock=clock, **kw)

    def __init__(self, opts, data, spec=None, **kw):
        super().__init__(opts, data, **kw)
        self.spec = spec
        self.id, self.LABEL = spec['id'], spec['label']
        self.tools = spec['tools']                                  # THE allowlist
        self.CAPABILITIES = sorted(spec['cap_tool'])
        self.cache_hits = 0
        self.available = None                                       # {tool name: annotations} from the server's tools/list
        self.listed_at = 0
        self.list_lock = threading.Lock()
        hdrs = {}
        if spec['auth_header'] and opts['key']:
            k = opts['key']
            if spec['auth_header'].lower() == 'authorization' and not re.match(r'^[A-Za-z]+ \S', k):
                k = 'Bearer ' + k
            hdrs[spec['auth_header']] = k
        op = self.opener
        if op is urllib.request.urlopen:
            op = no_redirect_opener()                               # the secret header must not follow a redirect
        self.client = McpClient(spec['url'], op, self.VERSION, hdrs)

    def agent_name(self):
        return 'ih_ai_layer_mcp_' + self.id

    def keywords(self):
        return self.spec['keywords']

    def configured(self):
        return not self.spec['auth_header'] or bool(self.opts['key'])

    def config_error(self):
        if '\n' in self.opts['key'] or '\r' in self.opts['key']:
            return '%s: the key contains a line break.' % self.LABEL
        return None

    # ---- the ONLY way to call a tool ----
    def invoke(self, tool, args, timeout):
        if tool not in self.tools:
            raise NotAllowed('tool not on the allowlist')
        return self.client.call_tool(tool, args, timeout)

    def refresh_tools(self, timeout):
        """Read tools/list on first use and every 24 h. Metadata-only log of allowlisted tools the server does not offer."""
        with self.list_lock:
            if self.available is not None and self.clock() - self.listed_at < LIST_EVERY_S:
                return
            listed = self.client.list_tools(timeout)
            offered = {t['name']: (t.get('annotations') if isinstance(t.get('annotations'), dict) else {}) for t in listed if isinstance(t.get('name'), str)}
            self.available = {n: offered[n] for n in self.tools if n in offered}
            self.listed_at = self.clock()
            missing = sorted(n for n in self.tools if n not in offered)
            self.log('mcp %s tools/list: offered=%d allowed=%d available=%d missing=%s' % (self.id, len(offered), len(self.tools), len(self.available), ','.join(missing) or '-'))
            writes = self.write_tools()
            if writes:
                self.log('mcp %s write_tool: %s' % (self.id, ','.join(writes)))

    def write_tools(self):
        """Available allowlisted tools whose server annotations do not say readOnlyHint true."""
        return sorted(n for n, a in (self.available or {}).items() if a.get('readOnlyHint') is not True)

    # ---- arguments ----
    def build_args(self, cap, text, ctx):
        spec = self.tools[self.spec['cap_tool'][cap]]
        a = {}
        if spec['arg']:
            if not text:
                raise BadRequest('This request needs text.')
            a[spec['arg']] = (scrub(text[:MAX_IN]) if self.opts['scrub'] else text[:MAX_IN])[:1000]
        for k in spec['context_args']:
            if k == spec['arg']:
                continue
            v = ctx.get(k)
            if isinstance(v, list):
                v = [_scalar(x) for x in v[:20]]
                v = v if all(x is not None for x in v) else None
            else:
                v = _scalar(v)
            if v is not None:
                a[k] = v
        return a

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
        if cap not in self.spec['cap_tool']:
            return res(False, 'This agent only does %s.' % ', '.join(self.CAPABILITIES), why='bad_request')
        tool = self.spec['cap_tool'][cap]
        if obj.get('model'):
            return res(False, 'This agent has no selectable models.', why='model_not_available', error='model_not_available')
        if self.stopped():
            return res(False, '%s provider is stopped (kill switch).' % self.LABEL, why='stopped')
        if not self.configured():
            return res(False, '%s is not configured (no key).' % self.LABEL, why='not_configured')
        ce = self.config_error()
        if ce:
            return res(False, ce, why='not_configured')
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
            return res(True, "[dry] shadow mode: would ask %s for '%s'. Nothing was sent." % (self.LABEL, cap), why='shadow', mode='dry')
        with self.lock:
            if self.used_today() >= self.opts['daily_cap']:
                self.refused += 1
                return res(False, '%s daily limit reached (%d per day). Try tomorrow.' % (self.LABEL, self.opts['daily_cap']), why='cap')
            self._count()
        to = self.opts['timeout_s'] if not timeout else max(1, min(self.opts['timeout_s'], timeout))
        try:
            self.refresh_tools(to)
            if tool not in (self.available or {}):
                return res(False, '%s does not offer the tool for %s.' % (self.LABEL, cap), why='tool_unavailable')
            body = self.invoke(tool, args, to)
            if not isinstance(body, dict) or body.get('error') or 'result' not in body:
                raise ValueError('rpc error')
            answer, is_err = extract(body['result'])
        except Exception as e:
            self.log('mcp %s failed: %s' % (self.id, type(e).__name__))      # type only
            return res(False, '%s did not answer.' % self.LABEL, why='upstream_error')
        self.live_calls += 1
        if is_err:
            return res(False, '%s reported an error.' % self.LABEL, why='upstream_error')
        if not answer:
            return res(False, '%s returned no text.' % self.LABEL, why='empty')
        answer = answer[:self.opts['max_chars']]
        if ttl > 0 and self.available.get(tool, {}).get('readOnlyHint') is True:
            cache = {k: v for k, v in cache.items() if self.clock() - v['t'] <= ttl}
            cache[ck] = {'t': self.clock(), 'answer': answer}
            self._write('cache.json', cache)
        return res(True, answer, cached=False)

    def status(self):
        s = super().status()
        host = urllib.parse.urlparse(self.spec['url']).hostname
        s.update({'id': self.id, 'label': self.LABEL, 'host': host, 'cache_hits': self.cache_hits,
                  'tools_allowed': sorted(self.tools), 'tools_available': None if self.available is None else sorted(self.available),
                  'write_tools': self.write_tools()})
        return s
