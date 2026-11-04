"""Metadata-only tracing for IH add-ons. stdlib only. Spans go to the Orchestrator's POST <ingest_url>/trace with the existing ingest token.
A span carries ONLY: random trace id, span id, parent span id, component, operation, status, short reason code, start time, duration,
plus an optional `attrs` dict: at most 8 entries, keys from the fixed ATTR_KEYS allowlist, values a small int or a short token (typed, not free-form).
No message text, no user/channel/entity IDs, no free-form fields exist in the API, so none can leak (sender is a person label such as andrew, never an ID).
Never raises into the caller, never blocks (bounded queue + one daemon sender thread), off when no url or token.
Usage:   T = trace.from_options('ih_jobs', opts)            # opts: ingest_url + ingest_token (or trace_url)
         with T.span('plugin_pass') as s:                   # new trace; nested spans pick up the parent by themselves
             with T.span('fetch'): ...
             s.skip('nothing_to_do')                        # or s.fail('timeout'); an exception marks error + exception class name"""
import contextvars, functools, json, queue, re, threading, time, urllib.request, uuid

TOKEN = re.compile(r'^[A-Za-z0-9_.-]{1,48}$')
STATUSES = ('ok', 'error', 'skipped', 'unknown')
MAX_BODY_SPANS = 40
MAX_MS = 3600000
ATTR_KEYS = ('sender', 'persona', 'kind', 'in_bytes', 'out_bytes', 'attachments', 'items', 'http_status', 'retries', 'chars')
MAX_ATTRS = 8
MAX_ATTR_INT = 1000000000
_current = contextvars.ContextVar('ih_trace_span', default=None)


def clean(value, default='unknown'):
    """Force a short code made of letters, digits and _ . - only (anything else becomes _)."""
    s = re.sub(r'[^A-Za-z0-9_.-]', '_', str(value if value is not None else ''))[:48]
    return s or default


def _attr_ok(k, v):
    if k not in ATTR_KEYS:
        return False
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return 0 <= v <= MAX_ATTR_INT
    return isinstance(v, str) and bool(TOKEN.fullmatch(v))


def clean_attrs(attrs):
    """Keep only allowlisted keys with an int 0..1e9 or a short token value; at most MAX_ATTRS. Never raises."""
    out = {}
    try:
        for k, v in (attrs.items() if isinstance(attrs, dict) else ()):
            if len(out) >= MAX_ATTRS:
                break
            if _attr_ok(k, v):
                out[k] = v
    except Exception:
        return {}
    return out


def new_trace_id():
    return uuid.uuid4().hex


def new_span_id():
    return uuid.uuid4().hex[:16]


def valid_span(d):
    """True if a span dict has exactly the allowed keys and values (same rules as the Orchestrator)."""
    keys = {'span_id', 'parent_span_id', 'component', 'operation', 'status', 'reason', 'started_at', 'duration_ms'}
    if not isinstance(d, dict) or not (set(d) == keys or set(d) == keys | {'attrs'}):
        return False
    if 'attrs' in d:
        a = d['attrs']
        if not isinstance(a, dict) or not a or len(a) > MAX_ATTRS or not all(_attr_ok(k, v) for k, v in a.items()):
            return False
    if not re.fullmatch(r'[a-f0-9]{16}', str(d['span_id'])):
        return False
    if d['parent_span_id'] is not None and not re.fullmatch(r'[a-f0-9]{16}', str(d['parent_span_id'])):
        return False
    if not TOKEN.fullmatch(str(d['component'])) or not TOKEN.fullmatch(str(d['operation'])) or d['status'] not in STATUSES:
        return False
    if d['reason'] and not TOKEN.fullmatch(str(d['reason'])):
        return False
    return isinstance(d['duration_ms'], int) and 0 <= d['duration_ms'] <= MAX_MS and isinstance(d['started_at'], float)


