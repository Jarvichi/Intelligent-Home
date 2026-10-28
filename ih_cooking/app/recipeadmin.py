"""Recipe writes for ih_cooking 0.2 (ports of host recipe_service/main.py validate+save, recipes.py save/shop/publish). stdlib only.
Data lives in /data: recipes/<slug>.json, recipe_backups/, staples.json, meal_items.json, mealplan.json."""
import base64, json, os, re, time

EDIT = ('name', 'title', 'oven', 'serves', 'ro' + 'b_minutes', 'steps', 'prep', 'shopping', 'equipment')
SLUG = re.compile(r'^[a-z0-9-]{1,60}$')
DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')


def validate(d):
    e = []
    if not isinstance(d, dict): return ['not an object']
    for k in ('name', 'title'):
        if k in d and not (isinstance(d[k], str) and 0 < len(d[k]) <= 120): e.append(f'{k}: text 1-120 chars')
    for k, lo, hi in (('oven', 0, 300), ('ro' + 'b_minutes', 0, 180)):
        if k in d and d[k] not in (None, '') and not (isinstance(d[k], int) and not isinstance(d[k], bool) and lo <= d[k] <= hi): e.append(f'{k}: whole number {lo}-{hi}')
    if d.get('serves') not in (None, '') and not ((isinstance(d['serves'], int) and not isinstance(d['serves'], bool) and 1 <= d['serves'] <= 20) or (isinstance(d['serves'], str) and len(d['serves']) <= 40)): e.append('serves: number or short text')
    for k in ('prep', 'shopping', 'equipment'):
        if k in d and not (isinstance(d[k], list) and all(isinstance(x, str) and len(x) <= 300 for x in d[k])): e.append(f'{k}: list of text')
    if 'steps' in d:
        s = d['steps']
        if not (isinstance(s, list) and s): e.append('steps: need at least one')
        else:
            for i, x in enumerate(s):
                if not (isinstance(x, dict) and isinstance(x.get('text'), str) and x['text'].strip() and len(x['text']) <= 600
                        and isinstance(x.get('before'), int) and not isinstance(x.get('before'), bool) and 0 <= x['before'] <= 600):
                    e.append(f'step {i+1}: needs text and whole minutes before serving 0-600')
    return e


def atomic(path, obj):
    tmp = path + '.tmp'; json.dump(obj, open(tmp, 'w'), indent=1, ensure_ascii=False); os.replace(tmp, path)


def save_recipe(data, slug, d):
    """Validated edit of an EXISTING recipe with a backup of the old file (same rules as the host recipe service). Returns (ok, errors)."""
    rdir = os.path.join(data, 'recipes'); f = os.path.join(rdir, slug + '.json')
    if not SLUG.match(slug or '') or not os.path.exists(f): return False, ['no such recipe']
    e = validate(d)
    if e: return False, e
    r = json.load(open(f)); old = json.loads(json.dumps(r))
    bdir = os.path.join(data, 'recipe_backups'); os.makedirs(bdir, exist_ok=True)
    atomic(os.path.join(bdir, '%s-%s.json' % (slug, time.strftime('%Y%m%d-%H%M%S'))), r)
    for k in EDIT:
        if k in d:
            if d[k] in (None, '') and k in ('oven', 'serves'):
                if k == 'oven' and 'oven' in r: r['oven'] = None
                else: r.pop(k, None)
            else: r[k] = d[k]
    if 'steps' in d:
        olds = {s.get('text'): s for s in old.get('steps', [])}
        r['steps'] = [dict(before=s['before'], text=s['text'], **{k: v for k, v in olds.get(s['text'], {}).items() if k not in ('before', 'text')}) for s in d['steps']]
    atomic(f, r)
    return True, []


