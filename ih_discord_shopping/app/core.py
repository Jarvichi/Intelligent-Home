"""Discord commands: thin core (Discord REST, HA REST, runner). One module per feature in features/.
stdlib only. Nothing deployment-specific lives here: see settings.py (channels, entities, folders come from settings).
Dry-run: reads are real, every write (Discord POST/PUT/DELETE/PATCH, HA service calls other than get_items) is only logged."""
import json, os, re, sys, time, threading, traceback, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings as ST
try:
    from ihlib.ha import HA as _IHA   # shared library: shipped as app/ihlib in the add-ons
except ImportError:   # stand-alone service: use the source copy in the ih folder
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'ih'))
    from ihlib.ha import HA as _IHA

SETTINGS = ST.load()   # stand-alone service: settings.local.json next to this file; the add-on passes its own to Ctx
HA_ROOT = SETTINGS['root']
API = 'https://discord.com/api/v10'


_LOGGER = None
HEARTBEAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'heartbeat')


def setup_logging():
    """Rotating file log (1 MB x 3), git-ignored. stdout only gets start-up and errors."""
    global _LOGGER
    import logging, logging.handlers
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(d, exist_ok=True)
    lg = logging.getLogger('commands')
    lg.setLevel(logging.INFO)
    h = logging.handlers.RotatingFileHandler(os.path.join(d, SETTINGS['log_name'] + '.log'), maxBytes=1000000, backupCount=2)
    h.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%Y-%m-%d %H:%M:%S'))
    lg.addHandler(h)
    _LOGGER = lg

    class _Out:   # feature modules use print(): send it to the log file too
        def write(self, t):
            if t.strip():
                lg.info(t.rstrip())
        def flush(self):
            pass
    sys.stdout = sys.stderr = _Out()


def beat():
    try:
        with open(HEARTBEAT, 'w') as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def log(*a):
    msg = ' '.join(str(x) for x in a)
    if _LOGGER:
        _LOGGER.info(msg)
    else:
        print(time.strftime('%H:%M:%S'), msg, flush=True)


def discord_token():
    """Stand-alone service only: the bot token of Home Assistant's own discord integration (needs settings root)."""
    if not HA_ROOT:
        raise RuntimeError('settings root is empty')
    cfg = os.path.join(HA_ROOT, '.storage/core.config_entries')
    for e in json.load(open(cfg))['data']['entries']:
        if e['domain'] == 'discord':
            return e['data']['api_token']
    raise RuntimeError('no discord config entry')


def allowed_users():
    ids = set()
    p = ST.path_of(HA_ROOT, SETTINGS['allowed_file'])
    if not p:
        return ids
    try:
        for line in open(p):
            t = line.split('#', 1)[0].strip()
            if t:
                ids.add(t)
    except OSError:
        pass
    return ids


class Discord:
    def __init__(self, token, dry=False, user_agent=None):
        self.dry, self._n = dry, 0
        self.h = {'Authorization': 'Bot ' + token, 'User-Agent': user_agent or SETTINGS['user_agent'], 'Content-Type': 'application/json'}

    def req(self, method, path, body=None):
        """Returns (status, json or None). Writes are logged and faked in dry-run."""
        if self.dry and method != 'GET':
            self._n += 1
            log(f'[DRY] {method} {path}', json.dumps(body, ensure_ascii=False)[:600] if body else '')
            return 200, {'id': f'dry{self._n}'}
        for _ in range(4):
            r = urllib.request.Request(API + path, method=method, headers=self.h, data=json.dumps(body).encode() if body is not None else None)
            try:
                with urllib.request.urlopen(r, timeout=20) as x:
                    raw = x.read()
                    return x.status, (json.loads(raw) if raw else None)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    try:
                        time.sleep(float(json.loads(e.read()).get('retry_after', 1)) + 0.3)
                    except Exception:
                        time.sleep(2)
                    continue
                return e.code, None
        return 429, None

    def me(self):
        if not hasattr(self, '_me'):
            self._me = (self.req('GET', '/users/@me')[1] or {})
        return self._me

    def post(self, chan, text, **kw):
        return self.req('POST', f'/channels/{chan}/messages', dict({'content': text[:1900]}, **kw))

    def get_bytes(self, url):
        try:
            with urllib.request.urlopen(url, timeout=30) as x:
                return x.read()
        except Exception:
            return None

    def find_channel(self, name):
        """A channel NUMBER is used as it is (names can change); a name is looked up in the bot's servers."""
        if str(name).strip().isdigit():
            return str(name).strip()
        st, guilds = self.req('GET', '/users/@me/guilds')
        for g in (guilds or []) if st == 200 else []:
            st, chans = self.req('GET', f"/guilds/{g['id']}/channels")
            for c in (chans or []) if st == 200 else []:
                if c.get('name') == name and c.get('type') == 0:
                    return c['id']
        return None


