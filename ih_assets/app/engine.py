"""ih_assets engine: items, history, totals, one-time import, barcode lookup. Standard library only."""
import csv, datetime, json, math, os, re, urllib.request

VERSION = '0.2.2'
CATEGORIES = ('game', 'dvd', 'toy', 'device', 'book', 'other')

MIGRATIONS = [
    (1, '''CREATE TABLE items(
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'other', platform TEXT,
        qty INTEGER NOT NULL DEFAULT 1, cost REAL, low REAL, high REAL, bought_date TEXT, sold_date TEXT,
        source TEXT, checked TEXT, barcode TEXT)'''),
    (2, 'CREATE TABLE history(date TEXT PRIMARY KEY, low REAL NOT NULL, high REAL NOT NULL, count INTEGER NOT NULL)'),
    (3, 'CREATE TABLE price_log(id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL, at TEXT NOT NULL, low REAL, high REAL, source TEXT)'),
]

FIELDS = ('name', 'category', 'platform', 'qty', 'cost', 'low', 'high', 'bought_date', 'sold_date', 'source', 'checked', 'barcode')


class BadInput(ValueError):
    pass


def _num(v, name, allow_none=True):
    if v in (None, ''):
        if allow_none:
            return None
        raise BadInput(name + ' is required')
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise BadInput(name + ' must be a number')
    if f < 0 or not math.isfinite(f):
        raise BadInput(name + ' must be 0 or more')
    return round(f, 2)


def _date(v, name):
    if v in (None, ''):
        return None
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)):
        raise BadInput(name + ' must be YYYY-MM-DD')
    try:
        datetime.date.fromisoformat(str(v))
    except ValueError:
        raise BadInput(name + ' must be a real date')
    return str(v)


def clean(d, partial=False):
    out = {}
    if 'name' in d or not partial:
        n = str(d.get('name') or '').strip()
        if not n:
            raise BadInput('name is required')
        out['name'] = n[:200]
    if 'category' in d or not partial:
        c = str(d.get('category') or 'other').strip().lower()
        if c not in CATEGORIES:
            raise BadInput('category must be one of ' + ', '.join(CATEGORIES))
        out['category'] = c
    if 'qty' in d or not partial:
        try:
            q = 1 if d.get('qty') in (None, '') else int(d.get('qty'))
        except (TypeError, ValueError, OverflowError):
            raise BadInput('qty must be a whole number')
        if isinstance(d.get('qty'), bool) or (isinstance(d.get('qty'), float) and d['qty'] != q):
            raise BadInput('qty must be a whole number')
        if q < 1 or q > 999:
            raise BadInput('qty must be 1 to 999')
        out['qty'] = q
    for k in ('cost', 'low', 'high'):
        if k in d or not partial:
            out[k] = _num(d.get(k), k)
    for k in ('bought_date', 'sold_date'):
        if k in d or not partial:
            out[k] = _date(d.get(k), k)
    for k in ('platform', 'source', 'checked', 'barcode'):
        if k in d or not partial:
            v = d.get(k)
            out[k] = (str(v).strip()[:300] or None) if v is not None else None
    lo, hi = out.get('low'), out.get('high')
    if lo is not None and hi is not None and lo > hi:
        raise BadInput('low must not be above high')
    return out


