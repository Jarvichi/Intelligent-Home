"""Ollama agent core: answers signed /ask requests for 'general_qa', 'summarize' and 'vision' by calling an Ollama server on ANOTHER machine
in the house (option ollama_url). It never runs Ollama itself and must never be pointed at the Raspberry Pi. stdlib only.
ollama_url empty = off: every request answers 'unavailable' at once so the AI layer skips this agent. Nothing leaves the home network, so text is NOT scrubbed.
Request and reply text are never logged unless option log_payloads is true (first 200 chars, one line). A picture is never logged."""
import json, os, re, threading, time, urllib.error, urllib.request
import jobs_link

VERSION = '0.1.0'
CAPABILITIES = ['general_qa', 'summarize', 'vision']
STOP_FILE = 'STOP'
MAX_IN = 8000
MAX_RAW = 200000
MAX_IMAGE_B64 = 1500000
SYSTEM = {'general_qa': 'You are a general assistant. Answer the question clearly and briefly in plain text. You have no tools.',
          'summarize': 'You are a general assistant. Summarize the text you are given clearly and briefly in plain text. Do not add facts that are not in the text.',
          'vision': 'You look at one picture and answer the question about it clearly and briefly in plain text. Describe only what is visible. Do not guess names or identify people.'}
_MODEL = re.compile(r'[A-Za-z0-9._:/-]{1,80}')


def _tag(v, default='-'):
    t = re.sub(r'[^A-Za-z0-9._:-]', '_', str(v if v is not None else default))[:64]
    return t or default


def _snip(s, n=200):
    s = str(s or '')[:n]
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n')


def _flag(v, default):
    return v if isinstance(v, bool) else default if v is None else str(v).lower() in ('true', '1', 'yes')


