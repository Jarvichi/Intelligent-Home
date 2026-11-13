import json, os, sys, tempfile, threading, urllib.request
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import ledger as L, main as M

REC = {'shop': 'Shop', 'bought_on': '2026-11-13', 'total': 4.0, 'lines': [{'raw_name': 'CAT BISCUITS', 'unit_price': 4.0, 'line_total': 4.0, 'category': 'Pet food'}]}


def app(mode='dry', token='tok'):
    return M.App(L.open_ledger(os.path.join(tempfile.mkdtemp(), 'ih.db')), mode, token, today=lambda: date(2026, 11, 20))


def test_post_needs_token():
    a = app()
    assert a.route('POST', '/receipts', {}, REC, None)[0] == 401
    assert a.route('POST', '/receipts', {}, REC, 'Bearer wrong')[0] == 401
    assert app(token='').route('POST', '/receipts', {}, REC, 'Bearer ')[0] == 401      # no token configured = POST refused


def test_dry_writes_nothing_live_stores():
    a = app('dry')
    code, r = a.route('POST', '/receipts', {}, REC, 'Bearer tok')
    assert code == 200 and r['dry'] and r['would_store'] == 1
    assert a.route('GET', '/healthz', {}, {}, None)[1]['receipts'] == 0
    b = app('live')
    code, r = b.route('POST', '/receipts', {}, REC, 'Bearer tok')
    assert code == 200 and r['status'] == 'stored' and r['balanced']
    assert b.route('POST', '/receipts', {}, REC, 'Bearer tok')[1]['status'] == 'duplicate'
    assert b.route('GET', '/healthz', {}, {}, None)[1]['lines'] == 1


def test_bad_receipt_and_payment_details_are_400():
    b = app('live')
    assert b.route('POST', '/receipts', {}, dict(REC, card='1234'), 'Bearer tok')[0] == 400
    assert b.route('POST', '/receipts', {}, {'shop': 'x'}, 'Bearer tok')[0] == 400
    assert app('dry').route('POST', '/receipts', {}, dict(REC, pan='1'), 'Bearer tok')[0] == 400


def test_stats_routes_and_http_server():
    b = app('live')
    for day in ('2026-09-01', '2026-09-22', '2026-10-13', '2026-11-03'):
        b.route('POST', '/receipts', {}, dict(REC, bought_on=day), 'Bearer tok')
    srv = M.make_server(b, port=0, host='127.0.0.1')
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d' % srv.server_address[1]
    due = json.load(urllib.request.urlopen(base + '/stats/due'))['due']
    assert due and due[0]['name'] == 'CAT BISCUITS' and due[0]['in_days'] == 4
    req = urllib.request.Request(base + '/receipts', data=json.dumps(dict(REC, bought_on='2026-11-14')).encode(), headers={'Authorization': 'Bearer tok'})
    assert json.load(urllib.request.urlopen(req))['status'] == 'stored'
    srv.shutdown()
