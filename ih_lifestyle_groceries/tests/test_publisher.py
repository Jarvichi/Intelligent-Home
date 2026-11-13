import os, sys, tempfile
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import ledger as L, publisher as P, main as M


class FakeHA:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail
    def post(self, path, data=None, timeout=30):
        if self.fail:
            raise RuntimeError('down')
        self.calls.append((path, data))


def mk():
    return L.open_ledger(os.path.join(tempfile.mkdtemp(), 'ih.db'))


def rec(day, price):
    return {'shop': 'Shop', 'bought_on': day, 'total': price, 'lines': [{'raw_name': 'CAT BISCUITS', 'unit_price': price, 'line_total': price, 'category': 'Pet food'}]}


def test_build_pounds_and_counts():
    d = mk()
    for day in ('2026-09-01', '2026-09-22', '2026-10-13', '2026-11-03'):
        L.store_receipt(d, rec(day, 3.0))
    out = P.build(d, date(2026, 11, 20))
    st, at = out[P.SPEND]
    assert st == 3.0 and at['weeks'][-1]['categories'] == {'Pet food': 3.0} and at['receipts'] == 4
    assert out[P.DUE][0] == 1 and out[P.DUE][1]['items'][0]['name'] == 'CAT BISCUITS'
    assert out[P.PRICES][0] == 0


def test_receipt_triggers_publish_and_failure_is_harmless():
    ha = FakeHA()
    a = M.App(mk(), 'live', 'tok', today=lambda: date(2026, 11, 20), ha=ha)
    assert a.route('POST', '/receipts', {}, rec('2026-11-13', 3.0), 'Bearer tok')[1]['status'] == 'stored'
    assert [p for p, _ in ha.calls] == ['states/' + P.SPEND, 'states/' + P.DUE, 'states/' + P.PRICES]
    assert a.route('POST', '/receipts', {}, rec('2026-11-13', 3.0), 'Bearer tok')[1]['status'] == 'duplicate'
    assert len(ha.calls) == 3                      # a duplicate does not republish
    bad = M.App(mk(), 'live', 'tok', ha=FakeHA(fail=True))
    assert bad.route('POST', '/receipts', {}, rec('2026-11-13', 3.0), 'Bearer tok')[0] == 200   # HA down does not fail the receipt


def test_dry_never_publishes():
    ha = FakeHA()
    a = M.App(mk(), 'dry', 'tok', ha=ha)
    a.route('POST', '/receipts', {}, rec('2026-11-13', 3.0), 'Bearer tok')
    assert ha.calls == []
