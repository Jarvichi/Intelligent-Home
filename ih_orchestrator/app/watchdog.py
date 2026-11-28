"""Add-on watchdog inside the orchestrator (v0.16.0). Watches ONLY add-ons of the Intelligent Home catalogue (never other installed apps).
The per-add-on result is built from healthlib checks (supervisor_addon state + HTTP on the ingress port), so it can feed the Health page.
Restarts go through the manager's restart action (one code path). Everything outside (Supervisor, tasks, Discord, clock) is injected so tests use fakes.
Why: the HA Codex terminal hung on 22 Nov 2026 and nothing restarted it; Andrew asked (task #236) for ONE place for add-on health, not a second add-on."""
import json, re, sqlite3, struct, threading, time, urllib.request, wave, os
import healthlib

REDACT = [
    (re.compile(r'(?i)(authorization|bearer)[:= ]+\S+'), r'\1 [secret]'),
    (re.compile(r'(?i)\b([\w.-]*(?:token|password|passwd|secret|api[_-]?key|apikey|key)[\w.-]*)\s*[=:]\s*\S+'), r'\1=[secret]'),
    (re.compile(r'\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b'), '[mac]'),
    (re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}\b'), '[addr]'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '[email]'),
    (re.compile(r'\b[A-Za-z0-9_\-]{32,}\b'), '[secret]'),
    (re.compile(r'\x1b\[[0-9;]*m'), ''),
]
LOG_LINES = 80
ANY_HTTP = tuple(range(100, 600))   # any HTTP answer (401, 404 too) means the app is alive; only no answer fails
DEFAULTS = {'watchdog_enabled': True, 'watchdog_mode': 'dry', 'watchdog_interval_seconds': 60, 'watchdog_failed_probes': 3,
            'watchdog_cooldown_minutes': 10, 'watchdog_max_restarts_per_hour': 3, 'watchdog_never_restart': [], 'watchdog_http_timeout': 5,
            'tasks_url': '', 'tasks_token': '', 'discord_bot_token': '', 'discord_channel_id': '',
            'media_probe_enabled': False, 'media_players': [], 'media_probe_clip': ''}
MIGRATIONS = [
    (1, 'CREATE TABLE incidents(id INTEGER PRIMARY KEY, at REAL NOT NULL, slug TEXT NOT NULL, name TEXT, probe TEXT, action TEXT, dry INTEGER, task INTEGER, excerpt TEXT)'),
    (2, 'CREATE TABLE restarts(id INTEGER PRIMARY KEY, at REAL NOT NULL, slug TEXT NOT NULL)'),
    (3, 'CREATE INDEX restarts_slug ON restarts(slug, at)'),
    (4, 'CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT NOT NULL)'),
]


def redact(text):
    for rx, rep in REDACT:
        text = rx.sub(rep, text)
    return text


def excerpt(raw, lines=LOG_LINES):
    return redact('\n'.join((raw or '').splitlines()[-lines:]))


def listed(slug, entries):
    """True when slug equals an entry or ends with '_<entry>' (repo prefix like 32c8114c_ is ignored)."""
    return any(slug == e or slug.endswith('_' + e) for e in entries)


IH_SHORT = re.compile(r'^ih_[a-z0-9_]{1,40}$')


def ih_targets(catalogue, installed):
    """[(short, full slug, name)] of INSTALLED add-ons that are in the Intelligent Home catalogue. Anything else (Codex terminal, Mosquitto, the orchestrator itself) is skipped.
    catalogue: [{slug, name}]; installed: {short: {slug: full}} (the manager's installed_map)."""
    return [(c['slug'], installed[c['slug']]['slug'], c.get('name') or c['slug']) for c in catalogue
            if IH_SHORT.match(c.get('slug') or '') and c['slug'] != 'ih_orchestrator' and c['slug'] in installed]


class DB:
    """Own sqlite file with numbered migrations (user_version). Thread-safe through one lock."""
    def __init__(self, path):
        self.lock = threading.Lock()
        self.c = sqlite3.connect(path, check_same_thread=False)
        v = self.c.execute('PRAGMA user_version').fetchone()[0]
        for n, sql in MIGRATIONS:
            if n > v:
                self.c.execute(sql)
                self.c.execute('PRAGMA user_version=%d' % n)
        self.c.commit()

    def execute(self, sql, args=()):
        with self.lock:
            self.c.execute(sql, args)
            self.c.commit()

    def query(self, sql, args=()):
        with self.lock:
            return self.c.execute(sql, args).fetchall()

    def load(self, k, default):
        r = self.query('SELECT v FROM kv WHERE k=?', (k,))
        try:
            return json.loads(r[0][0]) if r else default
        except ValueError:
            return default

    def save(self, k, v):
        self.execute('INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)', (k, json.dumps(v)))


