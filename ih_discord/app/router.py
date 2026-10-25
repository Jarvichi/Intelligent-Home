"""Message router of the Discord transport (generic: no deployment details).
ONE poller per channel key reads each Discord message once and hands it to CLAIMANTS:
  1. all modules with a SPECIFIC claim (exact words / prefix / regex) that matches get it;
  2. if none matched, the single CATCH-ALL ("any") claimant gets it. A second catch-all on one channel is refused (ClaimError);
  3. the transport allow-list decides who may command: other users reach a claimant only through its `public` words;
  4. typed answers (`expect`) go to the module that asked, ahead of the claim rules.
Claimants are objects with deliver(entry, raw, allowed): the in-process features (FeatureClaimant) and bus modules (busside.BusTransport).
Reactions are not routed here: only the module that owns a watched message receives them (busside).
Delivery order per message: journal write -> cursor moves -> route -> journal "done". A crash in between is repaired by recover().
Start: no cursor = start at the NEWEST message (nothing old is answered). Messages older than max_age_s are skipped (and journaled as skipped)."""
import re, time, threading, collections

EPOCH_MS = 1420070400000


def snowflake_ts(mid):
    return ((int(mid) >> 22) + EPOCH_MS) / 1000.0


class ClaimError(Exception):
    pass


def _norm(text):
    return str(text or '').strip().lower().rstrip('?!. ')


class Claim:
    """One module's claim on one channel key. match entries: {"any":true} | {"exact":[words]} | {"prefix":"x"} | {"regex":"..."}."""
    def __init__(self, module, channel_key, match, public, deliver, inprocess=False, shadow=False):
        self.module, self.channel_key, self.deliver, self.inprocess = module, channel_key, deliver, inprocess
        self.shadow = bool(shadow)   # a shadow claim gets COPIES of messages and never counts as the catch-all
        self.is_any, self.exact, self.prefixes, self.regexes = False, set(), [], []
        for m in match or [{'any': True}]:
            if not isinstance(m, dict):
                continue
            if m.get('any'):
                self.is_any = True
            if isinstance(m.get('exact'), list):
                self.exact |= {_norm(w) for w in m['exact'] if _norm(w)}
            if isinstance(m.get('prefix'), str) and m['prefix']:
                self.prefixes.append(m['prefix'].strip().lower())
            if isinstance(m.get('regex'), str) and m['regex']:
                try:
                    self.regexes.append(re.compile(m['regex'], re.I))
                except re.error:
                    raise ClaimError('bad regex in claim')
        self.public = {_norm(w) for w in (public or []) if _norm(w)}
        if not (self.is_any or self.exact or self.prefixes or self.regexes):
            self.is_any = True

    @property
    def specific(self):
        return bool(self.exact or self.prefixes or self.regexes)

    def matches(self, text):
        t = _norm(text)
        raw = str(text or '').strip()
        return t in self.exact or any(t.startswith(p) for p in self.prefixes) or any(r.fullmatch(raw) or r.fullmatch(t) for r in self.regexes)

    def is_public(self, text):
        return _norm(text) in self.public

    def kind(self):
        return 'any' if self.is_any and not self.specific else 'specific' if not self.is_any else 'any+specific'


class FeatureClaimant:
    """An in-process feature (shopping, house) as a claimant: delivery = put the raw message in the feature's inbox,
    which its own thread drains in tick(). So the feature still handles messages one at a time, as before."""
    def __init__(self, name, feature):
        self.name, self.feature = name, feature

    def deliver(self, entry, raw, allowed):
        self.feature.inbox.append(raw)
        return True


