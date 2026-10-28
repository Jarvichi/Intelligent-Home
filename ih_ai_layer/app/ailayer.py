"""ailayer: core logic of "Intelligent Home - AI Layer". stdlib only. NOTHING here knows about Discord or any chat: input is an AI request
(text + optional capability + optional context), output is an answer from a registered AI agent add-on.
Request text is never logged, stored or put in /status (only ids, capability, agent name, timings)."""
import collections, json, os, re, threading, time, urllib.request
import jobs_link

STOP_FILE = 'STOP'
AGENT_LIVE_S = 120          # an agent that has not re-registered for this long is not used
MAX_TEXT = 4000
ID_RE = re.compile(r'^[A-Za-z0-9._:-]{1,64}$')
NAME_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
CAP_RE = re.compile(r'^[a-z0-9_]{1,40}$')
MODEL_RE = re.compile(r'^[A-Za-z0-9._:/-]{1,60}$')

# capability -> keywords (lower case, substring match on the request text). First capability with most hits wins; no hit = no capability.
KEYWORDS = {
    'showtimes': ('showtime', 'showing', 'cinema', 'film', 'movie', 'odeon', 'screening', "what's on at", 'whats on at'),
    'web_search': ('search', 'look up', 'lookup', 'google', 'find out', 'latest', 'news', 'who is', 'what is', 'how much', 'when is'),
}


def classify(text):
    """-> capability name or None. Simple keyword rule; ties go to the earlier capability in KEYWORDS."""
    t = (text or '').lower()
    best, hits = None, 0
    for cap, words in KEYWORDS.items():
        n = sum(1 for w in words if w in t)
        if n > hits:
            best, hits = cap, n
    return best


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
    return {'api_token': str(o.get('api_token') or '').strip(), 'register_key': str(o.get('register_key') or '').strip(),
            'shadow': bool(o.get('shadow', True)), 'agent_timeout_s': num('agent_timeout_s', 20, 1, 60),
            'rate_per_min': num('rate_per_min', 30, 1, 600), 'max_tries': num('max_tries', 2, 1, 5)}