class Watchdog:
    def __init__(self, db, sup, targets, restart, logs, tasks, notify, media, get_opts, clock=time.time, http=healthlib.check_http):
        """sup(method, path) -> dict. targets() -> [(short, full slug, name)] of installed IH catalogue add-ons (never the orchestrator).
        restart(short) -> (code, obj) = the manager's restart action. logs(short) -> text."""
        self.db, self.sup, self.targets, self.restart, self.logs = db, sup, targets, restart, logs
        self.tasks, self.notify, self.media, self.opts, self.clock, self.http = tasks, notify, media, get_opts, clock, http
        self.health = {}          # short -> row shown on the Health page

    # ---- state ----
    def st(self, short):
        return self.db.load('addon.' + short, {'fails': 0, 'seen_started': False, 'last_action': 0, 'last_comment': 0, 'task': None, 'enabled': True})

    def save(self, short, s):
        self.db.save('addon.' + short, s)

    def mode(self):
        o = self.opts()
        ov = self.db.load('settings', {}).get('mode')
        return ov if ov in ('dry', 'live') else o['watchdog_mode']      # the page switch wins over the option

    def set_mode(self, mode):
        if mode not in ('dry', 'live'):
            raise ValueError('mode')
        self.db.save('settings', dict(self.db.load('settings', {}), mode=mode))

    def set_enabled(self, short, enabled):
        if short not in self.health:
            raise ValueError('not an IH add-on')
        s = self.st(short)
        s['enabled'] = bool(enabled)
        self.save(short, s)
        self.health[short]['enabled'] = s['enabled']

    def restarts_last_hour(self, slug):
        return self.db.query('SELECT COUNT(*) FROM restarts WHERE slug=? AND at>?', (slug, self.clock() - 3600))[0][0]

    def restarts_today(self, slug):
        t = time.localtime(self.clock())
        start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        return self.db.query('SELECT COUNT(*) FROM restarts WHERE slug=? AND at>=?', (slug, start))[0][0]

    # ---- one probe: healthlib checks ----
    def probe(self, full, info):
        """-> healthlib-style result {state: OK|FAIL|UNKNOWN, checks: [...]}."""
        results = [healthlib.check_supervisor_addon(full, 'started', fetch=lambda _s: info)]
        port, ip = info.get('ingress_port'), info.get('ip_address')
        if info.get('state') == 'started' and info.get('ingress') and port and ip:
            r = self.http('http://%s:%s/' % (ip, port), self.opts()['watchdog_http_timeout'], ANY_HTTP)
            if r['ok'] is False:
                r = dict(r, detail='started but no HTTP answer on its ingress port (%s)' % r['detail'])
            results.append(r)
        return {'state': healthlib.combine(results), 'checks': results}

    # ---- one cycle ----
    def cycle(self):
        opts = self.opts()
        rows = {}
        for short, full, name in self.targets():
            try:
                rows[short] = self.check(short, full, name, opts)
            except Exception as e:     # one broken add-on must not stop the loop
                rows[short] = {'slug': short, 'name': name, 'state': 'UNKNOWN', 'addon_state': 'error', 'probe': 'watchdog error: %s' % type(e).__name__,
                               'last_probe': self.clock(), 'restarts_today': 0, 'enabled': True, 'fails': 0}
        self.health = rows            # add-ons that are no longer IH catalogue entries or no longer installed drop out
        return rows

    def check(self, short, full, name, opts):
        info = self.sup('GET', '/addons/%s/info' % full)
        s = self.st(short)
        res = self.probe(full, info)
        astate = info.get('state')
        text = '; '.join(str(c.get('detail') or '') for c in res['checks'])
        state = res['state']
        # a stopped add-on counts only if we saw it running before and it starts on boot (a person stopping it from the UI looks the same: use the toggle)
        if astate != 'started' and not (s['seen_started'] and info.get('boot') == 'auto'):
            state, text = 'OK', 'stopped (not expected to run: %s)' % ('boot manual' if info.get('boot') != 'auto' else 'never seen running')
        if astate == 'started':
            s['seen_started'] = True
        if state == 'FAIL':
            s['fails'] += 1
        elif state == 'OK':
            s['fails'] = 0               # UNKNOWN (Supervisor unreadable) neither counts nor resets
        row = {'slug': short, 'name': name, 'state': state, 'addon_state': astate, 'probe': text, 'checks': res['checks'], 'last_probe': self.clock(),
               'enabled': s.get('enabled', True), 'restarts_today': self.restarts_today(full), 'fails': s['fails'], 'last_action': s.get('last_action_text', '')}
        if state == 'FAIL' and s['fails'] >= opts['watchdog_failed_probes'] and s.get('enabled', True):
            self.fail(short, full, name, text, s, opts, row)
        self.save(short, s)
        self.health[short] = row
        return row

    def fail(self, short, full, name, text, s, opts, row):
        now = self.clock()
        if now - s['last_action'] < opts['watchdog_cooldown_minutes'] * 60:
            row['action'] = 'cool-down'
            return
        live = self.mode() == 'live'
        if listed(full, opts['watchdog_never_restart']) or listed(short, opts['watchdog_never_restart']):
            action = 'not restarted (on the never-restart list): task only'
        elif self.restarts_last_hour(full) >= opts['watchdog_max_restarts_per_hour']:
            action = 'not restarted (limit of %d restarts per hour reached): escalated' % opts['watchdog_max_restarts_per_hour']
        elif not live:
            action = 'DRY: would restart'
        else:
            try:
                code, _obj = self.restart(short)          # the manager's own restart action (a background job)
                if code != 202:
                    raise RuntimeError('refused')
                self.db.execute('INSERT INTO restarts(at,slug) VALUES(?,?)', (now, full))
                action = 'restarted'
            except Exception as e:
                action = 'restart FAILED (%s)' % type(e).__name__
        s['last_action'] = now
        s['last_action_text'] = action
        try:
            logs = excerpt(self.logs(short))
        except Exception as e:
            logs = '(could not read the log: %s)' % type(e).__name__
        task = self.file_task(full, name, text, action, logs, s, live)
        self.db.execute('INSERT INTO incidents(at,slug,name,probe,action,dry,task,excerpt) VALUES(?,?,?,?,?,?,?,?)',
                        (now, full, name, text, action, 0 if live else 1, task, logs))
        row['action'] = action
        if action == 'restarted' or action.startswith('restart FAILED') or 'escalated' in action:
            self.notify('Add-on watchdog: %s - %s (%s)' % (name, action, text))
        elif action.startswith('not restarted'):
            self.notify('Add-on watchdog: %s is failing and is on the never-restart list (%s). Task filed.' % (name, text))

    def file_task(self, full, name, text, action, logs, s, live):
        tag = '' if live else '[DRY] '
        body = 'Add-on: %s (%s)\nSymptom: %s after %d failed probes in a row\nAction: %s\nMode: %s\n\nLast log lines (secrets and addresses removed):\n%s' % (
            name, full, text, s['fails'], action, 'live' if live else 'DRY (nothing restarted)', logs[-3000:])
        try:
            if s.get('task') and self.tasks.is_open(s['task']):
                if self.clock() - s['last_comment'] > 1800 or action == 'restarted':
                    self.tasks.comment(s['task'], 'Again: %s. Action: %s' % (text, action))
                    s['last_comment'] = self.clock()
                return s['task']
            n = self.tasks.add('%sAdd-on %s failed: %s' % (tag, name, text[:80]), body)
            s['task'], s['last_comment'] = n, self.clock()
            return n
        except Exception:
            return None

    # ---- media probe (optional module, off by default, live mode only, once per 30 min) ----
    def media_cycle(self):
        o = self.opts()
        if self.mode() != 'live' or not o['media_probe_enabled'] or not self.media:
            return []
        out = []
        for ent in o['media_players']:
            k = self.db.load('media.' + ent, {'last': 0})
            if self.clock() - k['last'] < 1800:
                continue
            k['last'] = self.clock()
            ok = self.media.play_probe(ent)
            res = 'ok' if ok else 'failed'
            if not ok:
                self.media.reload_entry(ent)
                ok2 = self.media.play_probe(ent)
                res = 'failed; reloaded the config entry; retry %s' % ('ok' if ok2 else 'failed')
                self.db.execute('INSERT INTO incidents(at,slug,name,probe,action,dry,task,excerpt) VALUES(?,?,?,?,?,?,?,?)',
                                (self.clock(), 'media.' + ent, ent, 'did not reach playing within 10 s', res, 0, None, ''))
                self.notify('Add-on watchdog: %s did not play the silent clip; %s.' % (ent, res))
                if not ok2:
                    k['task'] = self.tasks.add('Media player %s does not play after a reload' % ent, 'The silent-clip probe failed twice, even after reloading its config entry.')
            self.db.save('media.' + ent, k)
            out.append((ent, res))
        return out

    # ---- what the Health page shows ----
    def report(self):
        rows = sorted(self.health.values(), key=lambda r: (r['state'] != 'FAIL', r['name'].lower()))
        inc = [dict(zip(('id', 'at', 'slug', 'name', 'probe', 'action', 'dry', 'task', 'excerpt'), r))
               for r in self.db.query('SELECT id,at,slug,name,probe,action,dry,task,excerpt FROM incidents ORDER BY id DESC LIMIT 40')]
        o = self.opts()
        return {'enabled': bool(o['watchdog_enabled']), 'mode': self.mode(), 'failed_probes': o['watchdog_failed_probes'], 'rows': rows, 'incidents': inc}

    def prune(self):
        self.db.execute('DELETE FROM incidents WHERE at<?', (self.clock() - 30 * 86400,))
        self.db.execute('DELETE FROM restarts WHERE at<?', (self.clock() - 3 * 86400,))