class HA:
    base = 'http://supervisor/core/api'

    def __init__(self, token=None, dry=False):
        self.dry = dry
        self.token = token or os.environ.get('SUPERVISOR_TOKEN') or ''
        if not self.token:
            raise RuntimeError('SUPERVISOR_TOKEN is not set in this environment')

    def _req(self, method, path, body=None):
        return _IHA(self.token, rest_base=self.base + '/')._rest(method, path, body, 60)

    def state(self, entity):
        try:
            return self._req('GET', '/states/' + entity)
        except urllib.error.HTTPError:
            return None

    def states(self, domain=None):
        al = self._req('GET', '/states')
        return [s for s in al if not domain or s['entity_id'].startswith(domain + '.')]

    def get_items(self, entity, status='needs_action'):
        res = self._req('POST', '/services/todo/get_items?return_response', {'entity_id': entity, 'status': status})
        return ((res or {}).get('service_response') or {}).get(entity, {}).get('items', [])

    def config(self):
        return self._req('GET', '/config')

    def history(self, entity, start_dt):
        """State changes of one entity since start_dt (read only), oldest first."""
        from urllib.parse import quote
        res = self._req('GET', '/history/period/' + quote(start_dt.strftime('%Y-%m-%dT%H:%M:%S+00:00')) + '?filter_entity_id=' + entity + '&no_attributes&significant_changes_only=0')
        return (res or [[]])[0] if res else []

    def call(self, domain, service, data):
        """A service call that changes things (logged only in dry-run)."""
        if self.dry:
            log(f'[DRY] HA {domain}.{service}', json.dumps(data, ensure_ascii=False)[:400])
            return None
        return self._req('POST', f'/services/{domain}/{service}', data)

    def fire(self, event, data):
        if self.dry:
            log(f'[DRY] HA event {event}', data)
            return
        self._req('POST', '/events/' + event, data)


class Ctx:
    def __init__(self, discord, ha, dry, cfg=None):
        self.discord, self.ha, self.dry = discord, ha, dry
        self.cfg = cfg or SETTINGS
        self.allowed = allowed_users
        self.now = time.time


FEATURES = list(SETTINGS['service_features'])   # stand-alone service: one name per feature; each is features/<name>.py with class Feature(ctx): interval, tick()


DISABLED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'disabled_features.txt')   # git-ignored


def disabled_features(path=None):
    """Feature names switched off in this service (one per line; commas also work; '#' starts a comment). Read at start only.
    Used for a cut-over to the add-on: with 'shopping' and 'house' listed, this service stops answering them."""
    try:
        txt = open(path or DISABLED_FILE).read()
    except OSError:
        return set()
    out = set()
    for line in txt.splitlines():
        for t in line.split('#', 1)[0].replace(',', ' ').split():
            out.add(t.strip())
    return out


def active_features(path=None):
    off = disabled_features(path)
    return [n for n in FEATURES if n not in off]


def run(dry):
    import importlib
    setup_logging()
    ctx = Ctx(Discord(discord_token(), dry), HA(dry=dry), dry, SETTINGS)
    print('commands service starting', 'DRY-RUN' if dry else 'LIVE', '(log: logs/%s.log)' % SETTINGS['log_name'], file=sys.__stdout__, flush=True)
    log('commands service starting', 'DRY-RUN (nothing is posted)' if dry else 'LIVE')
    active = active_features()
    if len(active) != len(FEATURES):
        log('features DISABLED by disabled_features.txt:', ','.join(n for n in FEATURES if n not in active))

    beats = {}

    def runner(name):
        feat = importlib.import_module('features.' + name).Feature(ctx)
        while True:
            try:
                feat.tick()
                beats[name] = time.time()
            except Exception:
                log(f'{name} error:', traceback.format_exc(limit=3).strip().splitlines()[-1])
                time.sleep(30)
            time.sleep(feat.interval)
    threads = [threading.Thread(target=runner, args=(n,), daemon=True, name=n) for n in active]
    for t in threads:
        t.start()
    while True:   # heartbeat only while every feature thread has ticked recently
        time.sleep(10)
        if all(t.is_alive() for t in threads):
            beat()


def check():
    import py_compile, glob
    d = os.path.dirname(os.path.abspath(__file__))
    bad = 0
    for f in glob.glob(d + '/*.py') + glob.glob(d + '/features/*.py') + glob.glob(d + '/tests/*.py'):
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            print('FAIL', e); bad += 1
    import importlib
    sys.path.insert(0, d)
    for n in FEATURES:
        importlib.import_module('features.' + n)
    print('check ok' if not bad else 'check FAILED'); return bad
