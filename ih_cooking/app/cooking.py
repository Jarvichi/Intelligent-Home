"""Store (recipes, meal plan), options, HA calendar fetch, rate limit. stdlib only."""
import glob, hmac, json, os, re, shutil, time, urllib.request, urllib.parse
from datetime import date, datetime, timedelta, timezone
import clashes

VERSION = '0.1.0'
SLUG_RE = re.compile(r'^[a-z0-9-]{1,60}$')


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
    return {
        'api_token': str(o.get('api_token') or '').strip(),
        'ha_url': str(o.get('ha_url') or 'http://supervisor/core').rstrip('/'),
        'ha_token': str(o.get('ha_token') or '').strip(),
        'plan_entity_prefix': str(o.get('plan_entity_prefix') or '').strip(),
        'rate_per_min': int(o.get('rate_per_min') or 60),
        'clash_config': cc,
    }


class Store:
    def __init__(self, data, seed_dir, opts, log=None, clock=time.time, opener=None):
        self.data, self.seed, self.opts = data, seed_dir, opts
        self.log = log or (lambda *a: None)
        self.clock, self.opener = clock, opener or urllib.request.urlopen
        self.rdir = os.path.join(data, 'recipes')
        self.started = clock()
        self.hits = []
        self.calls = 0
        self.seed_recipes()

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
        req = urllib.request.Request(self.opts['ha_url'] + path, headers={'Authorization': 'Bearer ' + self.opts['ha_token']})
        self.calls += 1
        with self.opener(req, timeout=15) as r:
            return json.loads(r.read().decode())

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

    def status(self):
        return {'ok': True, 'version': VERSION, 'uptime_s': int(self.clock() - self.started), 'stopped': self.stopped(),
                'recipes': len(self.recipes()), 'token_set': bool(self.opts['api_token']), 'ha_token_set': bool(self.opts['ha_token']),
                'plan_source': self.plan()[1], 'ha_calls': self.calls, 'rate_per_min': self.opts['rate_per_min'],
                'people_configured': len(clashes.merged_config(self.opts['clash_config'])['people'])}
