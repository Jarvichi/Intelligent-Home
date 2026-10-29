"""Store (recipes, meal plan), options, HA calendar fetch, rate limit. stdlib only."""
import glob, hashlib, hmac, json, os, re, shutil, time, urllib.request, urllib.parse
from datetime import date, datetime, timedelta, timezone
import clashes, recipeadmin
from ihlib.ha import HA   # shared library: shipped as app/ihlib

VERSION = '0.4.0'
SLUG_RE = re.compile(r'^[a-z0-9-]{1,60}$')


def snip(s, n=200):
    """First n chars as ONE line: backslash, quote and newlines escaped. Only used when option log_payloads is true."""
    s = str(s or '')[:n]
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n')


def tag(v, default='-'):
    """A short safe token for a log line (letters, digits and . _ : - only)."""
    t = re.sub(r'[^A-Za-z0-9._:/-]', '_', str(v if v is not None else default))[:64]
    return t or default


def load_options(path):
    try:
        o = json.load(open(path))
    except (OSError, ValueError):
        o = {}
    cc = o.get('clash_config') or ''
    try:
        cc = json.loads(cc) if isinstance(cc, str) and cc.strip() else (cc if isinstance(cc, dict) else {})
    except ValueError:
        cc = {}
    ha_url = str(o.get('ha_url') or 'http://supervisor/core').rstrip('/')
    ha_token = str(o.get('ha_token') or '').strip()
    if not ha_token and ha_url == 'http://supervisor/core':   # homeassistant_api: the add-on's own Supervisor token works for HA Core
        ha_token = os.environ.get('SUPERVISOR_TOKEN', '').strip()
    return {
        'api_token': str(o.get('api_token') or '').strip(),
        'ha_url': ha_url,
        'ha_token': ha_token,
        'plan_entity_prefix': str(o.get('plan_entity_prefix') or '').strip(),
        'rate_per_min': int(o.get('rate_per_min') or 60),
        'clash_config': cc,
        'log_payloads': o.get('log_payloads') is True,
        'publish_webhook_url': str(o.get('publish_webhook_url') or '').strip(),
        'publish_live': o.get('publish_live') is True,
        'brain_url': str(o.get('brain_url') or '').strip(),
        'brain_token': str(o.get('brain_token') or '').strip(),
        'facts_url': str(o.get('facts_url') or '').strip().rstrip('/'),
        'facts_key': str(o.get('facts_key') or '').strip(),
        'cook': cook_config(o),
    }


def cook_config(o):
    """Cook-control settings (all ids come from options; nothing is built in). cook_mode: off | dry (default) | live."""
    s = lambda k, d='': str(o.get(k) or d).strip()
    mode = s('cook_mode', 'dry')
    return {
        'mode': mode if mode in ('off', 'dry', 'live') else 'dry', 'confirm': s('cook_confirm'),
        'other_entity': s('other_controller_entity'), 'other_max_age': int(o.get('other_controller_max_age_s') or 120), 'other_none': o.get('other_controller_none') is True,
        'channel': s('discord_channel_id'), 'child_dm': s('discord_child_id'), 'owner_dm': s('discord_owner_id'), 'child_name': s('child_name'),
        'discord_token': s('discord_token'),
        'player_entity': s('player_entity'), 'player_group': [x.strip() for x in s('player_group').split(',') if x.strip()], 'tts_entity': s('tts_entity', 'tts.google_translate_en_com'),
        'oven_prefix': s('oven_prefix'), 'oven_device': s('oven_device_id'),
        'plan_sensor': s('plan_sensor_entity', 'sensor.cooking_plan'), 'rerender_script': s('rerender_script_entity'),
    }