class Router:
    def __init__(self, channels, allowed, journal, cursors, discord, clock=time.time, max_age_s=600, log=print):
        self.channels = dict(channels)       # channel key -> channel name or number (from options; never exposed)
        self.allowed = allowed               # callable -> set of user ids
        self.journal, self.cursors, self.discord, self.clock, self.max_age_s, self.log = journal, cursors, discord, clock, max_age_s, log
        self.claims = collections.defaultdict(list)   # channel key -> [Claim]
        self.retry_at = {}
        self.ids = {}                        # channel key -> resolved channel id
        self.expects = {}                    # (channel key, user id) -> (module, expires)
        self.warnings = []
        self.stats = collections.Counter()
        self.last_poll = None
        self.lock = threading.RLock()

    # ---- claims ----
    def add_claim(self, module, channel_key, match, public, deliver, inprocess=False, shadow=False):
        c = Claim(module, channel_key, match, public, deliver, inprocess, shadow)
        with self.lock:
            others = [x for x in self.claims[channel_key] if x.module != module]
            if c.is_any and not c.shadow and any(x.is_any and not x.shadow for x in others):
                self.stats['claims_refused'] += 1
                raise ClaimError('channel %s already has a catch-all' % channel_key)
            for x in others:
                if c.specific and x.specific and not c.shadow and not x.shadow and (c.exact & x.exact or set(c.prefixes) & set(x.prefixes)):
                    w = 'overlapping specific claims on %s: %s and %s' % (channel_key, module, x.module)
                    if w not in self.warnings:
                        self.warnings.append(w); self.log('router: ' + w)
            self.claims[channel_key] = [x for x in self.claims[channel_key] if x.module != module or x.inprocess != inprocess] + [c]
        return c

    def remove_module(self, module):
        with self.lock:
            for k in list(self.claims):
                self.claims[k] = [x for x in self.claims[k] if x.module != module]

    def claim_summary(self):
        with self.lock:
            return {k: [{'module': c.module, 'kind': c.kind(), 'inprocess': c.inprocess} for c in v] for k, v in self.claims.items() if v}

    # ---- expectations (typed answers) ----
    def expect(self, channel_key, user_id, module, ttl_s):
        with self.lock:
            self.expects[(channel_key, str(user_id))] = (module, self.clock() + max(1, min(int(ttl_s), 3600)))

    # ---- routing ----
    def select(self, entry):
        """The claims a message goes to (no delivery). Used by route() and by redelivery to a returning module."""
        key, text, uid = entry['channel_key'], entry.get('content', ''), str(entry.get('author_id'))
        allowed = uid in self.allowed()
        with self.lock:
            cl = list(self.claims.get(key, []))
            cands = None
            ex = self.expects.get((key, uid))
            if ex and ex[1] < self.clock():
                self.expects.pop((key, uid), None); ex = None
            if ex:
                target = [c for c in cl if c.module == ex[0]][:1]
                if allowed and target:
                    self.expects.pop((key, uid), None)
                    cands = target
            if cands is None:
                cands = [c for c in cl if not c.shadow and c.specific and c.matches(text)]
                if not cands:
                    cands = [c for c in cl if not c.shadow and c.is_any][:1]
                cands += [c for c in cl if c.shadow and (c.is_any or c.matches(text))]   # shadow copies
        return [c for c in cands if allowed or c.is_public(text)]

    def route(self, entry, raw):
        """Deliver one journaled message. Returns the list of module names it went to."""
        allowed = str(entry.get('author_id')) in self.allowed()
        to, live_to = [], 0
        for c in self.select(entry):
            try:
                if c.deliver(entry, raw, allowed) is not False:
                    to.append(c.module)
                    live_to += 0 if c.shadow else 1
            except Exception as e:
                self.stats['deliver_errors'] += 1
                self.log('router: delivery to %s failed (%s)' % (c.module, type(e).__name__))
        if to:
            self.stats['delivered'] += len(to)
        if not live_to:
            self.stats['unclaimed'] += 1
        return to

    def entry_for(self, m, key):
        mid = str(m['id'])
        a = m.get('author') or {}
        atts = m.get('attachments') or []
        kinds = sorted({(x.get('content_type') or 'file').split('/')[0] for x in atts})
        ref = (m.get('message_reference') or {}).get('message_id')
        return {'id': mid, 'channel_key': key, 'author_id': str(a.get('id', '')), 'author_name': str(a.get('username', ''))[:60],
                'content': str(m.get('content') or '')[:2000], 'msg_ts': int(snowflake_ts(mid)), 'reply_to': ref,
                'attachments': {'count': len(atts), 'kinds': kinds}}

    def raw_for(self, entry):
        """A Discord-shaped message rebuilt from a journal entry (recovery after a crash)."""
        return {'id': entry['id'], 'content': entry.get('content', ''), 'author': {'id': entry.get('author_id'), 'username': entry.get('author_name'), 'bot': False},
                'attachments': [], 'message_reference': ({'message_id': entry['reply_to']} if entry.get('reply_to') else None)}

    def recover(self):
        """After a start: route messages that were journaled but not marked done (younger than max_age_s)."""
        n = 0
        for e in self.journal.pending(self.max_age_s, self.clock()):
            if e.get('channel_key') in self.channels:
                self.route(e, self.raw_for(e)); self.journal.mark_done(e['id']); n += 1
        if n:
            self.log('router: recovered %d message(s) from the journal' % n)
        self.stats['recovered'] += n
        return n

    # ---- polling ----
    def resolve(self, key):
        if key in self.ids:
            return self.ids[key]
        spec = self.channels.get(key)
        if not spec:
            return None
        if self.clock() < self.retry_at.get(key, 0):
            return None
        cid = self.discord.find_channel(spec)
        if not cid:
            self.retry_at[key] = self.clock() + 60   # a wrong name must not ask Discord every tick
        if cid:
            self.ids[key] = str(cid)
        return self.ids.get(key)

    def poll_once(self):
        self.last_poll = self.clock()
        n = 0
        with self.lock:
            keys = [k for k, v in self.claims.items() if v and k in self.channels]
        for k in keys:
            try:
                n += self.poll_channel(k)
            except Exception as e:
                self.stats['poll_errors'] += 1
                self.log('router: poll of a channel failed (%s)' % type(e).__name__)
        return n

    def poll_channel(self, key):
        cid = self.resolve(key)
        if cid is None:
            self.stats['unresolved'] += 1
            return 0
        last = self.cursors.get(key, cid)
        if last is None:   # first run (or a changed channel): start AT the newest message, answer nothing old
            st, msgs = self.discord.req('GET', '/channels/%s/messages?limit=1' % cid)
            if st != 200:
                if st in (403, 404): self.ids.pop(key, None)
                return 0
            self.cursors.set(key, cid, max([int(m['id']) for m in msgs or []] or [0]))
            self.log('router: channel %s starts at the newest message' % key)
            return 0
        handled = 0
        for _ in range(10):   # up to 500 messages per tick
            st, msgs = self.discord.req('GET', '/channels/%s/messages?limit=50&after=%s' % (cid, last))
            if st != 200:
                if st in (403, 404): self.ids.pop(key, None)
                return handled
            msgs = sorted(msgs or [], key=lambda x: int(x['id']))
            me = (self.discord.me() or {}).get('id')
            for m in msgs:
                a = m.get('author') or {}
                last = str(m['id'])
                if a.get('bot') or (me and a.get('id') == me):
                    self.cursors.set(key, cid, last)   # nothing to journal: just move on
                    continue
                e = self.entry_for(m, key)
                age = self.clock() - e['msg_ts']
                if age > self.max_age_s:
                    e['skipped'] = 'stale'; self.stats['skipped_stale'] += 1
                e['age_s'] = int(age)
                try:
                    self.journal.append(e)                 # 1. journal
                except OSError:
                    self.stats['journal_errors'] += 1
                    self.log('router: journal write failed: cursor NOT moved')
                    return handled
                self.cursors.set(key, cid, last)           # 2. cursor, only after the journal write
                if e.get('skipped'):
                    self.log('router: skipped an old message (%ds)' % e['age_s'])
                    continue
                self.route(e, m)                           # 3. route
                try: self.journal.mark_done(e['id'])
                except OSError: pass
                handled += 1
            if len(msgs) < 50:
                break
        return handled

    def status(self):
        return {'channels': self.claim_summary(), 'warnings': list(self.warnings), 'stats': dict(self.stats),
                'last_poll_s': None if self.last_poll is None else int(self.clock() - self.last_poll),
                'unresolved_channels': sorted(k for k in self.channels if k not in self.ids and self.claims.get(k)),
                'journal_lines': self.journal.count(), 'max_age_s': self.max_age_s}