# ---- real clients (stdlib only) ----
def _req(method, url, token=None, body=None, timeout=15, raw=False):
    h = {'Content-Type': 'application/json'}
    if token:
        h['Authorization'] = 'Bearer ' + token
    r = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        data = resp.read()
    return data.decode('utf-8', 'replace') if raw else (json.loads(data) if data else {})


class Tasks:
    """The IH Tasks add-on (token API)."""
    def __init__(self, get_opts):
        self.o = get_opts

    def _api(self, method, path, body=None):
        o = self.o()
        if not o['tasks_url'] or not o['tasks_token']:
            raise RuntimeError('tasks not configured')
        return _req(method, o['tasks_url'].rstrip('/') + path, o['tasks_token'], body)

    def add(self, title, desc):
        r = self._api('POST', '/tasks', {'title': title[:200], 'description': desc[:20000], 'priority': 'high', 'source': 'ih_orchestrator', 'allow_duplicate': True})
        t = r.get('task') or r
        return t.get('id') if isinstance(t, dict) else t

    def comment(self, n, text):
        self._api('POST', '/tasks/%d/comment' % n, {'text': text[:3800]})

    def is_open(self, n):
        t = self._api('GET', '/tasks/%d' % n)
        t = t.get('task') or t
        return t.get('stage') in ('todo', 'planning', 'approved', 'implementing', 'verifying')


