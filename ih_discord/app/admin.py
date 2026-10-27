"""Channel ADMINISTRATION for the Discord transport (S6; design: brain projects/ih-discord-split-design, section 8). Generic: no deployment details.
A bus module may ask the transport to create / move / re-topic / delete Discord channels. The module only names things by KEY. The transport decides:
  - rights come from the module row in the transport options (all OFF by default): can_create_channels, allowed_parent_categories, name_pattern,
    max_channels, max_creates_per_hour, permission_template, can_delete, category_ops. A module can never set raw permissions: it names one template
    and the transport applies it (private to the listed users and the bot; everyone else hidden).
  - only channels this module CREATED can be moved, re-topiced or deleted (/data/created_channels.json: key, id, owner, created, category, name).
  - quotas: live channels and creates per hour. Hitting a limit 3 times in an hour writes /data/ADMIN_LOCK_<module> (the admin ops stay off until the file is deleted).
  - kill switches: /data/STOP_ADMIN (all modules) and /data/STOP_ADMIN_<module>. They stop only the admin ops.
  - dry-run (module dry, shadow, transport dry or not in live_modules): nothing is created, the answer has code `dry` and a fake id.
  - /data/audit.jsonl (written by busside) holds time, module, op, channel KEY, discord id, code. Never text, topics or tokens.
  - reconcile at start (and every 6 h): created channels missing in Discord are marked dead (the owner gets one `channel_gone` event on the key `_admin`);
    channels in the managed categories that match a name pattern but are not in the state file are listed on /status as orphans (names and ids only)."""
import json, os, re, time, threading
import bus_client as B

DEFAULT_PATTERN = r'[a-z0-9-]{1,40}'
SUFFIX_RE = re.compile(r'^[a-z0-9-]{1,40}$')
ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')
CAT_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 _-]{0,59}$')
SEE = 1024 | 2048 | 65536 | 16384 | 32768 | 64   # view, send, read history, embed links, attach files, add reactions
MAX_TOPIC = 1000
HOUR = 3600
LOCK_HITS = 3
RECONCILE_EVERY = 6 * 3600
RECONCILE_RETRY = 120
CATEGORY_LIMIT = 50          # Discord: channels per category
SERVER_LIMIT = 500           # Discord: channels per server
MAX_CATEGORIES_PER_MODULE = 5


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


def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _split(raw):
    if isinstance(raw, str):
        return [x.strip() for x in re.split(r'[,\s]+', raw) if x.strip()]
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


class AdminCfg:
    """The admin rights of ONE module (from its row in the transport options). Everything off by default."""
    def __init__(self, can_create=False, cats=(), pattern=DEFAULT_PATTERN, max_channels=20, max_creates=5, template='', can_delete=False, category_ops=False):
        self.can_create, self.cats, self.pattern = bool(can_create), set(cats), pattern
        self.max_channels, self.max_creates, self.template = max_channels, max_creates, template
        self.can_delete, self.category_ops = bool(can_delete), bool(category_ops)
        try:
            self.rx = re.compile(pattern)
        except re.error:
            self.rx, self.can_create = None, False   # a bad pattern switches creating off (fail closed)

    @property
    def any(self):
        return self.can_create or self.can_delete or self.category_ops

    def public(self):
        return {'can_create_channels': self.can_create, 'can_delete': self.can_delete, 'category_ops': self.category_ops,
                'allowed_parent_categories': sorted(self.cats), 'max_channels': self.max_channels, 'max_creates_per_hour': self.max_creates,
                'permission_template': self.template}


def parse_cfg(m):
    """A `modules` option row -> AdminCfg. Every key is optional."""
    pat = m.get('name_pattern')
    return AdminCfg(can_create=m.get('can_create_channels') is True, cats=_split(m.get('allowed_parent_categories')),
                    pattern=pat.strip() if isinstance(pat, str) and pat.strip() else DEFAULT_PATTERN,
                    max_channels=_int(m.get('max_channels') or 20, 20, 1, 50), max_creates=_int(m.get('max_creates_per_hour') or 5, 5, 1, 30),
                    template=str(m.get('permission_template') or '').strip(), can_delete=m.get('can_delete') is True,
                    category_ops=m.get('category_ops') is True)


