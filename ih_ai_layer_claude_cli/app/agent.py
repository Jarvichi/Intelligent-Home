"""Claude CLI agent core: answers signed /ask requests for 'general_qa' and 'summarize' by running the Claude Code CLI in print mode. stdlib only.
No API key: the CLI uses a login that the owner creates himself inside this add-on (stored under /data/claude-home). Claude never does the login.
Sends text to an OUTSIDE service (Anthropic via the CLI), so the text is scrubbed first and the request context is never sent.
The subprocess gets a MINIMAL environment (PATH, HOME, LANG only: none of this add-on's options or secrets), an empty temp working dir, all tools off, one call at a time, a hard timeout.
Request and reply text are logged (first 200 chars, one line) ONLY when option log_payloads is true (default false); otherwise one metadata line per /ask.
Gates, in order: shadow, daily cap, spend cap/free tier, weekly usage gate (usage_url -> JSON {week_percent}). A refused call is never counted."""
import json, os, re, shutil, subprocess, tempfile, threading, time, urllib.request
import jobs_link

VERSION = '0.1.0'
CAPABILITIES = ['general_qa', 'summarize']
STOP_FILE = 'STOP'
MAX_IN = 8000
MAX_RAW = 200000          # stdout bytes kept before the max_chars cut
CLI = 'claude'
# UNVERIFIED flags (not tested against a real CLI; check `claude --help` of the installed version before going live):
#   -p                      print mode: answer once, exit, prompt read from stdin
#   --tools ""              no built-in tools at all (documented form for Claude Code >= 2.x; older versions: --disallowedTools "...")
#   --permission-mode dontAsk   never show a permission prompt (anything not allowed is denied)
#   --strict-mcp-config --mcp-config '{"mcpServers":{}}'   load no MCP servers
#   --disable-slash-commands    no skills / slash commands
#   --setting-sources ""    read no user/project/local settings files (no hooks)
#   --no-session-persistence    do not write session files
#   --output-format text    plain text answer
#   --system-prompt <text>  replace the default coding-agent prompt
#   --model <name>          optional, from option model
TOOL_FLAGS = ['--tools', '', '--permission-mode', 'dontAsk', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
              '--disable-slash-commands', '--setting-sources', '', '--no-session-persistence', '--output-format', 'text']
SYSTEM = {'general_qa': 'You are a general assistant. Answer the question clearly and briefly in plain text. You know nothing about the asker, their home or their location; do not ask for personal details. You have no tools.',
          'summarize': 'You are a general assistant. Summarize the text you are given clearly and briefly in plain text. Do not add facts that are not in the text. You have no tools.'}

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
    model = str(o.get('model') or 'sonnet').strip()
    if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,60}', model):
        model = 'sonnet'
    sh = o.get('shadow', True)
    return {'register_key': str(o.get('register_key') or '').strip(),
            'parent_url': str(o.get('parent_url') or 'http://172.30.32.1:8801').strip().rstrip('/'),
            'callback_url': str(o.get('callback_url') or '').strip().rstrip('/'), 'model': model,
            'spend_cap': num('spend_cap', 0, 0, 1000), 'free_tier': _flag(o.get('free_tier', False)),
            'daily_cap': num('daily_cap', 20, 0, 1000), 'timeout_s': num('timeout_s', 90, 5, 300),
            'log_payloads': str(o.get('log_payloads', False)).lower() in ('true', '1', 'yes'),
            'shadow': sh if isinstance(sh, bool) else str(sh).lower() not in ('false', '0', 'no'),
            'max_chars': num('max_chars', 1900, 100, 4000),
            'max_week_percent': num('max_week_percent', 50, 0, 100), 'usage_url': str(o.get('usage_url') or '').strip()}