class Store:
    def __init__(self, data, seed_dir, opts, log=None, clock=time.time, opener=None):
        self.data, self.seed, self.opts = data, seed_dir, opts
        self.opts.setdefault('cook', cook_config({}))
        self.opts.setdefault('publish_webhook_url', ''); self.opts.setdefault('publish_live', False); self.opts.setdefault('brain_url', ''); self.opts.setdefault('brain_token', '')
        self.log = log or (lambda *a: None)
        self.clock, self.opener = clock, opener or urllib.request.urlopen
        self.rdir = os.path.join(data, 'recipes')
        self.started = clock()
        self.hits = []
        self.calls = 0
        self.seed_recipes()
        self.engine = None

    def make_engine(self, clock_now=None, discord=None, ha=None, **kw):
        """The cook engine. HA client only if ha_token is set; Discord client only if a bot token is set."""
        import cookengine
        c = self.opts['cook']
        if ha is None and self.opts['ha_token']:
            ha = cookengine.HAClient(self.opts['ha_url'], self.opts['ha_token'], self.opener)
        if discord is None and c.get('discord_token'):
            discord = cookengine.DiscordClient(c['discord_token'], self.opener)
        kw.setdefault('log', self.log)
        if 'facts' not in kw and self.opts.get('facts_url') and self.opts.get('facts_key'):   # off when either option is empty
            from factsclient import FactsClient
            kw['facts'] = FactsClient(self.opts['facts_url'], self.opts['facts_key'], 'cooking', self.opener)
        self.engine = cookengine.Engine(c, os.path.join(self.data, 'cook'), ha=ha, discord=discord, **kw)
        return self.engine

    # --- publish (replaces recipes.py publish). Dry by default: only logs counts. Live: brain notes -> ih_brain (brain_write over HTTP JSON-RPC), www files -> publish_webhook_url.
    def _hash_file(self):
        return os.path.join(self.data, 'brain_hashes.json')

    @staticmethod
    def _note_hash(text):
        body = '\n'.join(l for l in text.split('\n') if not l.startswith('updated:'))   # the date line changes daily: not a content change
        return hashlib.sha256(body.encode()).hexdigest()

    def _brain_call(self, path, content):
        msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'brain_write', 'arguments': {
            'path': path, 'content': content, 'author': 'ih_cooking', 'summary': 'recipe published by ih_cooking'}}}
        req = urllib.request.Request(self.opts['brain_url'], data=json.dumps(msg, ensure_ascii=False).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'Authorization': 'Bearer ' + self.opts['brain_token']})
        with self.opener(req, timeout=20) as r:
            j = json.loads(r.read() or b'{}')
        if j.get('error') or (j.get('result') or {}).get('isError'):
            raise ValueError('brain refused')

    def publish_brain(self, notes):
        """-> dict(sent, unchanged, failed). Only notes whose content changed are sent. Never logs note text."""
        try: hashes = json.load(open(self._hash_file()))
        except (OSError, ValueError): hashes = {}
        sent = unchanged = failed = 0
        for path, text in notes.items():
            h = self._note_hash(text)
            if hashes.get(path) == h:
                unchanged += 1; continue
            try:
                self._brain_call(path, text); hashes[path] = h; sent += 1
            except Exception as e:
                failed += 1; self.log('brain publish failed: %s (%s)' % (tag(path), type(e).__name__))
        if sent:
            try:
                tmp = self._hash_file() + '.tmp'; json.dump(hashes, open(tmp, 'w')); os.replace(tmp, self._hash_file())
            except OSError: pass
        self.log('publish brain: sent %d, unchanged %d, failed %d' % (sent, unchanged, failed))
        return {'sent': sent, 'unchanged': unchanged, 'failed': failed}

    def publish(self):
        notes = recipeadmin.brain_notes(self.data)
        www = {'www/' + k: v for k, v in recipeadmin.www_index(self.data).items()}
        url, burl = self.opts['publish_webhook_url'], self.opts['brain_url']
        if not self.opts['publish_live']:
            self.log('publish (dry): would write %d brain notes%s and send %d www files%s' % (
                len(notes), '' if burl else ' (brain_url not set)', len(www) if url else 0, '' if url else ' (publish_webhook_url not set: www skipped)'))
            return {'ok': True, 'dry': True, 'files': len(notes) + (len(www) if url else 0), 'notes': len(notes), 'www': len(www) if url else 0}
        res = {'ok': True, 'dry': False, 'notes': len(notes), 'www': 0}
        if burl and self.opts['brain_token']:
            b = self.publish_brain(notes); res['brain'] = b
            if b['failed']: res['ok'] = False
        else:
            self.log('publish: brain_url or brain_token not set, brain notes skipped')
        if url:
            files = dict(('brain/' + k, v) for k, v in notes.items()); files.update(www)
            req = urllib.request.Request(url, data=json.dumps({'source': 'ih_cooking', 'files': files}, ensure_ascii=False).encode(), method='POST', headers={'Content-Type': 'application/json'})
            try:
                with self.opener(req, timeout=20) as r: code = getattr(r, 'status', 200)
                self.log('publish: sent %d files to the webhook (HTTP %s)' % (len(files), code)); res['www'] = len(www)
            except Exception as e:
                self.log('publish failed: ' + type(e).__name__); res['ok'] = False; res['why'] = type(e).__name__
        res['files'] = res['notes'] + res['www']
        return res

    def seed_recipes(self):
        os.makedirs(self.rdir, exist_ok=True)
        for f in glob.glob(os.path.join(self.seed, '*.json')):
            dst = os.path.join(self.rdir, os.path.basename(f))
            if not os.path.exists(dst):   # never overwrite /data copies
                shutil.copy(f, dst)

    # --- safety
    def stopped(self):
        return os.path.exists(os.path.join(self.data, 'STOP'))

    def authed(self, header):
        t = self.opts['api_token']
        return bool(t) and hmac.compare_digest(str(header or ''), 'Bearer ' + t)

    def rate_ok(self):
        now = self.clock()
        self.hits = [h for h in self.hits if now - h < 60]
        if len(self.hits) >= self.opts['rate_per_min']:
            return False
        self.hits.append(now)
        return True

    # --- recipes
    def recipes(self):
        out = []
        for f in sorted(glob.glob(os.path.join(self.rdir, '*.json'))):
            try:
                j = json.load(open(f))
            except (OSError, ValueError):
                continue
            out.append({'slug': os.path.basename(f)[:-5], 'title': j.get('title') or j.get('name'), 'serves': j.get('serves'),
                        'meal_time': j.get('meal_time'), 'oven': j.get('oven')})
        return out

    def recipe(self, slug):
        if not SLUG_RE.match(slug or ''):
            return None
        try:
            r = json.load(open(os.path.join(self.rdir, slug + '.json')))
        except (OSError, ValueError):
            return None
        r['slug'] = slug
        return r

    def recipe_meta(self):
        m = {}
        for r in self.recipes():
            full = self.recipe(r['slug']) or {}
            for k in (full.get('title'), full.get('name')):
                if k:
                    m[k.lower()] = {'serves': full.get('serves'), 'meal_time': full.get('meal_time')}
        return m

    # --- plan
    def plan(self):
        """-> (plan dict, source). /data/mealplan.json first, else HA input_selects (read only), else empty."""
        try:
            p = json.load(open(os.path.join(self.data, 'mealplan.json')))
            if isinstance(p, dict) and any(p.values()):
                return {k: str(p.get(k) or '') for k in clashes.DAYS}, 'file'
        except (OSError, ValueError):
            pass
        pre = self.opts['plan_entity_prefix']
        if pre and self.opts['ha_token']:
            p = {}
            for k in clashes.DAYS:
                try:
                    s = self.ha('/api/states/' + pre + k)
                    st = s.get('state', '')
                    p[k] = '' if st in ('-', 'unknown', 'unavailable') else st
                except Exception:
                    p[k] = ''
            if any(p.values()):
                return p, 'ha'
        return {}, 'empty'

    # --- HA (read only GETs)
    def ha(self, path):
        self.calls += 1
        base = self.opts['ha_url'] + '/api/'
        return HA(token=self.opts['ha_token'], opener=self.opener, rest_base=base).get(path[5:] if path.startswith('/api/') else path, timeout=15)

    def calendars(self, start, end, cfg):
        ents = [p['calendar'] for p in cfg['people'] if p.get('calendar')] + list(cfg['shared_calendars'])
        out, errors = {}, []
        for e in dict.fromkeys(ents):
            q = urllib.parse.urlencode({'start': start, 'end': end})
            try:
                out[e] = self.ha('/api/calendars/%s?%s' % (e, q))
            except Exception as ex:
                errors.append('%s: %s' % (e, type(ex).__name__))   # never the url or token
        return out, errors

    def clashes(self, days, today, events=None, plan=None):
        cfg = clashes.merged_config(self.opts['clash_config'])
        try:
            from zoneinfo import ZoneInfo
            tz, tzwarn = ZoneInfo(cfg['timezone']), None
        except Exception:
            tz, tzwarn = timezone.utc, 'timezone %s unavailable, using UTC' % cfg['timezone']
        errors = []
        if events is None:
            if not self.opts['ha_token']:
                events, errors = {}, ['ha_token not set: no calendar data']
            else:
                end = today + timedelta(days=days + 1)
                events, errors = self.calendars(today.isoformat() + 'T00:00:00', end.isoformat() + 'T00:00:00', cfg)
        src = 'given'
        if plan is None:
            plan, src = self.plan()
        res = clashes.compute(today, days, plan, events, cfg, self.recipe_meta(), tz)
        res['plan_source'] = src
        res['errors'] = errors + ([tzwarn] if tzwarn else [])
        return res

    def invocation_line(self, headers, path, code, out_text, t0, why='-'):
        """ONE line per authenticated API request (an invocation): id, caller, what, chars in/out, ms, ok, why. Path/response text only when log_payloads is on."""
        try:
            f = ['invoke', 'id=' + tag(headers.get('X-Request-Id')), 'src=' + tag(headers.get('X-Caller')), 'what=' + tag(path.split('?')[0]), 'in=%d' % len(path),
                 'out=%d' % len(out_text), 'ms=%d' % int((self.clock() - t0) * 1000), 'ok=%s' % (code < 400), 'why=' + tag(why if code >= 400 else '-'), 'http=%d' % code]
            if self.opts.get('log_payloads'):
                f += ['req="%s"' % snip(path), 'ans="%s"' % snip(out_text)]
            self.log(' '.join(f))
        except Exception:
            pass

    def status(self):
        return {'ok': True, 'version': VERSION, 'uptime_s': int(self.clock() - self.started), 'stopped': self.stopped(),
                'recipes': len(self.recipes()), 'token_set': bool(self.opts['api_token']), 'ha_token_set': bool(self.opts['ha_token']),
                'plan_source': self.plan()[1], 'ha_calls': self.calls, 'rate_per_min': self.opts['rate_per_min'],
                'people_configured': len(clashes.merged_config(self.opts['clash_config'])['people'])}