def create_recipe(data, d):
    """New recipe from the builder (host recipes.py save). d has name/title and steps. Returns (ok, slug-or-errors)."""
    if not isinstance(d, dict): return False, ['not an object']
    n = re.sub(r'[^a-z0-9-]+', '-', str(d.get('id') or d.get('name') or d.get('title') or 'recipe').lower()).strip('-')[:40] or 'recipe'
    steps = [dict(dict(before=int(s.get('before', 0)), text=str(s.get('text', ''))[:200], short=str(s.get('short') or s.get('text', ''))[:34]),
                  **({'note': str(s['note'])[:200]} if s.get('note') else {})) for s in d.get('steps', []) if isinstance(s, dict) and str(s.get('text', '')).strip()]
    r = {k: v for k, v in d.items() if k not in ('id', '_edit', 'steps', 'prep', 'shopping', 'equipment')}
    r['steps'] = steps
    for k in ('prep', 'shopping', 'equipment'): r[k] = [str(p)[:80] for p in d.get(k, []) if str(p).strip()]
    e = validate(r)
    if e: return False, e
    rdir = os.path.join(data, 'recipes'); os.makedirs(rdir, exist_ok=True)
    if os.path.exists(os.path.join(rdir, n + '.json')): return False, ['recipe %s already exists (edit it instead)' % n]
    atomic(os.path.join(rdir, n + '.json'), r)
    return True, n


def listing(data):
    out = []; rdir = os.path.join(data, 'recipes')
    for f in sorted(os.listdir(rdir)) if os.path.isdir(rdir) else []:
        if not f.endswith('.json'): continue
        try: r = json.load(open(os.path.join(rdir, f)))
        except (OSError, ValueError): continue
        st = r.get('steps', [])
        out.append(dict(id=f[:-5], title=(r.get('title') or r.get('name') or f[:-5]).strip(), minutes=max([s.get('before', 0) for s in st] or [0]),
                        serves=r.get('serves'), oven=r.get('oven'), steps=len(st), shopping=len(r.get('shopping', []))))
    return out


def shop(data, names, groups, now=time.time):
    """Merged shopping list for these recipes (host `recipes.py shop`) and update of the meal-plan ledger meal_items.json."""
    rdir = os.path.join(data, 'recipes')
    want = [os.path.basename(n) for n in names if n and os.path.exists(os.path.join(rdir, os.path.basename(n) + '.json'))]
    items, seen, titles = [], set(), []
    for n in want:
        r = json.load(open(os.path.join(rdir, n + '.json'))); titles.append(r.get('name') or n)
        for it in r.get('shopping', []):
            k = re.sub(r'^[0-9.,/½¼ ]+(g|kg|ml|l|tbsp|tsp)?\s*', '', it.lower()).strip()
            if k not in seen: seen.add(k); items.append(it)
    try: st = json.load(open(os.path.join(data, 'staples.json')))['groups']
    except (OSError, ValueError, KeyError): st = {}
    gs = [g for g in groups if g in st]
    staples = [it for g in gs for it in st[g]['items']]
    try:
        lf = os.path.join(data, 'meal_items.json')
        try: led = json.load(open(lf))
        except (OSError, ValueError): led = {}
        t = now()
        led = {k: v for k, v in led.items() if t - v < 45 * 86400}
        for it in items + staples: led[re.sub(r'^(meals|staples): ', '', it.lower().strip())] = t
        json.dump(led, open(lf, 'w'), ensure_ascii=False)
    except OSError:
        pass
    return {'items': items, 'recipes': titles, 'staples': staples, 'groups': [st[g]['name'] for g in gs]}


def write_plan(data, p):
    if not isinstance(p, dict) or not set(p) <= set(DAYS) or not all(isinstance(v, str) and len(v) <= 80 for v in p.values()):
        return False, ['plan: an object with keys mon..sun and text values']
    atomic(os.path.join(data, 'mealplan.json'), {k: p.get(k, '') for k in DAYS})
    return True, []


