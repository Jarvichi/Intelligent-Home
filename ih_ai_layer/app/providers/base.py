"""Shared core of the in-process AI providers (Phase 4: these used to be separate agent add-ons). stdlib only.
A provider answers one request at a time for the router (ailayer.Layer), in the same process. Behaviour is the same as the old agent add-ons:
text is scrubbed before it leaves the house, the request context is never sent, the key is only put in the request header (or URL for SerpApi)
and is never logged, returned or put in /status, and the daily cap and the paid-call cap are counted BEFORE the outside call is made.
Counters live in <data>/providers/<name>/ (usage.json, paid.json). A file STOP in that folder stops that one provider."""
import json, os, re, threading, time, urllib.request

CAPS_QA = ['general_qa', 'summarize']
MAX_IN = 8000
SYSTEM = {'general_qa': 'You are a general assistant. Answer the question clearly and briefly in plain text. You know nothing about the asker, their home or their location; do not ask for personal details.',
          'summarize': 'You are a general assistant. Summarize the text you are given clearly and briefly in plain text. Do not add facts that are not in the text.'}

_EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_URL = re.compile(r'(https?://[^\s?#]+)[?#]\S*')
_MAC = re.compile(r'\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b')
_IPV4 = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
_PHONE = re.compile(r'(?<![\w])\+?\d[\d ()\-.]{7,}\d(?![\w])')
_DIGITS = re.compile(r'\d{6,}')
_TOKEN = re.compile(r'\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{24,}\b')


def _snip(s, n=200):
    """First n chars as ONE line: backslash, quote and newlines escaped. Only used when log_payloads is true."""
    s = str(s or '')[:n]
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n')


def _tag(v, default='-'):
    """A short safe token for a log line (letters, digits and . _ : - only)."""
    t = re.sub(r'[^A-Za-z0-9._:-]', '_', str(v if v is not None else default))[:64]
    return t or default


def scrub(text):
    """Remove private-looking data before text leaves the house. Order matters (specific first)."""
    t = _EMAIL.sub('[email]', text)
    t = _URL.sub(r'\1', t)
    t = _MAC.sub('[mac]', t)
    t = _IPV4.sub('[ip]', t)
    t = _PHONE.sub('[number]', t)
    t = _DIGITS.sub('[number]', t)
    t = _TOKEN.sub('[token]', t)
    return t


def _flag(v):
    return v if isinstance(v, bool) else str(v).lower() in ('true', '1', 'yes')


def parse_settings(raw):
    """The `settings` text of a providers entry (a JSON object) -> dict. Anything odd = {}."""
    if isinstance(raw, dict):
        return raw
    try:
        d = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