def parse_categories(raw):
    """'wip=123,done=456' (or a list of 'key=id' / {key, id}) -> {key: id}."""
    out = {}
    items = _split(raw) if isinstance(raw, str) else list(raw or [])
    for it in items:
        if isinstance(it, dict):
            k, v = str(it.get('key') or '').strip(), str(it.get('id') or '').strip()
        else:
            k, _, v = str(it).partition('=')
            k, v = k.strip(), v.strip()
        if B.KEY_RE.match(k) and ID_RE.match(v):
            out[k] = v
    return out


def parse_templates(raw):
    """'private_owner_and_bot=111+222,other=333' -> {name: [user ids]}. A template = a channel visible to these users and the bot only."""
    out = {}
    for it in _split(raw):
        name, _, ids = it.partition('=')
        name = name.strip()
        users = [u.strip() for u in ids.split('+') if u.strip()]
        if B.NAME_RE.match(name) and all(ID_RE.match(u) for u in users):
            out[name] = users
    return out


def slug(text):
    return re.sub(r'[^a-z0-9]+', '-', str(text).lower()).strip('-')[:40].strip('-')


class Admin:
    def __init__(self, transport, categories=None, templates=None):
        self.t = transport
        self.categories = dict(categories or {})   # category key -> category id (transport options)
        self.templates = dict(templates or {})     # template name -> [user ids]
        self.path = os.path.join(transport.data, 'created_channels.json')
        self.state_path = os.path.join(transport.data, 'admin_state.json')
        self.created = _load(self.path, {})        # key -> {id, owner, created, category, name, type, guild, dead?}
        self.created = {k: v for k, v in self.created.items() if isinstance(v, dict) and v.get('id') and v.get('owner')}
        st = _load(self.state_path, {})
        self.creates = {m: [float(x) for x in v] for m, v in (st.get('creates') or {}).items() if isinstance(v, list)}
        self.hits = {m: [float(x) for x in v] for m, v in (st.get('hits') or {}).items() if isinstance(v, list)}
        self.lock = threading.RLock()
        self.guilds = {}
        self.recon = {'at': None, 'ok': None, 'missing': [], 'orphans': []}
        self.recon_next = 0.0
        self.counts = {}

    # ---- state ----
    def attach(self):
        """At start: make every saved, living channel key known to the transport, router and its owner's grant."""
        for k, e in self.created.items():
            if not e.get('dead'):
                self._register(k, e)

    def _save(self):
        _save(self.path, self.created)
        _save(self.state_path, {'creates': self.creates, 'hits': self.hits})

    def _register(self, key, e):
        if e.get('type', 'channel') == 'category':
            return
        t = self.t
        t.channels[key] = e['id']
        if getattr(t, 'router', None) is not None:
            t.router.channels[key] = e['id']
        g = t.grants.get(e['owner'])
        if g:
            g.channels.add(key)

    def _unregister(self, key, e):
        t = self.t
        t.channels.pop(key, None)
        r = getattr(t, 'router', None)
        if r is not None:
            r.channels.pop(key, None); r.ids.pop(key, None)
        g = t.grants.get(e['owner'])
        if g:
            g.channels.discard(key)
        for k in [k for k, w in t.watches.items() if w.get('ck') == key]:
            t.watches.pop(k, None)
        for k in [k for k, o in t.own.items() if o.get('ck') == key]:
            t.own.pop(k, None)
        t._save_state()
        if getattr(t, 'journal', None) is not None:
            try: t.journal.drop_channel(key)
            except Exception: pass
            cur = getattr(r, 'cursors', None)
            if cur is not None:
                try: cur.drop(key)
                except Exception: pass

    def _count(self, module, what):
        self.counts[(module, what)] = self.counts.get((module, what), 0) + 1

    def owned(self, module, typ='channel', live_only=True):
        return {k: e for k, e in self.created.items() if e['owner'] == module and e.get('type', 'channel') == typ and not (live_only and e.get('dead'))}

    def stop_reason(self, module):
        d = self.t.data
        for f in ('STOP_ADMIN', 'STOP_ADMIN_' + module, 'ADMIN_LOCK_' + module):
            if os.path.exists(os.path.join(d, f)):
                return f
        return ''

    def cat_id(self, module, g, key):
        """Category KEY -> category id, only if the module may use it (granted key, or a category it created itself)."""
        if not isinstance(key, str):
            return None
        if key in g.admin.cats and key in self.categories:
            return self.categories[key]
        e = self.created.get(key)
        if e and e['owner'] == module and e.get('type') == 'category' and not e.get('dead'):
            return e['id']
        return None

    def _fail(self, code, detail=''):
        return self.t._fail(code, detail)

    def _limit(self, module, what, dry):
        """A quota was hit. Live: count it; the third hit in an hour locks the module's admin ops."""
        if not dry:
            now = self.t.clock()
            h = [x for x in self.hits.get(module, []) if now - x < HOUR] + [now]
            self.hits[module] = h
            if len(h) >= LOCK_HITS:
                try: open(os.path.join(self.t.data, 'ADMIN_LOCK_' + module), 'w').close()
                except OSError: pass
                self.t.log('admin: module %s hit its limit %d times: admin ops locked' % (module, len(h)))
            self._save()
        return self._fail('rate_limited', what)

    def _recent_creates(self, module):
        now = self.t.clock()
        r = [x for x in self.creates.get(module, []) if now - x < HOUR]
        self.creates[module] = r
        return r

    def _guild_of(self, channel_id):
        if channel_id in self.guilds:
            return self.guilds[channel_id]
        st, ch = self.t.discord.req('GET', '/channels/%s' % channel_id)
        gid = str((ch or {}).get('guild_id') or '') if st == 200 else ''
        if gid:
            self.guilds[channel_id] = gid
        return gid or None

    def _guild_channels(self, gid):
        st, chans = self.t.discord.req('GET', '/guilds/%s/channels' % gid)
        return chans if st == 200 and isinstance(chans, list) else None

    def _overwrites(self, gid, users):
        me = str((self.t.discord.me() or {}).get('id') or '')
        ow = [{'id': gid, 'type': 0, 'allow': '0', 'deny': '1024'}]
        for u in users:
            ow.append({'id': u, 'type': 1, 'allow': str(SEE), 'deny': '0'})
        if me:
            ow.append({'id': me, 'type': 1, 'allow': str(SEE), 'deny': '0'})
        return ow

    # ---- entry point (called by busside._execute for every admin op) ----
    def run(self, module, g, d):
        op, a = d.get('op'), g.admin
        granted = {'create_category': a.category_ops, 'create_channel': a.can_create, 'move_channel': a.can_create, 'set_topic': a.can_create,
                   'delete_channel': a.can_delete, 'list_channels': a.any}.get(op, False)
        if not granted:
            return self._fail('denied_op', 'admin op not granted')
        why = self.stop_reason(module)
        if why:
            return self._fail('denied_op', 'admin off: ' + why.lower())
        if not self.t._rate_ok(g):
            return self._fail('rate_limited', 'ops per minute')
        live, _ = self.t.is_live(module)
        if getattr(g, 'shadow', False):
            live = False
        dry = d.get('dry') is True or not live
        with self.lock:
            r = getattr(self, '_op_' + op)(module, g, d, dry)
        if r.get('ok') and r.get('code') == 'ok' and op in ('create_channel', 'create_category', 'delete_channel'):
            self._count(module, {'create_channel': 'created', 'create_category': 'created', 'delete_channel': 'deleted'}[op])
        return r

    def _dry(self, **extra):
        self.t.stats['dry_ops'] += 1
        self.t.seq += 1
        r = {'ok': True, 'code': 'dry', 'discord_id': 'dry%d' % self.t.seq}
        r.update(extra)
        return r

    # ---- ops ----
    def _op_create_category(self, module, g, d, dry):
        a, now = g.admin, self.t.clock()
        name = d.get('name')
        if not isinstance(name, str) or not CAT_NAME_RE.match(name.strip()):
            return self._fail('denied_op', 'category name')
        name = name.strip()
        key = module + '.' + (slug(name) or 'category')
        if len(key) > 60 or not B.KEY_RE.match(key):
            return self._fail('bad_request', 'key')
        have = self.created.get(key)
        if have and have.get('type') == 'category' and not have.get('dead'):
            return {'ok': True, 'code': 'ok', 'discord_id': have['id'], 'channel_key': key, 'detail': 'exists'}   # idempotent: ensure-a-category
        if key in self.created:
            return self._fail('bad_request', 'key exists')
        if len(self.owned(module, 'category')) >= MAX_CATEGORIES_PER_MODULE:
            return self._limit(module, 'max categories', dry)
        if len(self._recent_creates(module)) >= a.max_creates:
            return self._limit(module, 'creates per hour', dry)
        policy = d.get('parent_policy') or ('private' if a.template else 'inherit')
        users = None
        if policy == 'private':
            if not a.template or a.template not in self.templates:
                return self._fail('denied_op', 'template')
            users = self.templates[a.template]
        elif policy != 'inherit':
            return self._fail('bad_request', 'parent_policy')
        if dry:
            return self._dry(channel_key=key)
        ref = next(iter(self.categories.values()), None) or next((e['id'] for e in self.created.values() if e.get('type') == 'category'), None)
        gid = self._guild_of(ref) if ref else None
        if not gid:
            return self._fail('bad_request', 'no guild known: set one category in the options')
        body = {'name': name, 'type': 4}
        if users is not None:
            body['permission_overwrites'] = self._overwrites(gid, users)
        st, ch = self.t.discord.req('POST', '/guilds/%s/channels' % gid, body)
        if st >= 300 or not ch or not ch.get('id'):
            return self._fail('discord_error', 'HTTP %s' % st)
        self.created[key] = {'id': str(ch['id']), 'owner': module, 'created': int(now), 'category': '', 'name': str(ch.get('name') or name), 'type': 'category', 'guild': gid}
        self.creates.setdefault(module, []).append(now)
        self._save()
        return {'ok': True, 'code': 'ok', 'discord_id': str(ch['id']), 'channel_key': key}

    def _op_create_channel(self, module, g, d, dry):
        a, now = g.admin, self.t.clock()
        name = d.get('name')
        if not isinstance(name, str):
            return self._fail('bad_request', 'name')
        name = name.strip().lower()
        if len(name) > 100 or not name or not a.rx.fullmatch(name):
            return self._fail('denied_op', 'name pattern')
        suffix = d.get('key') if isinstance(d.get('key'), str) else name
        suffix = suffix.strip().lower()
        key = module + '.' + suffix
        if not SUFFIX_RE.match(suffix) or len(key) > 60 or not B.KEY_RE.match(key):
            return self._fail('bad_request', 'key')
        if key in self.created or key in self.t.channels:
            return self._fail('bad_request', 'key exists')
        cat_id = self.cat_id(module, g, d.get('category'))
        if cat_id is None:
            return self._fail('denied_op', 'category not granted')
        tpl = d.get('template') or a.template
        if not a.template or tpl != a.template:
            return self._fail('denied_op', 'template not granted')
        if a.template not in self.templates:
            return self._fail('denied_op', 'template unknown')
        topic = d.get('topic')
        if topic is not None and not isinstance(topic, str):
            return self._fail('bad_request', 'topic')
        if len(self.owned(module)) >= a.max_channels:
            return self._limit(module, 'max channels', dry)
        if len(self._recent_creates(module)) >= a.max_creates:
            return self._limit(module, 'creates per hour', dry)
        if dry:
            return self._dry(channel_key=key)
        gid = self._guild_of(cat_id)
        chans = self._guild_channels(gid) if gid else None
        if chans is None:
            return self._fail('discord_error', 'cannot read the server channels')
        if len(chans) >= SERVER_LIMIT:
            return self._fail('discord_error', 'server channel limit')
        if len([c for c in chans if str(c.get('parent_id')) == str(cat_id)]) >= CATEGORY_LIMIT:
            return self._fail('discord_error', 'category full')
        body = {'name': name, 'type': 0, 'parent_id': cat_id, 'permission_overwrites': self._overwrites(gid, self.templates[a.template])}
        if topic and topic.strip():
            body['topic'] = topic.strip()[:MAX_TOPIC]
        st, ch = self.t.discord.req('POST', '/guilds/%s/channels' % gid, body)
        if st >= 300 or not ch or not ch.get('id'):
            return self._fail('discord_error', 'HTTP %s' % st)
        e = {'id': str(ch['id']), 'owner': module, 'created': int(now), 'category': d.get('category'), 'name': str(ch.get('name') or name), 'type': 'channel', 'guild': gid}
        self.created[key] = e
        self.creates.setdefault(module, []).append(now)
        self._register(key, e)
        self._save()
        return {'ok': True, 'code': 'ok', 'discord_id': e['id'], 'channel_key': key}

    def _own_channel(self, module, d):
        key = d.get('channel_key')
        e = self.created.get(key) if isinstance(key, str) else None
        if not e or e['owner'] != module or e.get('type', 'channel') != 'channel':
            return key, None
        return key, e

    def _op_move_channel(self, module, g, d, dry):
        key, e = self._own_channel(module, d)
        if not e:
            return self._fail('not_owner')
        cat_id = self.cat_id(module, g, d.get('category'))
        if cat_id is None:
            return self._fail('denied_op', 'category not granted')
        if dry:
            return self._dry(channel_key=key)
        st, _ = self.t.discord.req('PATCH', '/channels/%s' % e['id'], {'parent_id': cat_id})
        if st >= 300:
            return self._fail('discord_error', 'HTTP %s' % st)
        e['category'] = d.get('category')
        self._save()
        return {'ok': True, 'code': 'ok', 'discord_id': e['id'], 'channel_key': key}

    def _op_set_topic(self, module, g, d, dry):
        key, e = self._own_channel(module, d)
        if not e:
            return self._fail('not_owner')
        topic = d.get('topic')
        if not isinstance(topic, str):
            return self._fail('bad_request', 'topic')
        if dry:
            return self._dry(channel_key=key)
        st, _ = self.t.discord.req('PATCH', '/channels/%s' % e['id'], {'topic': topic.strip()[:MAX_TOPIC]})
        if st >= 300:
            return self._fail('discord_error', 'HTTP %s' % st)
        return {'ok': True, 'code': 'ok', 'discord_id': e['id'], 'channel_key': key}

    def _op_delete_channel(self, module, g, d, dry):
        key = d.get('channel_key')
        e = self.created.get(key) if isinstance(key, str) else None
        if not e or e['owner'] != module:
            return self._fail('not_owner')   # never a channel made by hand or by another module
        is_cat = e.get('type') == 'category'
        if is_cat and not g.admin.category_ops:
            return self._fail('denied_op', 'category_ops')
        if dry:
            return self._dry(channel_key=key)
        if is_cat:
            chans = self._guild_channels(e.get('guild') or self._guild_of(e['id']))
            if chans is None:
                return self._fail('discord_error', 'cannot read the server channels')
            if any(str(c.get('parent_id')) == e['id'] for c in chans):
                return self._fail('denied_op', 'category not empty')
        st, _ = self.t.discord.req('DELETE', '/channels/%s' % e['id'])
        if st >= 300 and st != 404:
            return self._fail('discord_error', 'HTTP %s' % st)
        self.created.pop(key, None)
        self._unregister(key, e)
        self._save()
        return {'ok': True, 'code': 'ok', 'channel_key': key}

    def _op_list_channels(self, module, g, d, dry):
        cat = d.get('category')
        with_topic = d.get('with_topic') is True
        out = []
        for k, e in sorted(self.owned(module, 'channel', live_only=False).items()):
            if cat and e.get('category') != cat:
                continue
            row = {'channel_key': k, 'discord_id': e['id'], 'name': e.get('name', ''), 'category': e.get('category'), 'created': e.get('created'), 'dead': bool(e.get('dead'))}
            if with_topic and not e.get('dead') and len(out) < 25:
                st, ch = self.t.discord.req('GET', '/channels/%s' % e['id'])
                row['topic'] = str((ch or {}).get('topic') or '') if st == 200 else None
            out.append(row)
        return {'ok': True, 'code': 'ok', 'channels': out}

    # ---- reconcile ----
    def tick(self):
        """Called by the transport tick. Reconcile once at start and then every 6 h, only if some module has admin rights."""
        if not any(g.admin.any for g in self.t.grants.values()):
            return
        if self.t.clock() < self.recon_next:
            return
        with self.lock:
            ok = self.reconcile()
        self.recon_next = self.t.clock() + (RECONCILE_EVERY if ok else RECONCILE_RETRY)

    def reconcile(self):
        t, now = self.t, self.t.clock()
        cat_ids = {i for i in self.categories.values()} | {e['id'] for e in self.created.values() if e.get('type') == 'category' and not e.get('dead')}
        guilds = {e['guild'] for e in self.created.values() if e.get('guild')}
        for cid in list(cat_ids)[:3]:
            gid = self._guild_of(cid)
            if gid:
                guilds.add(gid)
        if not guilds:   # nothing tracked and no category known: nothing to compare
            self.recon.update(at=int(now), ok=True)
            return True
        live_ids, missing, orphans, ok = {}, [], [], True
        for gid in sorted(guilds):
            chans = self._guild_channels(gid)
            if chans is None:
                ok = False; continue
            for c in chans:
                live_ids[str(c.get('id'))] = c
        if not ok:
            self.recon.update(at=int(now), ok=False)
            return False
        for k, e in list(self.created.items()):
            if e.get('dead') or e.get('guild') not in guilds:
                continue
            if e['id'] not in live_ids:
                e['dead'] = True
                missing.append({'channel_key': k, 'discord_id': e['id']})
                self._unregister(k, e)
                if not e.get('gone_notified'):
                    e['gone_notified'] = True
                    pid = 'g:%s:%d' % (k, int(now))
                    t._send_in(e['owner'], '_admin', {'type': 'channel_gone', 'id': pid, 'channel_key': k}, pid)
        known = {e['id'] for e in self.created.values()}
        rxs = [g.admin.rx for g in t.grants.values() if g.admin.can_create and g.admin.rx is not None]
        for cid, c in live_ids.items():
            if cid in known or str(c.get('parent_id')) not in cat_ids or c.get('type') not in (0, None):
                continue
            if any(rx.fullmatch(str(c.get('name') or '')) for rx in rxs):
                orphans.append({'discord_id': cid, 'name': str(c.get('name') or '')[:100]})
        self.recon = {'at': int(now), 'ok': True, 'missing': missing, 'orphans': orphans[:50]}
        self._save()
        return True

    # ---- status ----
    def status(self):
        now = self.t.clock()
        mods = {}
        for n, g in self.t.grants.items():
            if not g.admin.any:
                continue
            mods[n] = dict(g.admin.public(), live_channels=len(self.owned(n)), creates_last_hour=len(self._recent_creates(n)),
                           stopped=self.stop_reason(n) or None, created_total=self.counts.get((n, 'created'), 0), deleted_total=self.counts.get((n, 'deleted'), 0))
        return {'stop_all': os.path.exists(os.path.join(self.t.data, 'STOP_ADMIN')), 'modules': mods, 'tracked': len(self.created),
                'dead': len([1 for e in self.created.values() if e.get('dead')]), 'reconcile': dict(self.recon),
                'reconcile_next_s': None if self.recon_next <= 0 else int(max(0, self.recon_next - now))}
