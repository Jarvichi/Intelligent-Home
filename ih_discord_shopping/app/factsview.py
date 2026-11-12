"""Reads the shared facts store (ih_orchestrator /facts) for the shop checklist. stdlib only; every failure returns None (= today's output).
shopping/aisle_learned = {"order": [aisle...], "rank": {item: number}}  -> shop layout order + per-item order inside an aisle.
cooking/fresh_dates   = {"<normalised item>": "YYYY-MM-DD"}            -> date of the first planned meal that uses the item.
items/fresh_days      = {"<normalised item>": days}                    -> shelf life from the item library: 'use within N days', shown
                                                                          only when the item has no planned-meal date.
Never logs values or the key."""
import datetime, json, re, urllib.request

TIMEOUT_S = 5
DEFAULT_FRESH = ['Meat & fish', 'Fruit & veg', 'Dairy & chilled', 'Bakery']
UNITS = r'(?:kg|g|ml|l|lb|oz|tbsp|tsp|pack|packs|packet|packets|tin|tins|can|cans|jar|jars|bag|bags|bunch|bunches|pot|pots|punnet|punnets|large|small|medium|big|mixed|fresh|ripe|x)'
LEAD = re.compile(r'^(?:[0-9.,/½¼¾]+\s*(?:' + UNITS + r'\b)?\s*)+', re.I)
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
FRESH_DAYS_REF = 'items/fresh_days'
MAX_SHELF_DAYS = 60   # a longer shelf life is not a "fresh" hint: ignored


def norm_key(s):
    """Normalised item name, the same rule ih_cooking uses when it writes fresh_dates: lower case, no 'meals: ' tag, no brackets, no leading
    quantity or unit, no trailing 'x2'. 'Meals: 2 chicken breasts (about 450 g)' -> 'chicken breasts'."""
    t = str(s or '').lower().strip()
    t = re.sub(r'^(?:meals|staples):\s*', '', t)
    t = re.sub(r'\([^)]*\)', ' ', t)
    t = re.sub(r'\s+[x×]\s?\d+\s*$', '', t)
    t = re.sub(r'^\d+\s?[x×]\s+', '', t)
    t = LEAD.sub('', t)
    t = re.sub(r'\s+[x×]\s?\d+\s*$', '', t)
    return re.sub(r'\s+', ' ', t).strip(' ,;-')[:60]


def _stem(w):
    if len(w) > 4 and w.endswith('ies'):
        return w[:-3] + 'y'
    if len(w) > 4 and w.endswith(('oes', 'ches', 'shes')):
        return w[:-2]
    if len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
        return w[:-1]
    return w


def tokens(s):
    return [_stem(w) for w in re.findall(r"[a-z0-9&']+", norm_key(s))]


def _match(item, table, value):
    """Smallest value(v) over the keys of `table` that name the item, or None. value(v) -> a comparable value, or None to skip the entry.
    Exact normalised match first; else the shorter name's words are all in the longer name and the last word (the thing itself) is the same
    ('chicken thighs' ~ 'chicken thigh', 'peppers' ~ 'mixed peppers')."""
    it = tokens(item)
    if not it or not isinstance(table, dict):
        return None
    exact, loose = [], []
    for k, v in table.items():
        d = value(v)
        if d is None:
            continue
        kt = tokens(k)
        if not kt:
            continue
        if kt == it:
            exact.append(d)
        else:
            a, b = (kt, it) if len(kt) <= len(it) else (it, kt)
            if a[-1] == b[-1] and set(a) <= set(b):
                loose.append(d)
    pool = exact or loose
    return min(pool) if pool else None


def find_date(item, dates, today=None):
    """-> datetime.date for the item, or None. Matching as _match. Earliest date wins; past dates are ignored."""
    today = today or datetime.date.today()

    def value(v):
        if not (isinstance(v, str) and DATE_RE.match(v)):
            return None
        try:
            d = datetime.date.fromisoformat(v)
        except ValueError:
            return None
        return None if d < today else d
    return _match(item, dates, value)


def find_days(item, days):
    """-> shelf life in days for the item (int), or None. Matching as _match. The shortest shelf life wins."""
    return _match(item, days, lambda v: v if isinstance(v, int) and not isinstance(v, bool) and 0 < v <= MAX_SHELF_DAYS else None)


def fmt_days(n):
    return 'use within %d day%s' % (n, '' if n == 1 else 's')


def fmt_date(d):
    return '%s %d %s' % (d.strftime('%a'), d.day, d.strftime('%b'))


def get_fact(url, key, ref, opener=None):
    """Value of fact 'module/name', or None (not set, off, any failure)."""
    if not (url and key):
        return None
    try:
        req = urllib.request.Request('%s/facts/%s' % (url.rstrip('/'), ref), headers={'Authorization': 'Bearer ' + key})
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read())['value']
    except Exception:
        return None


def learned(url, key, base, opener=None):
    """(order, rank) from shopping/aisle_learned, or None. Order keeps only aisles in `base` and appends the missing ones (as features.shopping.learned)."""
    v = get_fact(url, key, 'shopping/aisle_learned', opener)
    if not isinstance(v, dict) or not isinstance(v.get('order'), list):
        return None
    base = list(base)
    order = [g for g in v['order'] if g in base]
    rank = v.get('rank')
    rank = {str(k): x for k, x in rank.items() if isinstance(x, (int, float)) and not isinstance(x, bool)} if isinstance(rank, dict) else {}
    return order + [g for g in base if g not in order], rank


def fresh_dates(url, key, opener=None):
    v = get_fact(url, key, 'cooking/fresh_dates', opener)
    return v if isinstance(v, dict) else {}


def fresh_days(url, key, opener=None):
    v = get_fact(url, key, FRESH_DAYS_REF, opener)
    return v if isinstance(v, dict) else {}


def decorate(msgs, mark, fresh_aisles, dates, today=None, limit=1900, days=None):
    """msgs = checklist texts ('<mark> **Aisle** ...' then 'KEY item' lines). -> (new texts, [{line index: suffix}, ...]).
    A fresh-aisle line gets ' (Tue 3 Nov)' when `dates` has the item; else ' (use within 5 days)' when `days` (shelf life) has it.
    A message that would pass `limit` characters is left as it was."""
    out, sufs = [], []
    for t in msgs:
        lines = t.split('\n')
        m = re.search(r'\*\*(.+?)\*\*', lines[0])
        suf = {}
        if m and m.group(1) in fresh_aisles and (dates or days):
            new = list(lines)
            for n in range(1, len(lines)):
                item = lines[n].split(' ', 1)[1] if ' ' in lines[n] else ''
                d = find_date(item, dates, today)
                n_days = None if d else find_days(item, days)
                if d or n_days:
                    suf[str(n)] = ' (%s)' % (fmt_date(d) if d else fmt_days(n_days))
                    new[n] = lines[n] + suf[str(n)]
            if suf and len('\n'.join(new)) <= limit:
                lines = new
            else:
                suf = {}
        out.append('\n'.join(lines))
        sufs.append(suf)
    return out, sufs
