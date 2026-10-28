"""OpenAI agent core: answers signed /ask requests for 'general_qa' and 'summarize'. stdlib only.
Sends text to an OUTSIDE service, so the text is scrubbed first and the request context is never sent.
The OpenAI key (option openai_key) is only put in the Authorization (Bearer) header and is never logged, returned or put in /status.
Request and reply text are logged (first 200 chars, one line) ONLY when option log_payloads is true (default false); otherwise one metadata line per /ask. Daily cap is counted per local date in /data/usage.json BEFORE the call is made."""
import json, os, re, threading, time, urllib.request
import jobs_link

VERSION = '0.1.2'
CAPABILITIES = ['general_qa', 'summarize']
API_URL = 'https://api.openai.com/v1/responses'
STOP_FILE = 'STOP'
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
    """First n chars as ONE line: backslash, quote and newlines escaped. Only used when option log_payloads is true."""
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
    model = str(o.get('model') or 'gpt-6-luna').strip()
    if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,60}', model):
        model = 'gpt-6-luna'
    sh = o.get('shadow', True)
    return {'openai_key': str(o.get('openai_key') or '').strip(), 'register_key': str(o.get('register_key') or '').strip(),
            'parent_url': str(o.get('parent_url') or 'http://172.30.32.1:8801').strip().rstrip('/'),
            'callback_url': str(o.get('callback_url') or '').strip().rstrip('/'), 'model': model,
            'spend_cap': num('spend_cap', 0, 0, 1000), 'free_tier': _flag(o.get('free_tier', False)),
            'daily_cap': num('daily_cap', 50, 0, 1000), 'timeout_s': num('timeout_s', 30, 5, 120),
            'log_payloads': str(o.get('log_payloads', False)).lower() in ('true', '1', 'yes'),
            'shadow': sh if isinstance(sh, bool) else str(sh).lower() not in ('false', '0', 'no'),
            'max_chars': num('max_chars', 1900, 100, 4000)}


