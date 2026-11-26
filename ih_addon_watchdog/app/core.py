"""Add-on watchdog logic. Everything that talks to the outside (Supervisor, HTTP probes, Tasks add-on, Discord, clock) is injected,
so the tests run with fakes. Why: the HA Codex terminal hung on 22 Nov 2026 and nothing restarted it (brain projects/ih-addon-watchdog)."""
import re, time

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
MIGRATIONS = [
    (1, 'CREATE TABLE incidents(id INTEGER PRIMARY KEY, at REAL NOT NULL, slug TEXT NOT NULL, name TEXT, probe TEXT, action TEXT, dry INTEGER, task INTEGER, excerpt TEXT)'),
    (2, 'CREATE TABLE restarts(id INTEGER PRIMARY KEY, at REAL NOT NULL, slug TEXT NOT NULL)'),
    (3, 'CREATE INDEX restarts_slug ON restarts(slug, at)'),
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


class Watchdog:
    def __init__(self, db, kv, sup, http_probe, tasks, notify, media, get_opts, clock=time.time):
        self.db, self.kv, self.sup, self.http_probe = db, kv, sup, http_probe
        self.tasks, self.notify, self.media = tasks, notify, media
        self.opts, self.clock = get_opts, clock
        self.health = {}          # slug -> row shown on the page

    # ---- state kept in kv ----
    def st(self, slug):
        return self.kv.load('addon.' + slug, {'fails': 0, 'seen_started': False, 'last_action': 0, 'last_comment': 0, 'task': None, 'enabled': True})

    def save(self, slug, s):
        self.kv.save('addon.' + slug, s)

    def restarts_last_hour(self, slug):
        r = self.db.query('SELECT COUNT(*) FROM restarts WHERE slug=? AND at>?', (slug, self.clock() - 3600))
        return r[0][0]

    def restarts_today(self, slug):
        t = time.localtime(self.clock())
        start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        return self.db.query('SELECT COUNT(*) FROM restarts WHERE slug=? AND at>=?', (slug, start))[0][0]

    # ---- one probe ----
    def probe(self, slug, info):
        """-> (ok, text). Started add-ons with an ingress port also get an HTTP request: any HTTP answer is alive;
        no answer (timeout, refused) is a failed probe."""
        state = info.get('state')
        if state != 'started':
            return False, 'state=%s' % state
        port, ip = info.get('ingress_port'), info.get('ip_address')
        if info.get('ingress') and port and ip:
            ok, why = self.http_probe('http://%s:%s/' % (ip, port))
            if not ok:
                return False, 'started but no HTTP answer on its ingress port (%s)' % why
            return True, 'started, HTTP ok (%s)' % why
        return True, 'started (no HTTP probe: add-on has no ingress port)'

    # ---- one cycle ----
    def cycle(self):
        opts = self.opts()
        out = []
        for a in self.sup.addons():
            slug = a['slug']
            try:
                out.append(self.check(slug, a.get('name') or slug, opts))
            except Exception as e:     # one broken add-on must not stop the loop
                self.health[slug] = {'slug': slug, 'name': a.get('name') or slug, 'state': 'error', 'probe': 'watchdog error: %s' % type(e).__name__,
                                     'last_probe': self.clock(), 'restarts_today': 0, 'enabled': True, 'fails': 0}
        return out

    def check(self, slug, name, opts):
        info = self.sup.info(slug)
        s = self.st(slug)
        ok, text = self.probe(slug, info)
        state = info.get('state')
        # a stopped add-on counts only if we saw it running before and it starts on boot (a person stopping it from the UI looks the same: use the toggle)
        if state != 'started' and not (s['seen_started'] and info.get('boot') == 'auto'):
            ok, text = True, 'stopped (not expected to run: %s)' % ('boot manual' if info.get('boot') != 'auto' else 'never seen running')
        if state == 'started':
            s['seen_started'] = True
        s['fails'] = 0 if ok else s['fails'] + 1
        row = {'slug': slug, 'name': name, 'state': state, 'probe': text, 'last_probe': self.clock(), 'enabled': s.get('enabled', True),
               'restarts_today': self.restarts_today(slug), 'fails': s['fails']}
        self.health[slug] = row
        if ok:
            if s.get('task') and s['fails'] == 0:
                pass            # keep the task link: a repeat becomes a comment on the same open task
            self.save(slug, s)
            return row
        if s['fails'] >= opts['failed_probes'] and s.get('enabled', True):
            self.fail(slug, name, info, text, s, opts, row)
        self.save(slug, s)
        return row

    def fail(self, slug, name, info, text, s, opts, row):
        now = self.clock()
        if now - s['last_action'] < opts['cooldown_minutes'] * 60:
            row['action'] = 'cool-down'
            return
        live = opts['mode'] == 'live'
        if listed(slug, opts['never_restart']):
            action = 'not restarted (on the never-restart list): task only'
        elif self.restarts_last_hour(slug) >= opts['max_restarts_per_hour']:
            action = 'not restarted (limit of %d restarts per hour reached): escalated' % opts['max_restarts_per_hour']
        elif not live:
            action = 'DRY: would restart'
        else:
            try:
                self.sup.restart(slug)
                self.db.execute('INSERT INTO restarts(at,slug) VALUES(?,?)', (now, slug))
                action = 'restarted'
            except Exception as e:
                action = 'restart FAILED (%s)' % type(e).__name__
        s['last_action'] = now
        try:
            logs = excerpt(self.sup.logs(slug))
        except Exception as e:
            logs = '(could not read the log: %s)' % type(e).__name__
        task = self.file_task(slug, name, text, action, logs, s, live)
        self.db.execute('INSERT INTO incidents(at,slug,name,probe,action,dry,task,excerpt) VALUES(?,?,?,?,?,?,?,?)',
                        (now, slug, name, text, action, 0 if live else 1, task, logs))
        row['action'] = action
        if action in ('restarted',) or action.startswith('restart FAILED') or 'escalated' in action:
            self.notify('Add-on watchdog: %s - %s (%s)' % (name, action, text))
        elif action.startswith('not restarted'):
            self.notify('Add-on watchdog: %s is failing and is on the never-restart list (%s). Task filed.' % (name, text))

    def file_task(self, slug, name, text, action, logs, s, live):
        tag = '' if live else '[DRY] '
        body = 'Add-on: %s (%s)\nSymptom: %s after %d failed probes in a row\nAction: %s\nMode: %s\n\nLast log lines (secrets and addresses removed):\n%s' % (
            name, slug, text, s['fails'], action, 'live' if live else 'DRY (nothing restarted)', logs[-3000:])
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

    # ---- media probe (live mode only, once per 30 min) ----
    def media_cycle(self):
        o = self.opts()
        if o['mode'] != 'live' or not o['media_probe_enabled']:
            return []
        out = []
        for ent in o['media_players']:
            k = self.kv.load('media.' + ent, {'last': 0})
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
                    n = self.tasks.add('Media player %s does not play after a reload' % ent, 'The silent-clip probe failed twice, even after reloading its config entry.')
                    k['task'] = n
            self.kv.save('media.' + ent, k)
            out.append((ent, res))
        return out