def brain_notes(data):
    """Same notes the host `recipes.py publish` writes: {relative path: text}. Paths are relative to the brain folder."""
    rdir = os.path.join(data, 'recipes'); out = {}
    names = sorted(f[:-5] for f in os.listdir(rdir) if f.endswith('.json')) if os.path.isdir(rdir) else []
    recs = {n: json.load(open(os.path.join(rdir, n + '.json'))) for n in names}
    for n in names:
        r = recs[n]; t = r.get('name') or r.get('title', n)
        upd = time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(os.path.join(rdir, n + '.json'))))
        lines = ['---', f'title: "Recipe: {t}"', 'type: note', 'status: current', 'share: false', 'tags:', '  - recipes', 'aliases:', f'  - "Recipe: {t}"', f'  - "{t}"',
                 'related:', '  - "[[home/recipes/recipes]]"', f'updated: {upd}', '---', f'# {t}', '', 'Part of [[home/recipes/recipes|Recipes]].', '',
                 f'Generated by the ih_cooking add-on. Oven {r.get("oven", "?")}°. A message goes to the child {r.get('ro' + 'b_minutes', 15)} min before serving.', '', '## Steps (minutes before serving)']
        lines += [f'- **{s.get("before", 0)}** {s.get("text", "")}' for s in sorted(r.get('steps', []), key=lambda s: -s.get('before', 0))]
        lines += ['', '## Prep'] + [f'- {p}' for p in r.get('prep', [])] + ['', '## Shopping'] + [f'- {p}' for p in r.get('shopping', [])] + (['', '## Equipment'] + [f'- {p}' for p in r.get('equipment', [])] if r.get('equipment') else [])
        out['home/recipes/%s.md' % n] = '\n'.join(lines) + '\n'
    ix = ['---', 'title: "Recipes: index"', 'type: area', 'status: current', 'share: false', 'tags:', '  - recipes', 'aliases:', '  - "Recipes: index"', '  - "Recipes"',
          f'updated: {time.strftime("%Y-%m-%d")}', '---', '# Recipes', '', 'Recipes the cooking helper can run. Generated by the ih_cooking add-on.', '']
    ix += [f'- [[home/recipes/{n}|{recs[n].get("name") or n}]]' for n in names]
    out['home/recipes/recipes.md'] = '\n'.join(ix) + '\n'
    return out


def www_index(data):
    """The index the old builder page read from www/recipes/index.json, plus every recipe: {relative path: json text}."""
    rdir = os.path.join(data, 'recipes'); out = {}; idx = []
    for f in sorted(os.listdir(rdir)) if os.path.isdir(rdir) else []:
        if not f.endswith('.json'): continue
        r = json.load(open(os.path.join(rdir, f))); n = f[:-5]
        idx.append({'name': n, 'title': r.get('name') or r.get('title', n)})
        out['recipes/%s.json' % n] = json.dumps(r, indent=1, ensure_ascii=False)
    out['recipes/index.json'] = json.dumps(idx, ensure_ascii=False)
    return out


def do_import(data, body):
    """Seed/refresh /data from the host. body: {recipes: {slug: obj}, staples: obj, meal_items: obj, mealplan: obj, overwrite: bool}. Never overwrites unless overwrite."""
    ow = body.get('overwrite') is True; n = {'recipes': 0, 'skipped': 0}
    rdir = os.path.join(data, 'recipes'); os.makedirs(rdir, exist_ok=True)
    for slug, obj in (body.get('recipes') or {}).items():
        f = os.path.join(rdir, slug + '.json')
        if not SLUG.match(slug) or not isinstance(obj, dict): n['skipped'] += 1; continue
        if os.path.exists(f) and not ow: n['skipped'] += 1; continue
        atomic(f, obj); n['recipes'] += 1
    for key, fn in (('staples', 'staples.json'), ('meal_items', 'meal_items.json'), ('mealplan', 'mealplan.json')):
        if isinstance(body.get(key), dict):
            f = os.path.join(data, fn)
            if os.path.exists(f) and not ow: n[key] = 'kept'; continue
            if key == 'mealplan':
                ok, _ = write_plan(data, body[key]); n[key] = 'written' if ok else 'rejected'
            else:
                atomic(f, body[key]); n[key] = 'written'
    return n