class Agent:
    def __init__(self, opts, data='/data', log=None, opener=None, clock=time.time, today=lambda: time.strftime('%Y-%m-%d'), runner=None):
        self.opts, self.data = opts, data
        self.log = log or (lambda m: None)
        self.opener, self.clock, self.today = opener or urllib.request.urlopen, clock, today
        self.runner = runner or subprocess.run
        self.lock = threading.Lock()
        self.run_lock = threading.Lock()          # one CLI call at a time
        self.started = clock()
        self.served = self.live_calls = self.refused = 0
        self.last_week_percent = None
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
        """A CLI call on the plan is free only when the owner set free_tier. Unknown = paid."""
        return bool(self.opts.get('free_tier'))

    def spend_ok(self):
        """Call under self.lock. Free calls always pass. Paid calls need spend_cap > 0 and fewer paid calls today than spend_cap; passing counts the call."""
        if self.is_free():
            return True
        if self.paid_today() >= self.opts.get('spend_cap', 0):
            return False
        self._write('paid.json', {self.today(): self.paid_today() + 1})
        return True

    def week_percent(self):
        """-> number from usage_url JSON {week_percent}, or None (no url, unreachable, bad JSON, not a number). Never raises."""
        url = self.opts.get('usage_url')
        if not url:
            return None
        try:
            with self.opener(urllib.request.Request(url, method='GET'), timeout=5) as x:
                v = json.loads(x.read(100000)).get('week_percent')
        except Exception:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    def log_line(self, name, obj, cap, text, ok, answer, kw, t0):
        """ONE structured line per /ask: metadata only. Request/answer snippets (200 chars) only when option log_payloads is true."""
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
            self.log(self.log_line('ih_ai_layer_claude_cli', obj, cap, text, ok, answer, kw, t0))
            return dict({'ok': ok, 'answer': answer, 'request_id': rid, 'agent': 'ih_ai_layer_claude_cli', 'free': self.is_free(), 'model': self.opts.get('model')}, **kw)
        self.served += 1
        if cap not in CAPABILITIES or not text:
            return res(False, 'This agent only does general_qa and summarize.', why='bad_request')
        if obj.get('model') and obj.get('model') != self.opts['model']:
            return res(False, 'This agent does not serve that model.', why='model_not_available', error='model_not_available')
        if self.stopped():
            return res(False, 'Claude CLI agent is stopped (kill switch).', why='stopped')
        if self.opts['shadow']:
            return res(True, "[dry] shadow mode: would run the Claude CLI for '%s'. Nothing was run." % cap, why='shadow', mode='dry')
        if not self.run_lock.acquire(blocking=False):
            self.refused += 1
            return res(False, 'Claude CLI agent is busy with another call.', why='busy')
        try:
            with self.lock:
                if self.used_today() >= self.opts['daily_cap']:
                    self.refused += 1
                    return res(False, 'Claude CLI daily limit reached (%d per day). Try tomorrow.' % self.opts['daily_cap'], why='cap')
                if not self.spend_ok():
                    self.refused += 1
                    return res(False, 'Paid calls are not allowed (spend_cap %d paid calls per day, 0 = free only).' % self.opts.get('spend_cap', 0), why='spend_cap', error='spend_cap')
                pct = self.week_percent()
                self.last_week_percent = pct
                if pct is None:
                    self.refused += 1
                    return res(False, 'Weekly usage is unknown, so the Claude CLI is not used.', why='usage_unknown')
                if pct > self.opts['max_week_percent']:
                    self.refused += 1
                    return res(False, 'Weekly usage is above the limit (%d%%), so the Claude CLI is not used.' % self.opts['max_week_percent'], why='usage_gate')
                self._count()
            try:
                answer = self.generate(cap, scrub(text[:MAX_IN]))
            except subprocess.TimeoutExpired:
                self.log('claude cli failed: timeout')
                return res(False, 'Claude CLI timed out.', why='timeout')
            except Exception as e:
                self.log('claude cli failed: %s' % type(e).__name__)      # type only: never the message
                return res(False, 'Claude CLI did not answer.', why='upstream_error')
            self.live_calls += 1
            if not answer:
                return res(False, 'Claude CLI returned no text.', why='empty')
            return res(True, answer[:self.opts['max_chars']], cached=False)
        finally:
            self.run_lock.release()

    def command(self, cap):
        cmd = [CLI, '-p'] + TOOL_FLAGS + ['--system-prompt', SYSTEM[cap]]
        if self.opts.get('model'):
            cmd += ['--model', self.opts['model']]
        return cmd

    def child_env(self):
        """Minimal environment: nothing from this process (so no options, tokens or keys). The login is found through HOME."""
        return {'PATH': os.environ.get('PATH') or '/usr/local/bin:/usr/bin:/bin', 'HOME': os.path.join(self.data, 'claude-home'), 'LANG': 'C.UTF-8'}

    def generate(self, cap, text):
        """The ONLY outside call. Prompt on stdin, empty temp cwd, minimal env, hard timeout. -> reply text (may be empty)."""
        tmp = tempfile.mkdtemp(prefix='clicall-')
        try:
            r = self.runner(self.command(cap), input=text, capture_output=True, text=True, cwd=tmp, env=self.child_env(),
                            timeout=self.opts['timeout_s'], check=False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if r.returncode != 0:
            raise RuntimeError('cli exit %s' % r.returncode)
        out = r.stdout if isinstance(r.stdout, str) else ''
        return out[:MAX_RAW].strip()

    def status(self):
        now = self.clock()
        return {'name': 'ih_ai_layer_claude_cli', 'version': VERSION, 'capabilities': CAPABILITIES, 'configured': os.path.isdir(os.path.join(self.data, 'claude-home', '.claude')),
                'model': self.opts['model'], 'shadow': self.opts['shadow'], 'kill_switch': self.stopped(), 'uptime_s': int(now - self.started),
                'used_today': self.used_today(), 'daily_cap': self.opts['daily_cap'], 'spend_cap': self.opts.get('spend_cap', 0), 'free': self.is_free(), 'paid_today': self.paid_today(), 'served': self.served, 'live_calls': self.live_calls,
                'refused_by_cap': self.refused, 'max_week_percent': self.opts['max_week_percent'], 'usage_url_set': bool(self.opts.get('usage_url')), 'last_week_percent': self.last_week_percent,
                'parent': {'link_enabled': bool(self.opts['register_key']), 'registered_s_ago': None if self.registered_at is None else int(now - self.registered_at),
                           'last_error': self.register_error}}

    def register(self, opener=None, now=None):
        """Signed POST to the parent /register. Never raises. -> bool."""
        o = self.opts
        if not o['register_key']:
            self.register_error = 'no register_key'
            return False
        try:
            body, headers = jobs_link.pack(o['register_key'], {'name': 'ih_ai_layer_claude_cli', 'version': VERSION, 'capabilities': CAPABILITIES,
                                                               'models': [o['model']], 'url': o['callback_url'] or 'http://172.30.32.1:8814', 'healthy': True}, now=now)
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
