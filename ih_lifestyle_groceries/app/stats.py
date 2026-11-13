"""Questions the receipts ledger answers (13 Nov 2026): price changes per product, why a week was cheaper, when a product is due again.
Read-only. Money in pence; weeks run Monday to Sunday. Cancelled lines never count. Price changes use the LIST (shelf) price; promotions are reported separately as net_pct, so a 2-for-1 week is not read as a price rise."""
import statistics
from ledger import q
from datetime import date, timedelta

PRICE_WINDOW_DAYS = 90
PRICE_MIN_PCT = 5.0
DUE_MIN_PURCHASES = 3        # two gaps at least before a cadence is trusted
DUE_OK_GAPS = 4              # gaps for 'ok' confidence, fewer = 'low'
DUE_SOON_DAYS = 7
DUE_MAX_GAP_DAYS = 120       # a gap longer than this is a break (holiday, stopped buying), not a cadence


def _d(s):
    return date.fromisoformat(s)


def monday(day):
    return day - timedelta(days=day.weekday())


def _unit_net(row):
    qty = row['qty'] or 1
    return (row['line_total_p'] - row['discount_p']) / qty


def _unit_list(row):
    qty = row['qty'] or 1
    return row['line_total_p'] / qty


def price_changes(d, today, window_days=PRICE_WINDOW_DAYS, min_pct=PRICE_MIN_PCT):
    """Per product bought on 2+ receipts in the window: first and last LIST unit price, % change (and net_pct after promotions), biggest rises first. Only |change| >= min_pct."""
    since = (today - timedelta(days=window_days)).isoformat()
    rows = q(d, "SELECT l.key, l.raw_name, l.qty, l.line_total_p, l.discount_p, r.bought_on FROM receipt_lines l JOIN receipts r ON r.id=l.receipt_id "
                   "WHERE l.cancelled=0 AND r.bought_on>=? AND r.bought_on<=? ORDER BY r.bought_on, l.id", (since, today.isoformat()))
    by = {}
    for r in rows:
        by.setdefault(r['key'], []).append(r)
    out = []
    for key, rs in by.items():
        days = sorted({r['bought_on'] for r in rs})
        if len(days) < 2:
            continue
        first = [r for r in rs if r['bought_on'] == days[0]]
        last = [r for r in rs if r['bought_on'] == days[-1]]
        a, b = _unit_list(first[0]), _unit_list(last[-1])
        if a <= 0:
            continue
        pct = (b - a) / a * 100.0
        na, nb = _unit_net(first[0]), _unit_net(last[-1])
        net_pct = (nb - na) / na * 100.0 if na > 0 else 0.0
        if abs(pct) >= min_pct:
            out.append({'key': key, 'name': last[-1]['raw_name'], 'first_day': days[0], 'last_day': days[-1],
                        'first_p': round(a), 'last_p': round(b), 'pct': round(pct, 1), 'net_pct': round(net_pct, 1)})
    return sorted(out, key=lambda x: -x['pct'])


def weeks(d, today, n=12, exclude=('Clothing',)):
    """One row per Monday-start week for the last n weeks that HAVE a receipt: total, savings, per-category net spend. A missing week is not a £0 week."""
    start = monday(today) - timedelta(weeks=n - 1)
    rows = q(d, "SELECT r.id, r.bought_on, r.savings_p, l.category, l.line_total_p, l.discount_p FROM receipts r JOIN receipt_lines l ON l.receipt_id=r.id "
                   "WHERE l.cancelled=0 AND r.bought_on>=? AND r.bought_on<=?", (start.isoformat(), today.isoformat()))
    out, seen = {}, set()
    for r in rows:
        if r['category'] in exclude:
            continue
        w = monday(_d(r['bought_on'])).isoformat()
        e = out.setdefault(w, {'week': w, 'total_p': 0, 'savings_p': 0, 'categories': {}})
        net = r['line_total_p'] - r['discount_p']
        e['total_p'] += net
        e['categories'][r['category']] = e['categories'].get(r['category'], 0) + net
        if r['id'] not in seen:
            seen.add(r['id']); e['savings_p'] += r['savings_p']
    return [out[k] for k in sorted(out)]


def week_diff(d, week_a, week_b):
    """Why week_b cost more or less than week_a (Monday dates 'YYYY-MM-DD'): change per category, change in savings, products bought in only one week."""
    today = _d(week_b) + timedelta(days=6)
    allw = {w['week']: w for w in weeks(d, max(today, _d(week_a) + timedelta(days=6)), n=60, exclude=())}
    a, b = allw.get(week_a), allw.get(week_b)
    if not a or not b:
        raise ValueError('no receipt in one of the weeks')
    cats = sorted(set(a['categories']) | set(b['categories']))
    by_cat = sorted(({'category': c, 'a_p': a['categories'].get(c, 0), 'b_p': b['categories'].get(c, 0), 'diff_p': b['categories'].get(c, 0) - a['categories'].get(c, 0)} for c in cats),
                    key=lambda x: -abs(x['diff_p']))
    def keys(w):
        s = _d(w).isoformat(); e = (_d(w) + timedelta(days=6)).isoformat()
        return {r['key']: r['raw_name'] for r in q(d, "SELECT l.key, l.raw_name FROM receipt_lines l JOIN receipts r ON r.id=l.receipt_id WHERE l.cancelled=0 AND r.bought_on BETWEEN ? AND ?", (s, e))}
    ka, kb = keys(week_a), keys(week_b)
    return {'total_diff_p': b['total_p'] - a['total_p'], 'savings_diff_p': b['savings_p'] - a['savings_p'], 'by_category': by_cat,
            'only_in_a': sorted(ka[k] for k in set(ka) - set(kb)), 'only_in_b': sorted(kb[k] for k in set(kb) - set(ka))}


def due_items(d, today, soon_days=DUE_SOON_DAYS):
    """Products with 3+ purchase days: median gap between purchases, next due = last bought + median gap, days until (negative = overdue).
    Gaps above DUE_MAX_GAP_DAYS are ignored. Suggestions only, never added to a list automatically."""
    rows = q(d, "SELECT l.key, l.raw_name, r.bought_on FROM receipt_lines l JOIN receipts r ON r.id=l.receipt_id WHERE l.cancelled=0 ORDER BY r.bought_on")
    by, names = {}, {}
    for r in rows:
        by.setdefault(r['key'], set()).add(r['bought_on']); names[r['key']] = r['raw_name']
    out = []
    for key, days in by.items():
        ds = sorted(_d(x) for x in days)
        if len(ds) < DUE_MIN_PURCHASES:
            continue
        gaps = [(b - a).days for a, b in zip(ds, ds[1:]) if 0 < (b - a).days <= DUE_MAX_GAP_DAYS]
        if len(gaps) < DUE_MIN_PURCHASES - 1:
            continue
        med = statistics.median(gaps)
        due = ds[-1] + timedelta(days=round(med))
        out.append({'key': key, 'name': names[key], 'last': ds[-1].isoformat(), 'every_days': med, 'due': due.isoformat(), 'in_days': (due - today).days,
                    'purchases': len(ds), 'confidence': 'ok' if len(gaps) >= DUE_OK_GAPS else 'low'})
    return sorted([x for x in out if x['in_days'] <= soon_days], key=lambda x: x['in_days'])