def clean_url(u):
    """Only http(s)://host[:port] with no path/query/credentials. Anything else = '' (off)."""
    u = str(u or '').strip().rstrip('/')
    return u if re.fullmatch(r'https?://[A-Za-z0-9._-]+(:\d{1,5})?', u) else ''


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

    def mdl(k, d):
        m = str(o.get(k) or d).strip()
        return m if _MODEL.fullmatch(m) else d
    return {'register_key': str(o.get('register_key') or '').strip(),
            'parent_url': str(o.get('parent_url') or 'http://172.30.32.1:8801').strip().rstrip('/'),
            'callback_url': str(o.get('callback_url') or '').strip().rstrip('/'), 'ollama_url': clean_url(o.get('ollama_url')),
            'model': mdl('model', 'llama3.2:3b'), 'vision_model': mdl('vision_model', 'llava:7b'),
            'timeout_s': num('timeout_s', 120, 5, 600), 'daily_cap': num('daily_cap', 500, 0, 100000),
            'shadow': _flag(o.get('shadow'), True), 'max_chars': num('max_chars', 1900, 100, 4000),
            'log_payloads': _flag(o.get('log_payloads'), False)}


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

    def used_today(self):
        try:
            with open(self._p('usage.json')) as f:
                return int(json.load(f).get(self.today(), 0))
        except (OSError, ValueError, AttributeError, TypeError):
            return 0

    def _count(self):
        try:
            with open(self._p('usage.json') + '.tmp', 'w') as f:
                json.dump({self.today(): self.used_today() + 1}, f)
            os.replace(self._p('usage.json') + '.tmp', self._p('usage.json'))
        except OSError:
            self.log('write usage.json failed')

    def stopped(self):
        return os.path.exists(self._p(STOP_FILE))

    def enabled(self):
        return bool(self.opts.get('ollama_url'))

    def reachable(self):
        """GET /api/tags on the Ollama server. -> (bool, [model names]). Never raises; short timeout so a sleeping host is skipped quickly."""
        if not self.enabled():
            return False, []
        try:
            with self.opener(urllib.request.Request(self.opts['ollama_url'] + '/api/tags', method='GET'), timeout=3) as x:
                names = [str(m.get('name')) for m in (json.loads(x.read(1000000)).get('models') or []) if isinstance(m, dict)]
            return True, names
        except Exception:
            return False, []

    def log_line(self, obj, cap, text, ok, answer, kw, t0):
        ctx = obj.get('context') if isinstance(obj.get('context'), dict) else {}
        f = ['ask', 'id=' + _tag(obj.get('request_id')), 'agent=ih_ai_layer_ollama', 'caller=' + _tag(obj.get('caller') or ctx.get('caller')), 'cap=' + _tag(cap),
             'in=%d' % len(text), 'out=%d' % (len(answer) if ok and isinstance(answer, str) else 0), 'ms=%d' % int((self.clock() - t0) * 1000),
             'ok=%s' % bool(ok), 'why=' + _tag(kw.get('error') or kw.get('why')), 'shadow=%s' % bool(self.opts.get('shadow'))]
        if self.opts.get('log_payloads'):
            f += ['req="%s"' % _snip(text), 'ans="%s"' % _snip(answer if ok else '')]
        return ' '.join(f)

    def model_for(self, cap):
        return self.opts['vision_model'] if cap == 'vision' else self.opts['model']

    def ask(self, obj):
        """obj: {text, capability, context, request_id, image?}. -> dict {ok, answer, request_id, ...}. Never raises."""
        rid, cap, text = obj.get('request_id'), obj.get('capability'), str(obj.get('text') or '').strip()
        image = obj.get('image')
        if not cap and obj.get('model'):
            cap = 'general_qa'
        t0 = self.clock()

        def res(ok, answer, **kw):
            self.log(self.log_line(obj, cap, text, ok, answer, kw, t0))
            return dict({'ok': ok, 'answer': answer, 'request_id': rid, 'agent': 'ih_ai_layer_ollama', 'free': True,
                         'model': self.model_for(cap) if cap in CAPABILITIES else self.opts['model'], 'local': True}, **kw)
        self.served += 1
        if cap not in CAPABILITIES or not text:
            return res(False, 'This agent only does general_qa, summarize and vision.', why='bad_request')
        if cap == 'vision' and (not isinstance(image, str) or not image.strip() or len(image) > MAX_IMAGE_B64):
            return res(False, 'vision needs one base64 picture.', why='bad_request')
        if obj.get('model') and obj.get('model') not in (self.opts['model'], self.opts['vision_model']):
            return res(False, 'This agent does not serve that model.', why='model_not_available', error='model_not_available')
        if self.stopped():
            return res(False, 'Ollama agent is stopped (kill switch).', why='stopped')
        if not self.enabled():
            return res(False, 'No Ollama server is set (ollama_url is empty), so this agent is off.', why='unavailable', error='unavailable')
        if self.opts['shadow']:
            return res(True, "[dry] shadow mode: would ask the Ollama server for '%s'. Nothing was sent." % cap, why='shadow', mode='dry')
        with self.lock:
            if self.used_today() >= self.opts['daily_cap']:
                self.refused += 1
                return res(False, 'Ollama daily limit reached (%d per day).' % self.opts['daily_cap'], why='cap')
            self._count()
        try:
            answer = self.generate(cap, text[:MAX_IN], image if cap == 'vision' else None)
        except Exception as e:
            why = 'timeout' if 'timed out' in str(e).lower() or type(e).__name__ == 'TimeoutError' else 'upstream_error' if isinstance(e, urllib.error.HTTPError) or not isinstance(e, OSError) else 'unavailable'
            self.log('ollama failed: %s' % type(e).__name__)
            return res(False, 'The Ollama server did not answer.', why=why, error=why)
        self.live_calls += 1
        if not answer:
            return res(False, 'The Ollama server returned no text.', why='empty')
        return res(True, answer[:self.opts['max_chars']], cached=False)

    def generate(self, cap, text, image=None):
        """The ONLY network call: POST /api/chat on the Ollama server (LAN only). -> reply text. Raises on any failure (incl. HTTP 500, model missing)."""
        user = {'role': 'user', 'content': text}
        if image:
            user['images'] = [''.join(image.split())]
        body = json.dumps({'model': self.model_for(cap), 'stream': False, 'messages': [{'role': 'system', 'content': SYSTEM[cap]}, user]}).encode()
        req = urllib.request.Request(self.opts['ollama_url'] + '/api/chat', data=body, method='POST', headers={'Content-Type': 'application/json'})
        with self.opener(req, timeout=self.opts['timeout_s']) as x:
            data = json.loads(x.read(MAX_RAW))
        msg = data.get('message') if isinstance(data, dict) else None
        out = msg.get('content') if isinstance(msg, dict) else ''
        return out.strip() if isinstance(out, str) else ''

    def status(self):
        now = self.clock()
        ok, names = self.reachable()
        return {'name': 'ih_ai_layer_ollama', 'version': VERSION, 'capabilities': CAPABILITIES, 'enabled': self.enabled(), 'server_reachable': ok,
                'models_present': {'model': self.opts['model'] in names, 'vision_model': self.opts['vision_model'] in names},
                'model': self.opts['model'], 'vision_model': self.opts['vision_model'], 'shadow': self.opts['shadow'], 'kill_switch': self.stopped(),
                'uptime_s': int(now - self.started), 'used_today': self.used_today(), 'daily_cap': self.opts['daily_cap'], 'served': self.served,
                'live_calls': self.live_calls, 'refused_by_cap': self.refused,
                'parent': {'link_enabled': bool(self.opts['register_key']), 'registered_s_ago': None if self.registered_at is None else int(now - self.registered_at),
                           'last_error': self.register_error}}

    def register(self, opener=None, now=None):
        """Signed POST to the parent /register. healthy = server set and reachable, so the layer skips this agent when the host is off. Never raises."""
        o = self.opts
        if not o['register_key']:
            self.register_error = 'no register_key'
            return False
        try:
            healthy, _ = self.reachable()
            body, headers = jobs_link.pack(o['register_key'], {'name': 'ih_ai_layer_ollama', 'version': VERSION, 'capabilities': CAPABILITIES,
                                                               'models': [o['model'], o['vision_model']], 'url': o['callback_url'] or 'http://172.30.32.1:8817', 'healthy': healthy}, now=now)
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