def discord_notifier(get_opts):
    def notify(text):
        o = get_opts()
        if o['discord_bot_token'] and o['discord_channel_id']:
            try:
                r = urllib.request.Request('https://discord.com/api/v10/channels/%s/messages' % o['discord_channel_id'], method='POST',
                                           data=json.dumps({'content': text[:1900]}).encode(),
                                           headers={'Authorization': 'Bot ' + o['discord_bot_token'], 'Content-Type': 'application/json',
                                                    'User-Agent': 'DiscordBot (ih_orchestrator, 0.16)'})
                urllib.request.urlopen(r, timeout=15).read()
            except Exception:
                pass
    return notify


class Media:
    """Silent-clip probe through the Home Assistant API. NEEDS `homeassistant_api: true` in config.yaml, which this add-on does NOT have:
    the module stays off (media_probe_enabled false) until Andrew approves that permission separately."""
    CORE = 'http://supervisor/core/api'

    def __init__(self, get_opts, token=None, sleep=time.sleep):
        self.o, self.token, self.sleep = get_opts, token or os.environ.get('SUPERVISOR_TOKEN', ''), sleep

    def _c(self, method, path, body=None, raw=False):
        return _req(method, self.CORE + path, self.token, body, raw=raw)

    def ensure_clip(self):
        d = '/media/ih_watchdog'
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, 'silence.wav')
        if not os.path.exists(f):
            with wave.open(f, 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(struct.pack('<h', 0) * 16000)

    def play_probe(self, entity):
        self.ensure_clip()
        self._c('POST', '/services/media_player/play_media', {'entity_id': entity, 'media_content_id': self.o()['media_probe_clip'], 'media_content_type': 'music'})
        for _ in range(10):
            self.sleep(1)
            if self._c('GET', '/states/' + entity).get('state') == 'playing':
                return True
        return False

    def reload_entry(self, entity):
        eid = self._c('POST', '/template', {'template': "{{ config_entry_id('%s') }}" % entity}, raw=True).strip()
        if eid and eid != 'None':
            self._c('POST', '/config/config_entries/entry/%s/reload' % eid, {})
            self.sleep(5)


def loop(wd, get_opts, stop=lambda: False, sleep=time.sleep, log=print):
    last_prune = 0
    while not stop():
        o = get_opts()
        if o['watchdog_enabled']:
            try:
                wd.cycle()
                wd.media_cycle()
            except Exception as e:
                log('watchdog: %s' % type(e).__name__)
            if time.time() - last_prune > 86400:
                wd.prune(); last_prune = time.time()
        sleep(max(10, int(o['watchdog_interval_seconds'])))