class Agent:
    def __init__(self, opts, data='/data', log=None, opener=None, clock=time.time, today=lambda: time.strftime('%Y-%m-%d')):
        self.opts, self.data = opts, data
        self.log = log or (lambda m: None)
        self.opener, self.clock, self.today = opener or urllib.request.urlopen, clock, today
        self.lock = threading.Lock()
        self.started = clock()
        self.served = self.live_calls = self.refused = 0
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
        return int(self._read('usage.json').get(self.today(), 0))

    def _count(self):
        self._write('usage.json', {self.today(): self.used_today() + 1})      # old days are dropped

    def paid_today(self):
        return int(self._read('paid.json').get(self.today(), 0))

    def is_free(self):
        """A call is paid unless the owner marked the plan free (option free_tier) or the model name ends in ':free'. Unknown = paid."""
        return bool(self.opts.get('free_tier')) or str(self.opts.get('model', '')).endswith(':free')

    def spend_ok(self):
        """Call under self.lock. Free calls always pass. Paid calls need spend_cap > 0 and fewer paid calls today than spend_cap; passing counts the call."""
        if self.is_free():
            return True
        if self.paid_today() >= self.opts.get('spend_cap', 0):
            return False
        self._write('paid.json', {self.today(): self.paid_today() + 1})
        return True

    def log_line(self, name, obj, cap, text, ok, answer, kw, t0):
        """ONE structured line per /ask: metadata only. Request/answer snippets (200 chars) only when option log_payloads is true. Never keys or headers."""
        ctx = obj.get('context') if isinstance(obj.get('context'), dict) else {}
        f = ['ask', 'id=' + _tag(obj.get('request_id')), 'agent=' + name, 'caller=' + _tag(obj.get('caller') or ctx.get('caller')), 'cap=' + _tag(cap),
             'model=' + _tag(self.opts.get('model')), 'in=%d' % len(text), 'out=%d' % (len(answer) if ok and isinstance(answer, str) else 0),
             'ms=%d' % int((self.clock() - t0) * 1000), 'ok=%s' % bool(ok), 'why=' + _tag(kw.get('error') or kw.get('why')),
             'free=%s' % self.is_free(), 'shadow=%s' % bool(self.opts.get('shadow', False))]
        if self.opts.get('log_payloads'):
            f += ['req="%s"' % _snip(text), 'ans="%s"' % _snip(answer if ok else '')]
        return ' '.join(f)

    def stopped(self):
        return os.path.exists(self._p(STOP_FILE))

    def ask(self, obj):
        """obj: {text, capability, context, request_id}. -> dict {ok, answer, request_id, ...}. Never raises; never logs text."""
        rid, cap, text = obj.get('request_id'), obj.get('capability'), str(obj.get('text') or '').strip()
        if not cap and obj.get('model'):
            cap = 'general_qa'  # forced model without a capability: plain question

        t0 = self.clock()

        def res(ok, answer, **kw):
            self.log(self.log_line('ih_ai_layer_openai', obj, cap, text, ok, answer, kw, t0))
            return dict({'ok': ok, 'answer': answer, 'request_id': rid, 'agent': 'ih_ai_layer_openai', 'free': self.is_free(), 'model': self.opts.get('model')}, **kw)
        self.served += 1
        if cap not in CAPABILITIES or not text:
            return res(False, 'This agent only does general_qa and summarize.', why='bad_request')
        if obj.get('model') and obj.get('model') != self.opts['model']:
            return res(False, 'This agent does not serve that model.', why='model_not_available', error='model_not_available')
        if self.stopped():
            return res(False, 'OpenAI agent is stopped (kill switch).', why='stopped')
        if not self.opts['openai_key']:
            return res(False, 'OpenAI is not configured (no openai_key option).', why='not_configured')
        if self.opts['shadow']:
            return res(True, "[dry] shadow mode: would ask OpenAI for '%s'. Nothing was sent." % cap, why='shadow', mode='dry')
        with self.lock:
            if self.used_today() >= self.opts['daily_cap']:
                self.refused += 1
                return res(False, 'OpenAI daily limit reached (%d per day). Try tomorrow.' % self.opts['daily_cap'], why='cap')
            if not self.spend_ok():
                self.refused += 1
                return res(False, 'Paid calls are not allowed (spend_cap %d paid calls per day, 0 = free only).' % self.opts.get('spend_cap', 0), why='spend_cap', error='spend_cap')
            self._count()
        try:
            answer = self.generate(cap, scrub(text[:MAX_IN]))
        except Exception as e:
            self.log('openai failed: %s' % type(e).__name__)      # type only: never the message
            return res(False, 'OpenAI did not answer.', why='upstream_error')
        self.live_calls += 1
        if not answer:
            return res(False, 'OpenAI returned no text.', why='empty')
        return res(True, answer[:self.opts['max_chars']], cached=False)

    def generate(self, cap, text):
        """The ONLY outside call. Key goes in the Authorization header here and nowhere else. -> reply text (may be empty)."""
        body = json.dumps({'model': self.opts['model'], 'instructions': SYSTEM[cap], 'input': text, 'max_output_tokens': 1024, 'store': False}).encode()
        req = urllib.request.Request(API_URL, data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.opts['openai_key']})
        with self.opener(req, timeout=self.opts['timeout_s']) as x:
            data = json.loads(x.read(2000000))
        try:
            parts = [c.get('text', '') for o in data.get('output', []) if o.get('type') == 'message' for c in o.get('content', [])
                     if isinstance(c.get('text', ''), str)]
            return ''.join(parts).strip()
        except (TypeError, AttributeError):
            return ''

    def status(self):
        now = self.clock()
        return {'name': 'ih_ai_layer_openai', 'version': VERSION, 'capabilities': CAPABILITIES, 'configured': bool(self.opts['openai_key']),
                'model': self.opts['model'], 'shadow': self.opts['shadow'], 'kill_switch': self.stopped(), 'uptime_s': int(now - self.started),
                'used_today': self.used_today(), 'daily_cap': self.opts['daily_cap'], 'spend_cap': self.opts.get('spend_cap', 0), 'free': self.is_free(), 'paid_today': self.paid_today(), 'served': self.served, 'live_calls': self.live_calls,
                'refused_by_cap': self.refused,
                'parent': {'link_enabled': bool(self.opts['register_key']), 'registered_s_ago': None if self.registered_at is None else int(now - self.registered_at),
                           'last_error': self.register_error}}

    def register(self, opener=None, now=None):
        """Signed POST to the parent /register. Never raises, never sends the OpenAI key. -> bool."""
        o = self.opts
        if not o['register_key']:
            self.register_error = 'no register_key'
            return False
        try:
            body, headers = jobs_link.pack(o['register_key'], {'name': 'ih_ai_layer_openai', 'version': VERSION, 'capabilities': CAPABILITIES,
                                                               'models': [o['model']], 'url': o['callback_url'] or 'http://172.30.32.1:8811', 'healthy': True}, now=now)
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