class Assets:
    def __init__(self, db, today=None):
        self.db = db
        self.today = today or (lambda: datetime.date.today().isoformat())

    def _q(self, sql, params=()):
        cur = self.db.con.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ---- items
    def items(self, include_sold=False):
        sql = 'SELECT * FROM items' + ('' if include_sold else ' WHERE sold_date IS NULL') + ' ORDER BY category, name'
        return self._q(sql)

    def get(self, iid):
        r = self._q('SELECT * FROM items WHERE id=?', (iid,))
        return r[0] if r else None

    def add(self, d):
        c = clean(d)
        with self.db.tx() as con:
            cur = con.execute('INSERT INTO items(%s) VALUES(%s)' % (','.join(FIELDS), ','.join('?' * len(FIELDS))),
                              [c[k] for k in FIELDS])
            iid = cur.lastrowid
            if c['low'] is not None:
                con.execute('INSERT INTO price_log(item_id,at,low,high,source) VALUES(?,?,?,?,?)',
                            (iid, self.today(), c['low'], c['high'], c['source']))
        self.record()
        return self.get(iid)

    def update(self, iid, d):
        if not self.get(iid):
            return None
        c = clean(d, partial=True)
        cur = self.get(iid)
        lo = c.get('low', cur['low']); hi = c.get('high', cur['high'])
        if lo is not None and hi is not None and lo > hi:
            raise BadInput('low must not be above high')
        if c:
            with self.db.tx() as con:
                con.execute('UPDATE items SET %s WHERE id=?' % ','.join(k + '=?' for k in c), list(c.values()) + [iid])
                if 'low' in c or 'high' in c:
                    con.execute('INSERT INTO price_log(item_id,at,low,high,source) VALUES(?,?,?,?,?)',
                                (iid, self.today(), lo, hi, c.get('source', cur['source'])))
        self.record()
        return self.get(iid)

    def sell(self, iid, date=None):
        return self.update(iid, {'sold_date': date or self.today()})

    def delete(self, iid):
        with self.db.tx() as con:
            n = con.execute('DELETE FROM items WHERE id=?', (iid,)).rowcount
            con.execute('DELETE FROM price_log WHERE item_id=?', (iid,))
        self.record()
        return bool(n)

    # ---- totals and history
    def totals(self):
        lo = hi = n = unp = missing_low = missing_high = 0
        cats = {}
        for i in self.items():
            q = i['qty']
            n += q
            if i['low'] is None or i['high'] is None:
                unp += q
                missing_low += q if i['low'] is None else 0
                missing_high += q if i['high'] is None else 0
                continue
            lo += i['low'] * q; hi += i['high'] * q
            c = cats.setdefault(i['category'], [0, 0]); c[0] += i['low'] * q; c[1] += i['high'] * q
        return {'low': round(lo, 2), 'high': round(hi, 2), 'count': n, 'unpriced': unp, 'missing_low': missing_low, 'missing_high': missing_high,
                'by_category': {k: {'low': round(v[0], 2), 'high': round(v[1], 2)} for k, v in cats.items()}, 'date': self.today()}

    def record(self):
        t = self.totals()
        self.db.execute('INSERT OR REPLACE INTO history(date,low,high,count) VALUES(?,?,?,?)', (t['date'], t['low'], t['high'], t['count']))
        return t

    def history(self):
        return self._q('SELECT date,low,high,count FROM history ORDER BY date')

    def prices(self, iid):
        return self._q('SELECT at,low,high,source FROM price_log WHERE item_id=? ORDER BY id', (iid,))

    # ---- one-time import (refuses to overwrite)
    def import_legacy(self, inventory_path, history_path=None):
        if self.db.query('SELECT 1 FROM items LIMIT 1') or self.db.query('SELECT 1 FROM history LIMIT 1'):
            raise BadInput('database already has data; import refused')
        with open(inventory_path) as f:
            inv = json.load(f)['items']
        with self.db.tx() as con:
            for i in inv:
                c = clean({'name': i['name'], 'category': i.get('category') or 'other', 'platform': i.get('platform'), 'qty': i.get('qty') or 1,
                           'cost': i.get('cost'), 'low': i.get('low_gbp'), 'high': i.get('high_gbp'), 'bought_date': i.get('bought_date'),
                           'sold_date': i.get('sold_date'), 'source': i.get('source'), 'checked': i.get('checked')})
                con.execute('INSERT INTO items(%s) VALUES(%s)' % (','.join(FIELDS), ','.join('?' * len(FIELDS))), [c[k] for k in FIELDS])
            if history_path and os.path.exists(history_path):
                with open(history_path) as f:
                    for r in csv.DictReader(f):
                        con.execute('INSERT OR REPLACE INTO history(date,low,high,count) VALUES(?,?,?,?)',
                                    (r['date'], float(r['low']), float(r['high']), int(float(r['count']))))
        return {'items': len(inv), 'totals': self.totals()}

    # ---- sensors
    def publish(self, ha):
        t = self.totals()
        for k in ('low', 'high'):
            ha.post('states/sensor.assets_' + k, {'state': t[k], 'attributes': {
                'unit_of_measurement': 'GBP', 'friendly_name': 'Assets ' + k, 'state_class': 'measurement',
                'count': t['count'], 'unpriced': t['unpriced'], 'by_category': t['by_category'], 'source': 'ih_assets'}})
        return t


def barcode_lookup(code, get=None):
    """Sends ONLY the barcode number to Open Library / UPCitemdb."""
    code = re.sub(r'\D', '', str(code))
    if not code:
        raise BadInput('barcode must contain digits')
    get = get or (lambda url: json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ih-assets'}), timeout=12)))
    failed = False
    if len(code) in (10, 13) and (len(code) == 10 or code.startswith(('978', '979'))):
        try:
            d = get('https://openlibrary.org/api/books?bibkeys=ISBN:%s&format=json&jscmd=data' % code).get('ISBN:' + code)
            if d:
                return {'code': code, 'name': d['title'] + (': ' + d['subtitle'] if d.get('subtitle') else ''), 'source': 'openlibrary'}
        except Exception:
            failed = True
    try:
        it = get('https://api.upcitemdb.com/prod/trial/lookup?upc=' + code).get('items')
        if it:
            return {'code': code, 'name': it[0]['title'], 'source': 'upcitemdb'}
    except Exception:
        failed = True
    return {'code': code, 'name': None, 'source': None, 'lookup_failed': failed}
