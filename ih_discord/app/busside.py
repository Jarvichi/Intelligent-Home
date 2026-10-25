"""Transport side of the Discord bus (generic: no deployment details). See bus_client.py for the schema and signing.
What it does: publishes `in` events to modules (one signed publish per module), verifies and executes `out` ops against the GRANTS in the
add-on options, returns signed `res`, tracks which module owns which Discord message (edit/delete/watch only on own messages), emits
reaction events for watched messages to the owner only, tracks module heartbeats (`hello`), redelivers after a module restart, and posts
the optional "module is offline" line. The effective right of a module = what it REQUESTS in hello AND what the options GRANT.
Writes to Discord happen only when is_live(module) says so (dry-run, live_modules, kill switch files). A module never sees a channel number."""
import json, os, re, time, threading, collections
from urllib.parse import quote
import bus_client as B

DEFAULT_OPS = ('post', 'reply', 'react', 'unreact', 'edit', 'delete', 'typing', 'watch', 'unwatch')
MAX_TEXT = 1900
OFFLINE_S = 90
RESEND_S = 20
NOTICE_EVERY_S = 600
OWN_KEEP_S = 7 * 24 * 3600
RES_KEEP_S = 3600
NEEDS_CHANNEL = set(B.OPS)
ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')   # a user id (Discord ids are digits; letters are allowed so tests can use short fakes)
OWNER_OPS = ('edit', 'delete', 'get_message', 'watch', 'unwatch')


class Grant:
    def __init__(self, name, key, channels, ops, rate, public):
        self.name, self.key, self.channels, self.ops, self.rate, self.public = name, key, set(channels), set(ops), rate, set(public)


def parse_modules(raw, reserved=()):
    """Option list -> {name: Grant}. Invalid, duplicate or reserved entries are dropped. A module without a usable key is kept (state 'no key')."""
    out = {}
    for m in raw or []:
        if not isinstance(m, dict):
            continue
        name = str(m.get('name') or '').strip()
        if not B.NAME_RE.match(name) or name in out or name in reserved:
            continue
        ops = [str(x).strip() for x in (m.get('ops') or []) if str(x).strip() in B.OPS] or list(DEFAULT_OPS)
        try:
            rate = max(1, int(m.get('rate_per_min') or 30))
        except (TypeError, ValueError):
            rate = 30
        out[name] = Grant(name, str(m.get('signing_key') or '').strip(), [str(c).strip() for c in (m.get('channels') or []) if B.KEY_RE.match(str(c).strip())],
                          ops, rate, [str(w).strip().lower() for w in (m.get('public_words') or []) if str(w).strip()])
    return out