class Layer:
    def __init__(self, opts, data='/data', log=None, opener=None, clock=time.time):
        self.opts, self.data = opts, data
        self.log = log or (lambda m: None)
        self.opener = opener or urllib.request.urlopen
        self.clock = clock
        self.lock = threading.Lock()
        self.agents = {}                       # name -> dict(url, capabilities, version, healthy, seen)
        self.scores = self._load()             # cap -> agent -> dict(n, ok, ms, up, down)
        self.recent = collections.OrderedDict()  # request_id -> (cap, agent), for feedback
        self.window = collections.deque()
        self.started = clock()
        self.counts = collections.Counter()
        self.agent_why = {}                    # request_id -> why code of the last refusal by an agent (e.g. spend_cap), popped when reported

    # ---- persistence ----
    def _path(self):
        return os.path.join(self.data, 'scores.json')

    def _load(self):
        try:
            with open(self._path()) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            tmp = self._path() + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.scores, f)
            os.replace(tmp, self._path())
        except OSError:
            self.log('scores save failed')

    # ---- gates ----
    def stopped(self):
        return os.path.exists(os.path.join(self.data, STOP_FILE))

    def auth_ok(self, header):
        tok = self.opts.get('api_token')
        if not tok:
            return False
        import hmac
        return hmac.compare_digest(str(header or ''), 'Bearer ' + tok)

    def rate_ok(self):
        now = self.clock()
        with self.lock:
            while self.window and now - self.window[0] > 60:
                self.window.popleft()
            if len(self.window) >= self.opts['rate_per_min']:
                return False
            self.window.append(now)
            return True

    # ---- registration ----
    def register(self, body, signature):
        """Signed registration from an agent add-on. -> (code, dict)."""
        obj, why = jobs_link.unpack(self.opts.get('register_key', ''), body, signature, now=self.clock())
        if obj is None:
            self.counts['register_refused'] += 1
            return 401, {'ok': False, 'why': why}
        name, url, caps = obj.get('name'), str(obj.get('url') or ''), obj.get('capabilities')
        if not (isinstance(name, str) and NAME_RE.match(name) and re.match(r'^https?://[^\s]+$', url)
                and isinstance(caps, list) and caps and all(isinstance(c, str) and CAP_RE.match(c) for c in caps)):
            self.counts['register_refused'] += 1
            return 400, {'ok': False, 'why': 'bad registration'}
        models = obj.get('models', [])
        if not (isinstance(models, list) and len(models) <= 20 and all(isinstance(m, str) and MODEL_RE.match(m) for m in models)):
            self.counts['register_refused'] += 1
            return 400, {'ok': False, 'why': 'bad registration'}
        with self.lock:
            self.agents[name] = {'models': sorted(set(models)), 'url': url.rstrip('/'), 'capabilities': sorted(set(caps)), 'version': str(obj.get('version') or '')[:20],
                                 'healthy': bool(obj.get('healthy', True)), 'seen': self.clock()}
        return 200, {'ok': True}

    def live_agents(self, cap=None):
        now = self.clock()
        return [n for n, a in sorted(self.agents.items()) if now - a['seen'] <= AGENT_LIVE_S and a['healthy'] and (cap is None or cap in a['capabilities'])]

    # ---- scoring ----
    def score(self, cap, agent):
        """0..1, higher is better. Unknown agent = 0.55 (a little optimistic) so a new agent gets tried, but a proven good one (about 0.75) still beats it."""
        s = self.scores.get(cap, {}).get(agent)
        if not s or not s['n']:
            return 0.55
        success = s['ok'] / s['n']
        speed = 1.0 / (1.0 + (s['ms'] / s['n']) / 2000.0)
        fb = 0.5 if not (s['up'] + s['down']) else s['up'] / (s['up'] + s['down'])
        return round(0.5 * fb + 0.3 * success + 0.2 * speed, 4)

    def _record(self, cap, agent, ok, ms):
        with self.lock:
            s = self.scores.setdefault(cap, {}).setdefault(agent, {'n': 0, 'ok': 0, 'ms': 0, 'up': 0, 'down': 0})
            s['n'] += 1; s['ok'] += 1 if ok else 0; s['ms'] += int(ms)
            self._save()

    def feedback(self, obj):
        rid, sc = obj.get('request_id'), obj.get('score')
        if not isinstance(rid, str) or isinstance(sc, bool) or not isinstance(sc, (int, float)):
            return 400, {'ok': False, 'why': 'need request_id and numeric score'}
        with self.lock:
            hit = self.recent.get(rid)
            if not hit:
                return 404, {'ok': False, 'why': 'unknown or expired request_id'}
            cap, agent = hit
            s = self.scores.setdefault(cap, {}).setdefault(agent, {'n': 0, 'ok': 0, 'ms': 0, 'up': 0, 'down': 0})
            s['up' if sc > 0 else 'down'] += 1
            self._save()
        self.log('feedback id=%s cap=%s agent=%s' % (rid, cap, agent))
        return 200, {'ok': True, 'capability': cap, 'agent': agent}

    def ranked(self, cap):
        return sorted(self.live_agents(cap), key=lambda a: (-self.score(cap, a), a))

    # ---- the request ----
    def request(self, obj):
        """-> (http code, response dict). Never logs obj['text']."""
        t0 = self.clock()
        text = obj.get('text')
        rid = obj.get('request_id')
        if rid is None:
            rid = 'r%d' % int(t0 * 1000)
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or not isinstance(rid, str) or not ID_RE.match(rid):
            return 400, {'ok': False, 'error': 'need text (1-%d chars) and a simple request_id' % MAX_TEXT}
        ctx = obj.get('context')
        if ctx is not None and not isinstance(ctx, dict):
            return 400, {'ok': False, 'error': 'context must be an object'}
        model = obj.get('model')
        if model is not None and (not isinstance(model, str) or not MODEL_RE.match(model)):
            return 400, {'ok': False, 'error': 'bad model'}
        cap, classified = obj.get('capability'), False
        if cap is not None and (not isinstance(cap, str) or not CAP_RE.match(cap)):
            return 400, {'ok': False, 'error': 'bad capability'}
        if not cap and not model:
            cap, classified = classify(text), True
        self.counts['requests'] += 1

        def out(ok, answer, agent=None, extra=None):
            r = {'ok': ok, 'answer': answer, 'agent': agent, 'capability': cap, 'classified': classified,
                 'took_ms': int((self.clock() - t0) * 1000), 'request_id': rid}
            r.update(extra or {})
            self.log('request id=%s cap=%s agent=%s ok=%s ms=%d' % (rid, cap, agent, ok, r['took_ms']))
            return 200, r

        if model:
            # explicit model: no classifier, no scoring, only the agent that lists it, no fallback to other agents
            owners = [n for n in self.live_agents(cap) if model in self.agents[n].get('models', [])]
            if not owners:
                self.counts['model_not_available'] += 1
                self.log('request id=%s model_not_available' % rid)
                return 404, {'ok': False, 'error': 'model_not_available', 'answer': "No live agent serves model '%s'." % model,
                             'model': model, 'request_id': rid}
            if self.opts['shadow']:
                return out(True, "[dry] shadow mode: would ask agent '%s' for model '%s'. No agent was called." % (owners[0], model), owners[0], {'mode': 'dry', 'model': model})
            a0 = self.clock()
            ans = self._ask(owners[0], text, cap, ctx or {}, rid, model)
            ok = ans is not None
            if cap:
                self._record(cap, owners[0], ok, (self.clock() - a0) * 1000)
            if ok:
                with self.lock:
                    self.recent[rid] = (cap or 'model', owners[0])
                    while len(self.recent) > 500:
                        self.recent.popitem(last=False)
                return out(True, ans, owners[0], {'mode': 'live', 'model': model})
            self.counts['agents_failed'] += 1
            w = self.agent_why.pop(rid, None)
            return out(False, "The agent for model '%s' did not answer%s." % (model, ' (%s)' % w if w else ''), owners[0],
                       dict({'model': model}, **({'error': w} if w else {})))
        if cap is None:
            return out(False, 'No agent: I could not tell what kind of request this is. Give a capability.')
        order = self.ranked(cap)
        if not order:
            self.counts['no_agent'] += 1
            return out(False, "No agent is available for capability '%s'." % cap)
        if self.opts['shadow']:
            return out(True, "[dry] shadow mode: would ask agent '%s' for capability '%s'. No agent was called." % (order[0], cap), order[0], {'mode': 'dry'})
        tried = 0
        for name in order[:self.opts['max_tries']]:
            tried += 1
            a0 = self.clock()
            ans = self._ask(name, text, cap, ctx or {}, rid)
            ms = (self.clock() - a0) * 1000
            self._record(cap, name, ans is not None, ms)
            if ans is not None:
                with self.lock:
                    self.recent[rid] = (cap, name)
                    while len(self.recent) > 500:
                        self.recent.popitem(last=False)
                return out(True, ans, name, {'mode': 'live'})
        self.counts['agents_failed'] += 1
        w = self.agent_why.pop(rid, None)
        return out(False, "The agents for '%s' did not answer (%d tried)." % (cap, tried), None, {'error': w} if w else None)

    def _ask(self, name, text, cap, ctx, rid, model=None):
        """Signed POST to the agent's /ask. -> answer string or None. Errors are logged by type only."""
        a = self.agents[name]
        try:
            body, headers = jobs_link.pack(self.opts['register_key'], dict({'text': text, 'capability': cap, 'context': ctx, 'request_id': rid}, **({'model': model} if model else {})), now=self.clock())
            req = urllib.request.Request(a['url'] + '/ask', data=body, method='POST', headers=headers)
            with self.opener(req, timeout=self.opts['agent_timeout_s']) as x:
                raw = x.read(200000)
                sig = x.headers.get(jobs_link.HEADER)
            obj, why = jobs_link.unpack(self.opts['register_key'], raw, sig, now=self.clock())
            if obj is None or obj.get('request_id') != rid or not obj.get('ok') or not isinstance(obj.get('answer'), str):
                self.log('agent %s bad answer id=%s (%s)' % (name, rid, why if obj is None else 'not ok'))
                w = obj.get('why') if obj else None
                if isinstance(w, str) and re.match(r'^[a-z_]{1,30}$', w):
                    self.agent_why[rid] = w
                    while len(self.agent_why) > 100:
                        self.agent_why.pop(next(iter(self.agent_why)))
                return None
            return obj['answer']
        except Exception as e:
            self.log('agent %s failed id=%s: %s' % (name, rid, type(e).__name__))
            return None

    def status(self):
        now = self.clock()
        return {'name': 'ih_ai_layer', 'version': '0.2.0', 'mode': 'dry' if self.opts['shadow'] else 'live', 'kill_switch': self.stopped(),
                'uptime_s': int(now - self.started), 'counts': dict(self.counts), 'link_enabled': bool(self.opts.get('register_key')),
                'agents': self.agents_view(), 'scores': {c: {a: self.score(c, a) for a in d} for c, d in self.scores.items()}}

    def agents_view(self):
        now = self.clock()
        return [{'name': n, 'capabilities': a['capabilities'], 'models': a.get('models', []), 'version': a['version'], 'healthy': a['healthy'],
                 'seen_s_ago': int(now - a['seen']), 'live': now - a['seen'] <= AGENT_LIVE_S} for n, a in sorted(self.agents.items())]
