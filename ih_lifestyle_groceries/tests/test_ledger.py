import os, sys, tempfile
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import ledger as L, stats as S


def mk():
    return L.open_ledger(os.path.join(tempfile.mkdtemp(), 'ih.db'))


def rec(day, total, lines, shop='Sainsburys', savings=0):
    return {'shop': shop, 'bought_on': day, 'total': total, 'savings': savings, 'lines': lines}


def ln(name, price, qty=1, disc=0, cat='Other', cancelled=False):
    return {'raw_name': name, 'qty': qty, 'unit_price': price, 'line_total': round(price * qty, 2), 'discount': disc, 'category': cat, 'cancelled': cancelled}


def test_store_whole_receipt_and_dedupe():
    d = mk()
    r = rec('2026-11-13', 8.0, [ln('SHEBA TRAYS X8', 4.75, 1, 1.0, 'Pet food'), ln('JS CHIA SEEDS', 4.25, 1, 0, 'Pantry and cupboard'), ln('VOIDED THING', 3.0, cancelled=True)])
    st, rid, bal = L.store_receipt(d, r)
    assert (st, bal) == ('stored', True)
    assert L.store_receipt(d, r)[0] == 'duplicate'
    assert L.q(d, 'SELECT COUNT(*) c FROM receipt_lines')[0]['c'] == 3        # cancelled line is kept
    assert L.q(d, 'SELECT total_p FROM receipts')[0]['total_p'] == 800


def test_unbalanced_flagged_and_payment_details_refused():
    d = mk()
    st, rid, bal = L.store_receipt(d, rec('2026-11-13', 50.0, [ln('A', 4.0)]))
    assert st == 'stored' and bal is False
    bad = rec('2026-11-14', 4.0, [ln('A', 4.0)]); bad['card'] = '1234'
    try:
        L.store_receipt(d, bad); assert False
    except L.LedgerError:
        pass


def test_alias_rekeys_existing_lines():
    d = mk()
    L.store_receipt(d, rec('2026-11-13', 2.0, [ln('JS CHIA SEEDS', 2.0)]))
    assert L.q(d, 'SELECT key FROM receipt_lines')[0]['key'] == 'raw:js chia seeds'
    L.add_alias(d, 'js chia seeds', 'chia-seeds')
    assert L.q(d, 'SELECT key, item_id FROM receipt_lines')[0]['key'] == 'chia-seeds'
    L.store_receipt(d, rec('2026-11-20', 2.0, [ln('JS CHIA SEEDS', 2.0)]))
    assert L.q(d, "SELECT COUNT(*) c FROM receipt_lines WHERE key='chia-seeds'")[0]['c'] == 2


def test_price_changes():
    d = mk()
    L.store_receipt(d, rec('2026-10-01', 2.0, [ln('OLIVE OIL', 2.0)]))
    L.store_receipt(d, rec('2026-11-01', 2.5, [ln('OLIVE OIL', 2.5)]))
    out = S.price_changes(d, date(2026, 11, 13))
    assert len(out) == 1 and out[0]['pct'] == 25.0 and out[0]['name'] == 'OLIVE OIL'
    assert S.price_changes(d, date(2026, 11, 13), min_pct=30) == []


def test_due_items_cadence():
    d = mk()
    for day in ('2026-09-01', '2026-09-22', '2026-10-13', '2026-11-03'):       # every 21 days
        L.store_receipt(d, rec(day, 3.0, [ln('CAT BISCUITS', 3.0)]))
    L.store_receipt(d, rec('2026-11-03', 5.0, [ln('ONE OFF', 5.0)]))
    out = S.due_items(d, date(2026, 11, 20))
    assert len(out) == 1 and out[0]['name'] == 'CAT BISCUITS' and out[0]['every_days'] == 21 and out[0]['due'] == '2026-11-24' and out[0]['in_days'] == 4
    assert out[0]['confidence'] == 'low'
    assert S.due_items(d, date(2026, 10, 1)) == []     # not yet due soon


def test_weeks_and_week_diff():
    d = mk()
    L.store_receipt(d, rec('2026-11-02', 10.0, [ln('MEAT', 6.0, cat='Meat and fish'), ln('SWEET', 4.0, cat='Treats and snacks')], savings=1.0))
    L.store_receipt(d, rec('2026-11-13', 7.0, [ln('MEAT', 6.0, cat='Meat and fish'), ln('PIZZA', 2.0, cat='Ready meals and pizza'), ln('SHIRT', 9.0, cat='Clothing')], savings=3.0))
    w = S.weeks(d, date(2026, 11, 13))
    assert [x['week'] for x in w] == ['2026-11-02', '2026-11-09'] and w[1]['total_p'] == 800   # clothing left out
    dif = S.week_diff(d, '2026-11-02', '2026-11-09')
    assert dif['savings_diff_p'] == 200 and 'SWEET' in dif['only_in_a'] and 'PIZZA' in dif['only_in_b']
