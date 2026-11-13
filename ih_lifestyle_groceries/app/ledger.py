"""Receipts ledger: the WHOLE receipt, every line, in this add-on's SQLite database (ihlib.db). Slice 2 of the item library (13 Nov 2026).
Money is stored in pence (integers). No card numbers, auth codes or other payment details are ever stored or accepted.
A product is identified by `key`: the item id when the till name is a known alias, else a normalised form of the till name (so unmatched
lines still build price history and buying gaps, and are matched later without losing anything)."""
import re
from datetime import datetime, timezone
from ihlib import db as ihdb

MIGRATIONS = [
    (1, '''CREATE TABLE receipts(
        id INTEGER PRIMARY KEY, shop TEXT NOT NULL, branch TEXT NOT NULL DEFAULT '', bought_on TEXT NOT NULL, total_p INTEGER NOT NULL,
        savings_p INTEGER NOT NULL DEFAULT 0, line_count INTEGER NOT NULL DEFAULT 0, balanced INTEGER NOT NULL DEFAULT 1,
        source TEXT NOT NULL DEFAULT '', photo_ref TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        UNIQUE(shop, bought_on, total_p));
      CREATE TABLE receipt_lines(
        id INTEGER PRIMARY KEY, receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE, line_no INTEGER NOT NULL,
        raw_name TEXT NOT NULL, key TEXT NOT NULL, item_id TEXT, qty REAL NOT NULL DEFAULT 1, unit_price_p INTEGER NOT NULL,
        line_total_p INTEGER NOT NULL, discount_p INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0, category TEXT NOT NULL DEFAULT 'Other');
      CREATE INDEX receipt_lines_key ON receipt_lines(key);
      CREATE INDEX receipt_lines_receipt ON receipt_lines(receipt_id);
      CREATE TABLE item_aliases(alias TEXT PRIMARY KEY, item_id TEXT NOT NULL) WITHOUT ROWID;'''),
]
CATEGORIES = ('Meat and fish', 'Fruit and veg', 'Treats and snacks', 'Pantry and cupboard', 'Dairy and eggs', 'Pet food',
              'Ready meals and pizza', 'Drinks', 'Bakery', 'Household', 'Clothing', 'Other')
BALANCE_TOLERANCE_P = 5      # lines may differ from the balance due by this many pence (rounding) and still count as balanced
MAX_LINES = 400
FORBIDDEN_KEYS = ('card', 'pan', 'auth', 'merchant', 'tid', 'icc', 'aid')


class LedgerError(ValueError):
    pass


def q(d, sql, params=()):
    """Rows as dicts (column name -> value)."""
    cur = d.con.execute(sql, params)
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def open_ledger(path, **kw):
    return ihdb.open_db(path, MIGRATIONS, **kw)


def pence(v):
    """£ amount (number or '3.25' text) -> integer pence. Negative allowed (discounts as written on the till)."""
    try:
        return int(round(float(v) * 100))
    except (TypeError, ValueError):
        raise LedgerError('not an amount: %r' % (v,))


def norm_name(raw):
    t = re.sub(r'[^a-z0-9 ]+', ' ', str(raw or '').lower())
    return re.sub(r'\s+', ' ', t).strip()[:80]


def add_alias(d, raw_name, item_id):
    """Teach the ledger that a till name is an item; existing lines with that name are re-keyed."""
    a = norm_name(raw_name)
    if not a or not item_id:
        raise LedgerError('alias and item id needed')
    with d.tx() as con:
        con.execute('INSERT OR REPLACE INTO item_aliases(alias, item_id) VALUES(?,?)', (a, str(item_id)))
        con.execute("UPDATE receipt_lines SET key=?, item_id=? WHERE key=?", (str(item_id), str(item_id), 'raw:' + a))
    return a


def _key(con, raw):
    a = norm_name(raw)
    row = con.execute('SELECT item_id FROM item_aliases WHERE alias=?', (a,)).fetchone()
    return (row[0], row[0]) if row else ('raw:' + a, None)


def store_receipt(d, rec, now_iso=None):
    """rec = {shop, branch?, bought_on 'YYYY-MM-DD', total (£), savings? (£), source?, photo_ref?, lines:[{raw_name, qty?, unit_price (£), line_total (£),
    discount? (£, positive = money off), cancelled? (bool), category?}]}. -> ('stored'|'duplicate', receipt_id, balanced)."""
    if not isinstance(rec, dict):
        raise LedgerError('receipt must be an object')
    if any(k in rec for k in FORBIDDEN_KEYS) or any(k in ln for ln in (rec.get('lines') or []) if isinstance(ln, dict) for k in FORBIDDEN_KEYS):
        raise LedgerError('payment details are not accepted')
    shop = str(rec.get('shop') or '').strip()[:60]
    day = str(rec.get('bought_on') or '')
    if not shop or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        raise LedgerError('shop and bought_on (YYYY-MM-DD) needed')
    lines = rec.get('lines') or []
    if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
        raise LedgerError('lines: 1 to %d needed' % MAX_LINES)
    total = pence(rec.get('total'))
    prepared, kept = [], 0
    for i, ln in enumerate(lines, 1):
        if not isinstance(ln, dict) or not norm_name(ln.get('raw_name')):
            raise LedgerError('line %d has no name' % i)
        cancelled = bool(ln.get('cancelled'))
        lt, disc = pence(ln.get('line_total')), pence(ln.get('discount') or 0)
        qty = float(ln.get('qty') or 1)
        if qty <= 0:
            raise LedgerError('line %d: qty must be above 0' % i)
        up = pence(ln['unit_price']) if ln.get('unit_price') is not None else int(round(lt / qty))
        cat = ln.get('category') if ln.get('category') in CATEGORIES else 'Other'
        if not cancelled:
            kept += lt - disc
        prepared.append((i, str(ln['raw_name']).strip()[:120], qty, up, lt, disc, 1 if cancelled else 0, cat))
    balanced = 1 if abs(kept - total) <= BALANCE_TOLERANCE_P else 0
    with d.tx() as con:
        dup = con.execute('SELECT id FROM receipts WHERE shop=? AND bought_on=? AND total_p=?', (shop, day, total)).fetchone()
        if dup:
            return 'duplicate', dup[0], bool(balanced)
        cur = con.execute('INSERT INTO receipts(shop, branch, bought_on, total_p, savings_p, line_count, balanced, source, photo_ref, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (shop, str(rec.get('branch') or '')[:60], day, total, pence(rec.get('savings') or 0), len(prepared), balanced,
                           str(rec.get('source') or '')[:40], str(rec.get('photo_ref') or '')[:80], now_iso or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')))
        rid = cur.lastrowid
        for i, raw, qty, up, lt, disc, canc, cat in prepared:
            key, item_id = _key(con, raw)
            con.execute('INSERT INTO receipt_lines(receipt_id, line_no, raw_name, key, item_id, qty, unit_price_p, line_total_p, discount_p, cancelled, category) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (rid, i, raw, key, item_id, qty, up, lt, disc, canc, cat))
    return 'stored', rid, bool(balanced)