class Span:
    def __init__(self, tracer, operation, trace_id=None, parent=None):
        self.tracer, self.operation = tracer, clean(operation)
        cur = _current.get()
        if parent is None and trace_id is None and cur is not None:
            parent = cur
        self.trace_id = trace_id or (parent.trace_id if parent else new_trace_id())
        if not re.fullmatch(r'[a-f0-9]{32}', self.trace_id):
            self.trace_id = new_trace_id()
        self.parent_span_id = parent.span_id if isinstance(parent, Span) else (parent if isinstance(parent, str) and re.fullmatch(r'[a-f0-9]{16}', parent) else None)
        self.span_id = new_span_id()
        self.status, self.reason = 'ok', ''
        self.attrs = {}
        self.started = self.t0 = None
        self._token = None
        self._ended = False

    def start(self):
        self.started, self.t0 = self.tracer.clock(), self.tracer.mono()
        return self

    def child(self, operation):
        return Span(self.tracer, operation, parent=self)

    def fail(self, reason='failed'):
        self.status, self.reason = 'error', clean(reason, 'failed')

    def skip(self, reason='skipped'):
        self.status, self.reason = 'skipped', clean(reason, 'skipped')

    def set(self, **attrs):
        """Add typed metadata (allowlisted keys only; bad keys/values are dropped silently). Returns self."""
        try:
            merged = dict(self.attrs)
            merged.update(clean_attrs(attrs))
            self.attrs = clean_attrs(merged)
        except Exception:
            pass
        return self

    def end(self):
        if self._ended:
            return
        self._ended = True
        try:
            if self.started is None:
                self.start()
            ms = max(0, min(MAX_MS, int((self.tracer.mono() - self.t0) * 1000)))
            rec = {'span_id': self.span_id, 'parent_span_id': self.parent_span_id, 'component': self.tracer.component,
                   'operation': self.operation, 'status': self.status, 'reason': self.reason,
                   'started_at': float(self.started), 'duration_ms': ms}
            if self.attrs:
                rec['attrs'] = dict(self.attrs)
            self.tracer._emit(self.trace_id, rec)
        except Exception:
            pass

    def __enter__(self):
        self.start()
        self._token = _current.set(self)
        return self

    def __exit__(self, et, ev, tb):
        if et is not None and self.status == 'ok':
            self.status, self.reason = 'error', clean(et.__name__, 'exception')
        try:
            if self._token is not None:
                _current.reset(self._token)
        except Exception:
            pass
        self.end()
        return False   # never swallow the caller's exception


class Tracer:
    def __init__(self, component, url='', token='', opener=None, clock=time.time, mono=time.monotonic, max_queue=500, threaded=True):
        self.component = clean(component)
        self.url = str(url or '').strip().rstrip('/')
        self.token = str(token or '').strip()
        self.opener, self.clock, self.mono, self.threaded = opener, clock, mono, threaded
        self.q = queue.Queue(maxsize=max_queue)
        self.dropped = 0
        self._thread = None
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return bool(self.url and self.token and self.url.startswith(('http://', 'https://')))

    def span(self, operation, trace_id=None, parent=None):
        return Span(self, operation, trace_id, parent)

    def _emit(self, trace_id, rec):
        if not self.enabled or not valid_span(rec):
            return
        try:
            self.q.put_nowait((trace_id, rec))
        except queue.Full:
            self.dropped += 1
            return
        if self.threaded:
            with self._lock:
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._run, daemon=True)
                    self._thread.start()

    def _run(self):
        while True:
            try:
                first = self.q.get(timeout=30)
            except queue.Empty:
                return   # restarts on the next span
            time.sleep(0.2)   # let sibling spans join the same batch
            self._send([first] + self._drain())

    def _drain(self):
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                return out

    def flush(self):
        """Send everything queued now (used by tests and on shutdown)."""
        self._send(self._drain())

    def _send(self, items):
        by = {}
        for tid, rec in items:
            by.setdefault(tid, []).append(rec)
        for tid, spans in by.items():
            for i in range(0, len(spans), MAX_BODY_SPANS):
                try:
                    req = urllib.request.Request(self.url + '/trace', data=json.dumps({'trace_id': tid, 'spans': spans[i:i + MAX_BODY_SPANS]}).encode(), method='POST',
                                                 headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
                    with (self.opener or urllib.request.urlopen)(req, timeout=4) as r:
                        r.read()
                except Exception:
                    pass   # tracing must never disturb the add-on


def from_options(component, opts, **kw):
    """Build a Tracer from an add-on's options dict: ingest_url/trace_url + ingest_token. Disabled (no-op) if either is missing."""
    o = opts if isinstance(opts, dict) else {}
    return Tracer(component, o.get('ingest_url') or o.get('trace_url') or '', o.get('ingest_token') or '', **kw)


def traced(operation):
    """Method decorator: one span around the call, using self.tracer (no tracer or a disabled one = plain call). Exceptions still propagate."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *a, **k):
            tr = getattr(self, 'tracer', None)
            if tr is None or not tr.enabled:
                return fn(self, *a, **k)
            with tr.span(operation):
                return fn(self, *a, **k)
        return wrapper
    return deco