def _load(path, default):
    try:
        with open(path) as f:
            x = json.load(f)
        return x if isinstance(x, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(path, obj):
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except OSError:
        pass


class BusTransport:
    def __init__(self, bus, router, discord, grants, channels, data, journal=None, clock=time.time, log=print, is_live=None,
                 offline_notice=False, notice_live=None, max_age_s=600, threaded=False):
        self.bus, self.router, self.discord, self.grants, self.channels, self.data = bus, router, discord, grants, dict(channels), data
        self.journal, self.clock, self.log, self.max_age_s, self.threaded = journal, clock, log, max_age_s, threaded
        self.is_live = is_live or (lambda m: (False, 'no gate given'))
        self.offline_notice = offline_notice
        self.notice_live = notice_live or (lambda: False)
        self.lock = threading.RLock()
        self.guard = B.ReplayGuard(os.path.join(data, 'bus_replay.log'), clock=clock)
        st = _load(os.path.join(data, 'bus_state.json'), {})
        self.own = _load(os.path.join(data, 'ownership.json'), {})          # message id -> {module, tag, ck, t}
        self.results = collections.OrderedDict(sorted(_load(os.path.join(data, 'bus_ops.json'), {}).items(), key=lambda kv: kv[1].get('t', 0)))
        self.watches = _load(os.path.join(data, 'watches.json'), {})        # "ck|msg" -> {...}
        self.last_acked = dict(st.get('last_acked') or {})
        self.mods = {n: {'last_hb': None, 'version': '', 'boot_id': '', 'granted': [], 'denied': [], 'offline_notice': True} for n in grants}
        self.pending = collections.OrderedDict()      # (module, id) -> {body, ck, sent, attempt, t}
        self.rate = collections.defaultdict(collections.deque)
        self.rejected = collections.Counter()
        self.rejected_by = collections.defaultdict(collections.Counter)
        self.notice_at = {}
        self.stats = collections.Counter()
        self.seq = 0
        self.last_hb_pub = 0
        self.q, self.cv = collections.deque(), threading.Condition()
        self.started = False
        self.audit_path = os.path.join(data, 'audit.jsonl')

    # ---- start ----
    def start(self):
        for n, g in self.grants.items():
            if not B.good_key(g.key):
                continue
            self.bus.subscribe(B.topic_out(n), self._on_out)
            self.bus.subscribe(B.topic_ack(n), self._on_ack)
            self.bus.subscribe(B.topic_hello(n), self._on_hello)
        ok = self.bus.start()
        self.started = ok is not False
        if self.started and self.threaded:
            threading.Thread(target=self._worker, daemon=True, name='bus-ops').start()
        return self.started

    def _worker(self):
        while True:
            with self.cv:
                while not self.q:
                    self.cv.wait(1.0)
                f = self.q.popleft()
            try: f()
            except Exception as e: self.log('bus: op worker error (%s)' % type(e).__name__)

    def _later(self, fn):
        if self.threaded:
            with self.cv:
                self.q.append(fn); self.cv.notify()
        else:
            fn()

    # ---- helpers ----
    def alive(self, module):
        h = self.mods.get(module, {}).get('last_hb')
        return h is not None and self.clock() - h <= OFFLINE_S

    def _count_reject(self, module, code):
        self.rejected[code] += 1
        self.rejected_by[module][code] += 1
        self.log('bus: rejected a message from a module (%s)' % code)   # no module payload, no key

    def _pub(self, kind, module, topic, body):
        g = self.grants.get(module)
        if not g or not B.good_key(g.key):
            return False
        try:
            return self.bus.publish(topic, B.encode(g.key, kind, module, topic, body, self.clock))
        except Exception:
            self.log('bus: could not publish %s' % kind)
            return False

    def _audit(self, module, op, ck, discord_id, code):
        try:
            with open(self.audit_path, 'a') as f:   # no text, no topic text, no tokens
                f.write(json.dumps({'t': int(self.clock()), 'module': module, 'op': op, 'channel_key': ck, 'discord_id': discord_id, 'code': code}) + '\n')
        except OSError:
            pass

    def _save_state(self):
        _save(os.path.join(self.data, 'ownership.json'), self.own)
        _save(os.path.join(self.data, 'watches.json'), self.watches)

    def _save_state_small(self):
        _save(os.path.join(self.data, 'bus_state.json'), {'last_acked': self.last_acked})

    # ---- hello ----
    def _on_hello(self, topic, raw):
        pt = B.parse_topic(topic)
        if not pt:
            return
        module, g = pt[1], self.grants.get(pt[1])
        if not g or not B.good_key(g.key):
            self.rejected['no_key'] += 1; return
        code, d = B.open_message(g.key, 'hello', module, topic, raw, self.clock, self.guard)
        if code != 'ok':
            self._count_reject(module, code); return
        was_alive = self.alive(module)
        m = self.mods[module]
        m.update(last_hb=self.clock(), version=str(d.get('version', ''))[:30], boot_id=str(d.get('boot_id', ''))[:20],
                 offline_notice=d.get('offline_notice') is not False)
        granted, denied = [], []
        self.router.remove_module(module)
        for c in d.get('claims') or []:
            ck = str(c.get('channel_key', '')) if isinstance(c, dict) else ''
            if ck not in g.channels or ck not in self.channels:
                denied.append(ck[:60]); self.log('bus: claim not granted for module %s' % module); continue
            pub = [w for w in (c.get('public') or []) if isinstance(w, str) and w.strip().lower() in g.public]   # public only where the options allow it
            try:
                self.router.add_claim(module, ck, c.get('match') or [{'any': True}], pub, self._deliverer(module))
                granted.append(ck)
            except Exception as e:
                denied.append(ck[:60]); self.log('bus: claim refused for module %s (%s)' % (module, type(e).__name__))
        m['granted'], m['denied'] = granted, denied
        restart = d.get('restart') is True
        if restart or not was_alive:
            self._later(lambda: self.redeliver(module, restart))

    def _deliverer(self, module):
        def deliver(entry, raw, allowed):
            return self.deliver_message(module, entry, allowed)
        return deliver

    # ---- inbound events to modules ----
    def _send_in(self, module, ck, body, pid):
        body = dict(body); body.setdefault('attempt', 1)
        self.pending[(module, pid)] = {'body': body, 'ck': ck, 'sent': None, 'attempt': 1, 't': self.clock()}
        if self.alive(module):
            self._publish_in(module, pid)
            return True
        return False

    def _publish_in(self, module, pid):
        p = self.pending.get((module, pid))
        if not p:
            return
        p['sent'] = self.clock()
        self._pub('in', module, B.topic_in(module, p['ck']), dict(p['body'], attempt=p['attempt']))

    def deliver_message(self, module, entry, allowed):
        pid = 'm:' + str(entry['id'])
        if (module, pid) in self.pending:
            return True
        body = {'type': 'message', 'id': pid, 'msg_id': str(entry['id']), 'channel_key': entry['channel_key'], 'author_id': entry.get('author_id'),
                'author_name': entry.get('author_name', ''), 'allowed': bool(allowed), 'content': str(entry.get('content', ''))[:2000],
                'msg_ts': entry.get('msg_ts'), 'age_s': int(max(0, self.clock() - (entry.get('msg_ts') or self.clock()))),
                'reply_to': entry.get('reply_to'), 'attachments': entry.get('attachments') or {'count': 0, 'kinds': []}}
        sent = self._send_in(module, entry['channel_key'], body, pid)
        self.stats['in_sent' if sent else 'in_held'] += 1
        if not sent:
            self.maybe_offline_notice(module, entry['channel_key'], allowed)
        return True

    def redeliver(self, module, restart):
        """A module came back (or started). Send what it missed: held messages, and on restart the journal tail after its last ack."""
        if not self.alive(module):
            return
        for (m, pid), p in list(self.pending.items()):
            if m == module and (p['sent'] is None or restart):   # held while it was away, or sent to the old process and never acked
                if self.clock() - p['t'] <= self.max_age_s:
                    if p['sent'] is not None:
                        p['attempt'] += 1
                    self._publish_in(module, pid)
                else:
                    self.pending.pop((m, pid), None); self.stats['expired_held'] += 1
        if restart and self.journal is not None:
            after = self.last_acked.get(module, 0)
            for ck in self.mods[module]['granted']:
                for e in self.journal.since(ck, after, self.max_age_s, self.clock()):
                    if ('m:' + e['id']) in [pid for (mm, pid) in self.pending if mm == module]:
                        continue
                    if any(c.module == module for c in self.router.select(e)):
                        self.deliver_message(module, e, str(e.get('author_id')) in self.router.allowed())
                        self.stats['redelivered'] += 1

    def _on_ack(self, topic, raw):
        pt = B.parse_topic(topic)
        if not pt:
            return
        module, g = pt[1], self.grants.get(pt[1])
        if not g or not B.good_key(g.key):
            return
        code, d = B.open_message(g.key, 'ack', module, topic, raw, self.clock, self.guard)
        if code != 'ok':
            self._count_reject(module, code); return
        for pid in d.get('ids') or []:
            if self.pending.pop((module, str(pid)), None) is not None:
                self.stats['acked'] += 1
            if str(pid).startswith('m:'):
                try:
                    n = int(str(pid)[2:])
                    if n > int(self.last_acked.get(module, 0) or 0):
                        self.last_acked[module] = str(n); self._save_state_small()
                except ValueError:
                    pass

    # ---- outbound ops ----
    def _on_out(self, topic, raw):
        pt = B.parse_topic(topic)
        if not pt:
            return
        module, g = pt[1], self.grants.get(pt[1])
        if not g or not B.good_key(g.key):
            self.rejected['no_key'] += 1; return
        code, d = B.open_message(g.key, 'out', module, topic, raw, self.clock, None)   # replay is handled below, so a lost res can be asked for again
        if code == 'unsupported_version':
            oid = d.get('op_id') if isinstance(d, dict) else None
            self._pub('res', module, B.topic_res(module), {'op_id': oid if isinstance(oid, str) else '', 'ok': False, 'code': 'unsupported_version'})
            self._count_reject(module, code); return
        if code != 'ok':
            self._count_reject(module, code); return
        oid = d.get('op_id')
        if not isinstance(oid, str) or not oid or len(oid) > 100:
            self._count_reject(module, 'malformed'); return
        if oid in self.results:   # asked again: repeat the old answer, do nothing twice
            self._pub('res', module, B.topic_res(module), self.results[oid]['res']); self.stats['res_repeated'] += 1; return
        if not self.guard.add(B.replay_key('out', d)):
            self._count_reject(module, 'replay'); return
        self._later(lambda: self._run_op(module, g, d))

    def _rate_ok(self, g):
        q, now = self.rate[g.name], self.clock()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= g.rate:
            return False
        q.append(now)
        return True

    def _run_op(self, module, g, d):
        oid, op = d['op_id'], d.get('op')
        try:
            res = self._execute(module, g, d)
        except Exception as e:   # never leave the module without an answer; the detail is only the error type
            self.log('bus: op failed inside the transport (%s)' % type(e).__name__)
            res = self._fail('discord_error', 'internal error')
        res['op_id'] = oid
        self._audit(module, str(op)[:20], str(d.get('channel_key', ''))[:60], res.get('discord_id'), res.get('code'))
        self.results[oid] = {'t': self.clock(), 'res': res}
        self._trim_results()
        self._pub('res', module, B.topic_res(module), res)

    def _trim_results(self):
        now = self.clock()
        for k in [k for k, v in self.results.items() if now - v['t'] > RES_KEEP_S][:]:
            self.results.pop(k, None)
        while len(self.results) > 1000:
            self.results.popitem(last=False)
        _save(os.path.join(self.data, 'bus_ops.json'), dict(self.results))

    def _fail(self, code, detail=''):
        r = {'ok': False, 'code': code}
        if detail: r['detail'] = detail[:80]
        return r

    def _execute(self, module, g, d):
        op = d.get('op')
        if op not in B.OPS:
            return self._fail('bad_request', 'unknown op')
        if op not in g.ops:
            return self._fail('denied_op')
        ck = d.get('channel_key')
        if not isinstance(ck, str) or ck not in g.channels or ck not in self.channels:
            return self._fail('denied_channel')
        if not self._rate_ok(g):
            return self._fail('rate_limited')
        mid = str(d.get('msg_id', '')) if d.get('msg_id') is not None else ''
        if op in ('react', 'unreact', 'edit', 'delete', 'get_message', 'watch', 'unwatch') and not re.fullmatch(r'\d{1,25}|dry\d+', mid):
            return self._fail('bad_request', 'msg_id')
        if op in OWNER_OPS and not mid.startswith('dry'):
            o = self.own.get(mid)
            if not o or o['module'] != module:
                return self._fail('not_owner')
        live, why = self.is_live(module)
        is_write = op not in ('get_message', 'unwatch', 'expect')
        if d.get('dry') is True or (is_write and not live):
            self.stats['dry_ops'] += 1
            self.log('[DRY] bus op %s from %s (%s)' % (op, module, 'module is in dry-run' if d.get('dry') is True else why))
            self.seq += 1
            r = {'ok': True, 'code': 'dry'}
            if op in ('post', 'reply'):
                r['discord_id'] = 'dry%d' % self.seq
            return r
        cid = self.router.resolve(ck)
        if cid is None:
            return self._fail('discord_error', 'channel not found')
        return self._discord_op(module, d, op, ck, cid, mid)

    def _text(self, d):
        t = d.get('text')
        return t[:MAX_TEXT] if isinstance(t, str) and t.strip() else None

    def _discord_op(self, module, d, op, ck, cid, mid):
        rq = self.discord.req
        if op in ('post', 'reply'):
            text = self._text(d)
            if text is None:
                return self._fail('bad_request', 'text')
            body = {'content': text, 'allowed_mentions': {'parse': ['users']}}
            rt = d.get('reply_to')
            if op == 'reply' and not rt:
                return self._fail('bad_request', 'reply_to')
            if rt:
                body['message_reference'] = {'message_id': str(rt), 'fail_if_not_exists': False}
            st, msg = rq('POST', '/channels/%s/messages' % cid, body)
            if st >= 300 or not msg:
                return self._fail('discord_error', 'HTTP %s' % st)
            self.own[str(msg['id'])] = {'module': module, 'tag': str(d.get('tag') or '')[:40], 'ck': ck, 't': int(self.clock())}
            self._prune_own(); self._save_state()
            return {'ok': True, 'code': 'ok', 'discord_id': str(msg['id'])}
        if op in ('react', 'unreact'):
            em = d.get('emoji')
            if not isinstance(em, str) or not em or len(em) > 40:
                return self._fail('bad_request', 'emoji')
            who = str(d.get('user_id') or '@me')
            if who != '@me' and not ID_RE.match(who):
                return self._fail('bad_request', 'user_id')
            st, _ = rq('PUT' if op == 'react' else 'DELETE', '/channels/%s/messages/%s/reactions/%s/%s' % (cid, mid, quote(em, safe=''), '@me' if op == 'react' else who))
            return {'ok': True, 'code': 'ok'} if st < 300 else self._fail('discord_error', 'HTTP %s' % st)
        if op == 'edit':
            text = self._text(d)
            if text is None:
                return self._fail('bad_request', 'text')
            st, _ = rq('PATCH', '/channels/%s/messages/%s' % (cid, mid), {'content': text, 'allowed_mentions': {'parse': ['users']}})
            return {'ok': True, 'code': 'ok', 'discord_id': mid} if st < 300 else self._fail('discord_error', 'HTTP %s' % st)
        if op == 'delete':
            st, _ = rq('DELETE', '/channels/%s/messages/%s' % (cid, mid))
            if st < 300 or st == 404:
                self.own.pop(mid, None); self._save_state()
                return {'ok': True, 'code': 'ok'}
            return self._fail('discord_error', 'HTTP %s' % st)
        if op == 'delete_tagged':
            tag = str(d.get('tag') or '')
            if not tag:
                return self._fail('bad_request', 'tag')
            n = 0
            for m_id, o in list(self.own.items()):
                if o['module'] == module and o['ck'] == ck and o['tag'] == tag:
                    st, _ = rq('DELETE', '/channels/%s/messages/%s' % (cid, m_id))
                    if st < 300 or st == 404:
                        self.own.pop(m_id, None); n += 1
            self._save_state()
            return {'ok': True, 'code': 'ok', 'count': n}
        if op == 'get_message':
            st, msg = rq('GET', '/channels/%s/messages/%s' % (cid, mid))
            if st >= 300 or not msg:
                return self._fail('discord_error', 'HTTP %s' % st)
            return {'ok': True, 'code': 'ok', 'discord_id': mid, 'text': str(msg.get('content') or '')[:2000]}
        if op == 'typing':
            st, _ = rq('POST', '/channels/%s/typing' % cid)
            return {'ok': True, 'code': 'ok'} if st < 300 else self._fail('discord_error', 'HTTP %s' % st)
        if op == 'watch':
            ems = [e for e in (d.get('emojis') or []) if isinstance(e, str) and 0 < len(e) <= 40][:20]
            if not ems:
                return self._fail('bad_request', 'emojis')
            try:
                ttl = max(10, min(int(d.get('ttl_s') or 180), 3600))
            except (TypeError, ValueError):
                ttl = 180
            self.watches['%s|%s' % (ck, mid)] = {'module': module, 'ck': ck, 'msg': mid, 'emojis': ems, 'until': self.clock() + ttl,
                                                  'tag': str(d.get('tag') or self.own.get(mid, {}).get('tag', ''))[:40], 'seen': {}}
            self._save_state()
            return {'ok': True, 'code': 'ok'}
        if op == 'unwatch':
            self.watches.pop('%s|%s' % (ck, mid), None); self._save_state()
            return {'ok': True, 'code': 'ok'}
        if op == 'expect':
            uid = str(d.get('user_id') or '')
            if not ID_RE.match(uid):
                return self._fail('bad_request', 'user_id')
            self.router.expect(ck, uid, module, d.get('ttl_s') or 120)
            return {'ok': True, 'code': 'ok'}
        return self._fail('bad_request')

    def _prune_own(self):
        now = self.clock()
        for k in [k for k, v in self.own.items() if now - v.get('t', 0) > OWN_KEEP_S]:
            self.own.pop(k, None)

    # ---- offline notice ----
    def maybe_offline_notice(self, module, ck, allowed):
        if not (self.offline_notice and allowed and self.mods.get(module, {}).get('offline_notice', True)):
            return False
        now = self.clock()
        if now - self.notice_at.get(ck, -1e9) < NOTICE_EVERY_S:
            return False
        self.notice_at[ck] = now
        if not self.notice_live():
            self.log('[DRY] would post an offline notice for module %s' % module)
            self.stats['notice_dry'] += 1
            return False
        cid = self.router.resolve(ck)
        if cid:
            self.discord.req('POST', '/channels/%s/messages' % cid, {'content': ('`%s` is offline.' % module)[:200], 'allowed_mentions': {'parse': []}})
            self.stats['notices'] += 1
            return True
        return False

    # ---- periodic work ----
    def tick(self):
        now = self.clock()
        for (m, pid), p in list(self.pending.items()):
            if p['sent'] is not None and now - p['sent'] >= RESEND_S:
                if p['attempt'] == 1 and self.alive(m):
                    p['attempt'] = 2; self._publish_in(m, pid); self.stats['resent'] += 1
                elif p['attempt'] >= 2 or not self.alive(m):
                    self.pending.pop((m, pid), None); self.stats['gave_up'] += 1
                    self.log('bus: no ack from module %s: gave up on one message' % m)
            elif p['sent'] is None and now - p['t'] > self.max_age_s:
                self.pending.pop((m, pid), None); self.stats['expired_held'] += 1
        self.poll_watches()
        if now - self.last_hb_pub >= B.HEARTBEAT_S:
            self.last_hb_pub = now
            try: self.bus.publish('ih/discord/hb/transport', json.dumps({'v': 1, 'ts': int(now), 'state': 'up'}), retain=True)   # unsigned: a status light only
            except Exception: pass

    def poll_watches(self):
        now = self.clock()
        me = (self.discord.me() or {}).get('id')
        for k, w in list(self.watches.items()):
            if now > w['until'] or w['module'] not in self.grants:
                self.watches.pop(k, None); self._save_state(); continue
            cid = self.router.resolve(w['ck'])
            if cid is None:
                continue
            for em in w['emojis']:
                st, users = self.discord.req('GET', '/channels/%s/messages/%s/reactions/%s?limit=100' % (cid, w['msg'], quote(em, safe='')))
                if st == 404:
                    self.watches.pop(k, None); self._save_state(); break
                if st != 200:
                    continue
                cur = {str(u.get('id')) for u in users or [] if not u.get('bot') and str(u.get('id')) != str(me)}
                prev = set(w['seen'].get(em, []))
                for uid, added in [(u, True) for u in sorted(cur - prev)] + [(u, False) for u in sorted(prev - cur)]:
                    if added and uid not in self.router.allowed():
                        continue
                    self.seq += 1
                    pid = 'r:%s:%s:%s:%d' % (w['msg'], em, uid, self.seq)
                    self._send_in(w['module'], w['ck'], {'type': 'reaction', 'id': pid, 'msg_id': w['msg'], 'channel_key': w['ck'], 'emoji': em,
                                                         'user_id': uid, 'added': added, 'tag': w.get('tag', '')}, pid)
                    self.stats['reactions'] += 1
                w['seen'][em] = sorted(cur)

    # ---- status ----
    def status(self):
        now = self.clock()
        mods = {}
        for n, g in self.grants.items():
            m = self.mods[n]
            hb = m['last_hb']
            state = 'no key' if not B.good_key(g.key) else 'never' if hb is None else 'online' if now - hb <= OFFLINE_S else 'offline'
            live, why = self.is_live(n)
            mods[n] = {'state': state, 'last_heartbeat_s': None if hb is None else int(now - hb), 'version': m['version'],
                       'claims_granted': list(m['granted']), 'claims_denied': list(m['denied']), 'ops': sorted(g.ops),
                       'rejected': dict(self.rejected_by.get(n, {})), 'pending': len([1 for (mm, _) in self.pending if mm == n]),
                       'mode': 'live' if live else 'dry', 'why': why}
        return {'started': self.started, 'rejected': dict(self.rejected), 'modules': mods, 'stats': dict(self.stats),
                'owned_messages': len(self.own), 'watches': len(self.watches)}