class Provider:
    """Subclasses set the class attributes and implement generate(cap, text, timeout)."""
    NAME = ''                         # short name used in the `providers` option, e.g. 'groq'
    LABEL = ''                        # shown in messages, e.g. 'Groq'
    VERSION = '0.1.0'
    CAPABILITIES = CAPS_QA
    DEFAULT_MODEL = ''
    MODEL_PATTERN = r'[A-Za-z0-9._:/-]{1,60}'
    HAS_MODELS = True                 # False: no selectable models (SerpApi)
    KEY_OPTION = ''                   # name of the flat option that can hold this provider's key (the settings hub fills it)
    KEY_REQUIRED = True
    DEFAULT_FREE = False
    DEFAULT_DAILY_CAP = 50
    MAX_DAILY_CAP = 1000

    # ---- options ----
    @classmethod
    def agent_name(cls):
        return 'ih_ai_layer_' + cls.NAME

    @classmethod
    def make_opts(cls, entry, flat_key=''):
        """entry: {name, enabled, key, model, settings}. -> options dict. Entry key wins over the flat option key."""
        s = parse_settings(entry.get('settings'))

        def num(k, d, lo, hi):
            try:
                return max(lo, min(hi, int(s.get(k, d))))
            except (TypeError, ValueError):
                return d
        model = str(entry.get('model') or s.get('model') or cls.DEFAULT_MODEL).strip()
        if not re.fullmatch(cls.MODEL_PATTERN, model):
            model = cls.DEFAULT_MODEL
        sh = s.get('shadow', False)
        o = {'key': str(entry.get('key') or flat_key or '').strip(), 'model': model,
             'spend_cap': num('spend_cap', 0, 0, 1000), 'free_tier': _flag(s.get('free_tier', cls.DEFAULT_FREE)),
             'daily_cap': num('daily_cap', cls.DEFAULT_DAILY_CAP, 0, cls.MAX_DAILY_CAP), 'timeout_s': num('timeout_s', 30, 5, 120),
             'log_payloads': _flag(s.get('log_payloads', False)),
             'shadow': sh if isinstance(sh, bool) else str(sh).lower() not in ('false', '0', 'no'),
             'max_chars': num('max_chars', 1900, 100, 4000)}
        o.update(cls.extra_opts(s, num))
        return o

    @classmethod
    def extra_opts(cls, s, num):
        return {}

    def __init__(self, opts, data, log=None, opener=None, clock=time.time, today=lambda: time.strftime('%Y-%m-%d')):
        self.opts, self.data = opts, data
        self.log = log or (lambda m: None)
        self.opener, self.clock, self.today = opener or urllib.request.urlopen, clock, today
        self.lock = threading.Lock()
        self.started = clock()
        self.served = self.live_calls = self.refused = 0
        try:
            os.makedirs(self.data, exist_ok=True)
        except OSError:
            pass

    # ---- counters (files) ----
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
        return int(self._read('usage.json').get(self.today(), 0))

    def _count(self):
        self._write('usage.json', {self.today(): self.used_today() + 1})      # old days are dropped

    def paid_today(self):
        return int(self._read('paid.json').get(self.today(), 0))

    def is_free(self):
        """A call is paid unless the owner marked the plan free (setting free_tier) or the model name ends in ':free'. Unknown = paid."""
        return bool(self.opts.get('free_tier')) or str(self.opts.get('model', '')).endswith(':free')

    def spend_ok(self):
        """Call under self.lock. Free calls always pass. Paid calls need spend_cap > 0 and fewer paid calls today than spend_cap; passing counts the call."""
        if self.is_free():
            return True
        if self.paid_today() >= self.opts.get('spend_cap', 0):
            return False
        self._write('paid.json', {self.today(): self.paid_today() + 1})
        return True

    def stopped(self):
        return os.path.exists(self._p('STOP'))

    # ---- state seen by the router ----
    def configured(self):
        return bool(self.opts['key']) or not self.KEY_REQUIRED

    def config_error(self):
        """Message when something besides the key is missing (hook; None = fine)."""
        return None

    def healthy(self):
        return self.configured() and self.config_error() is None and not self.stopped()

    def models(self):
        return [self.opts['model']] if self.HAS_MODELS else []

    # ---- log ----
    def log_line(self, obj, cap, text, ok, answer, kw, t0):
        """ONE structured line per ask: metadata only. Request/answer snippets (200 chars) only when log_payloads is true. Never keys or headers."""
        ctx = obj.get('context') if isinstance(obj.get('context'), dict) else {}
        f = ['ask', 'id=' + _tag(obj.get('request_id')), 'agent=' + self.agent_name(), 'caller=' + _tag(obj.get('caller') or ctx.get('caller')), 'cap=' + _tag(cap),
             'model=' + _tag(self.opts.get('model')), 'in=%d' % len(text), 'out=%d' % (len(answer) if ok and isinstance(answer, str) else 0),
             'ms=%d' % int((self.clock() - t0) * 1000), 'ok=%s' % bool(ok), 'why=' + _tag(kw.get('error') or kw.get('why')),
             'free=%s' % self.is_free(), 'shadow=%s' % bool(self.opts.get('shadow', False))]
        if self.opts.get('log_payloads'):
            f += ['req="%s"' % _snip(text), 'ans="%s"' % _snip(answer if ok else '')]
        return ' '.join(f)

    # ---- answering ----
    def ask(self, obj, timeout=None):
        """obj: {text, capability, context, request_id, model?}. -> dict {ok, answer, request_id, ...}. Never raises; never logs text.
        timeout: upper limit (seconds) for the outside call, from the router (agent_timeout_s)."""
        rid, cap, text = obj.get('request_id'), obj.get('capability'), str(obj.get('text') or '').strip()
        if not cap and obj.get('model'):
            cap = 'general_qa'  # forced model without a capability: plain question
        t0 = self.clock()

        def res(ok, answer, **kw):
            self.log(self.log_line(obj, cap, text, ok, answer, kw, t0))
            return dict({'ok': ok, 'answer': answer, 'request_id': rid, 'agent': self.agent_name(), 'free': self.is_free(), 'model': self.opts.get('model')}, **kw)
        self.served += 1
        if cap not in self.CAPABILITIES or not text:
            return res(False, 'This agent only does %s.' % ' and '.join(self.CAPABILITIES), why='bad_request')
        if obj.get('model') and (not self.HAS_MODELS or obj.get('model') != self.opts['model']):
            return res(False, 'This agent does not serve that model.' if self.HAS_MODELS else 'This agent has no selectable models.',
                       why='model_not_available', error='model_not_available')
        if self.stopped():
            return res(False, '%s provider is stopped (kill switch).' % self.LABEL, why='stopped')
        if not self.configured():
            return res(False, '%s is not configured (no key for provider %s).' % (self.LABEL, self.NAME), why='not_configured')
        ce = self.config_error()
        if ce:
            return res(False, ce, why='not_configured')
        pre = self.before_call(cap, text, obj, res)
        if pre is not None:
            return pre
        if self.opts['shadow']:
            return res(True, "[dry] shadow mode: would ask %s for '%s'. Nothing was sent." % (self.LABEL, cap), why='shadow', mode='dry')
        with self.lock:
            if self.used_today() >= self.opts['daily_cap']:
                self.refused += 1
                return res(False, '%s daily limit reached (%d per day). Try tomorrow.' % (self.LABEL, self.opts['daily_cap']), why='cap')
            if not self.spend_ok():
                self.refused += 1
                return res(False, 'Paid calls are not allowed (spend_cap %d paid calls per day, 0 = free only).' % self.opts.get('spend_cap', 0), why='spend_cap', error='spend_cap')
            self._count()
        to = self.opts['timeout_s'] if not timeout else max(1, min(self.opts['timeout_s'], timeout))
        try:
            answer = self.generate(cap, scrub(text[:MAX_IN]), to)
        except Exception as e:
            self.log('%s failed: %s' % (self.NAME, type(e).__name__))      # type only: never the message
            return res(False, '%s did not answer.' % self.LABEL, why='upstream_error')
        self.live_calls += 1
        if not answer:
            return res(False, '%s returned no text.' % self.LABEL, why='empty')
        return res(True, answer[:self.opts['max_chars']], cached=False)

    def before_call(self, cap, text, obj, res):
        """Hook before shadow/caps (SerpApi: cache). Return a result dict to stop, None to go on."""
        return None

    def generate(self, cap, text, timeout):
        raise NotImplementedError

    def status(self):
        """No secrets: the key is only reported as set / not set."""
        return {'name': self.agent_name(), 'provider': self.NAME, 'version': self.VERSION, 'capabilities': self.CAPABILITIES, 'configured': self.configured(),
                'model': self.opts['model'] if self.HAS_MODELS else None, 'shadow': self.opts['shadow'], 'kill_switch': self.stopped(),
                'used_today': self.used_today(), 'daily_cap': self.opts['daily_cap'], 'spend_cap': self.opts.get('spend_cap', 0), 'free': self.is_free(),
                'paid_today': self.paid_today(), 'served': self.served, 'live_calls': self.live_calls, 'refused_by_cap': self.refused}


class ChatProvider(Provider):
    """OpenAI-style chat completions with a Bearer key (Groq, OpenRouter, xAI Grok, Pollinations)."""
    API_URL = ''

    def headers(self):
        return {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.opts['key']}

    def generate(self, cap, text, timeout):
        """The ONLY outside call. Key goes in the Authorization header here and nowhere else. -> reply text (may be empty)."""
        body = json.dumps({'model': self.opts['model'], 'max_tokens': 1024,
                           'messages': [{'role': 'system', 'content': SYSTEM[cap]}, {'role': 'user', 'content': text}]}).encode()
        req = urllib.request.Request(self.API_URL, data=body, method='POST', headers=self.headers())
        with self.opener(req, timeout=timeout) as x:
            data = json.loads(x.read(2000000))
        try:
            c = data['choices'][0]['message']['content']
            return c.strip() if isinstance(c, str) else ''
        except (KeyError, IndexError, TypeError, AttributeError):
            return ''
