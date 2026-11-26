import json, os, sys, tempfile, threading, unittest, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'app'))
from ihlib import db as ihdb
import engine as E
import main as M

ROOT = HERE.parents[1]


def mk():
    d = tempfile.mkdtemp()
    return E.Assets(ihdb.open(os.path.join(d, 'ih.db'), E.MIGRATIONS), today=lambda: '2026-11-21'), d


class Engine(unittest.TestCase):
    def test_add_totals_sell_history(self):
        a, _ = mk()
        a.add({'name': 'Example Game', 'category': 'game', 'qty': 2, 'low': 10, 'high': 20})
        a.add({'name': 'Unpriced Disc', 'category': 'dvd'})
        t = a.totals()
        self.assertEqual((t['low'], t['high'], t['count'], t['unpriced']), (20, 40, 3, 1))
        i = [x for x in a.items() if x['name'] == 'Example Game'][0]
        a.sell(i['id'])
        self.assertEqual(a.totals()['high'], 0)
        h = a.history()
        self.assertEqual((len(h), h[0]['high']), (1, 0))
        self.assertEqual(len(a.items(include_sold=True)), 2)

    def test_price_log(self):
        a, _ = mk()
        i = a.add({'name': 'Thing', 'low': 1, 'high': 2})
        a.update(i['id'], {'low': 2, 'high': 3})
        self.assertEqual([(p['low'], p['high']) for p in a.prices(i['id'])], [(1, 2), (2, 3)])

    def test_validation(self):
        a, _ = mk()
        for bad in ({'name': ''}, {'name': 'x', 'category': 'car'}, {'name': 'x', 'low': 5, 'high': 1}, {'name': 'x', 'qty': 0}, {'name': 'x', 'low': -1}, {'name': 'x', 'bought_date': '1 Nov'}):
            with self.assertRaises(E.BadInput):
                a.add(bad)

    def test_import_matches_and_refuses_twice(self):
        a, d = mk()
        inv = os.path.join(d, 'inventory.json')
        hist = os.path.join(d, 'history.csv')
        json.dump({'items': [
            {'name': 'Sample A', 'category': 'game', 'qty': 2, 'low_gbp': 5.0, 'high_gbp': 8.0},
            {'name': 'Sample B', 'category': 'toy', 'low_gbp': None, 'high_gbp': None},
            {'name': 'Sample C', 'category': 'book', 'low_gbp': 1.5, 'high_gbp': 2.5, 'sold_date': '2026-11-01'}]}, open(inv, 'w'))
        open(hist, 'w').write('date,low,high,count\n2026-11-20,10.0,16.0,3\n')
        r = a.import_legacy(inv, hist)
        self.assertEqual((r['items'], r['totals']['low'], r['totals']['high'], r['totals']['unpriced']), (3, 10.0, 16.0, 1))
        self.assertEqual(a.history()[0]['date'], '2026-11-20')
        with self.assertRaises(E.BadInput):
            a.import_legacy(inv)

    def test_barcode(self):
        fake = lambda url: {'items': [{'title': 'Some Game'}]} if 'upcitemdb' in url else {}
        self.assertEqual(E.barcode_lookup('5 0 2', fake)['name'], 'Some Game')
        with self.assertRaises(E.BadInput):
            E.barcode_lookup('abc', fake)

    def test_publish_sensors(self):
        a, _ = mk()
        a.add({'name': 'X', 'low': 1, 'high': 2})
        posted = {}
        class HA:
            def post(self, path, data): posted[path] = data
        a.publish(HA())
        self.assertEqual(posted['states/sensor.assets_low']['state'], 1)
        self.assertEqual(posted['states/sensor.assets_high']['state'], 2)


class Server(unittest.TestCase):
    def setUp(self):
        self.a, self.d = mk()
        fake = lambda url: {'items': [{'title': 'Some Game'}]} if 'upcitemdb' in url else {}
        self.app = {'a': self.a, 'opts': {'mode': 'dry', 'api_token': 'tok', 'interval_min': 60}, 'get': fake}
        self.srvs = []
        self.base = {}
        for ui in (False, True):
            s = M.QuietServer(('127.0.0.1', 0), M.make_handler(self.app, ui, peer='127.0.0.1' if ui else 'x'))
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self.srvs.append(s)
            self.base[ui] = 'http://127.0.0.1:%d' % s.server_port

    def tearDown(self):
        for s in self.srvs:
            s.shutdown()

    def call(self, path, method='GET', body=None, tok='tok', ui=False):
        h = {'Content-Type': 'application/json'}
        if tok and not ui:
            h['Authorization'] = 'Bearer ' + tok
        rq = urllib.request.Request(self.base[ui] + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            r = urllib.request.urlopen(rq)
            raw = r.read()
            return r.status, (json.loads(raw) if 'json' in r.headers['Content-Type'] else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_token_api(self):
        self.assertEqual(self.call('/healthz', tok=None)[0], 200)
        self.assertEqual(self.call('/items', tok=None)[0], 401)
        self.assertEqual(self.call('/items', tok='bad')[0], 401)
        self.app['opts']['api_token'] = ''
        self.assertEqual(self.call('/items', tok='')[0], 401)
        self.app['opts']['api_token'] = 'tok'
        s, it = self.call('/items', 'POST', {'name': 'A', 'category': 'toy', 'low': 1, 'high': 2})
        self.assertEqual(s, 201)
        self.assertEqual(self.call('/items', 'POST', {'name': ''})[0], 400)
        self.assertEqual(self.call('/items/%d' % it['id'], 'PATCH', {'high': 5})[1]['high'], 5)
        self.assertEqual(self.call('/items/999', 'PATCH', {'high': 5})[0], 404)
        self.assertEqual(len(self.call('/items/%d/prices' % it['id'])[1]['prices']), 2)
        s, b = self.call('/barcode', 'POST', {'code': '502', 'add': True, 'category': 'game'})
        self.assertEqual(b['item']['name'], 'Some Game')
        self.assertEqual(self.call('/summary')[1]['totals']['count'], 2)
        self.assertEqual(self.call('/status')[1]['items'], 2)
        self.assertEqual(self.call('/items/%d/sell' % it['id'], 'POST', {})[1]['sold_date'], '2026-11-21')
        self.assertEqual(self.call('/items/%d' % it['id'], 'DELETE')[1], {'deleted': True})
        self.assertEqual(self.call('/items/%d' % it['id'], 'DELETE')[0], 404)
        self.assertEqual(self.call('/')[0], 404)   # the API port serves no page

    def test_ingress_page_and_api(self):
        s, page = self.call('/', ui=True)
        self.assertEqual(s, 200)
        self.assertIn(b'<ih-nav', page)
        for f in ('/ui.js', '/ui.css', '/ih-ui.css', '/ih-ui.js', '/ih-patterns.css'):
            self.assertEqual(self.call(f, ui=True)[0], 200, f)
        self.assertEqual(self.call('/api/summary', ui=True)[0], 200)
        self.assertEqual(self.call('/summary', ui=True)[0], 404)

    def test_ingress_refuses_other_peers(self):
        s = M.QuietServer(('127.0.0.1', 0), M.make_handler(self.app, True, peer='9.9.9.9'))
        threading.Thread(target=s.serve_forever, daemon=True).start()
        try:
            rq = urllib.request.Request('http://127.0.0.1:%d/' % s.server_port)
            with self.assertRaises(urllib.error.HTTPError) as c:
                urllib.request.urlopen(rq)
            self.assertEqual(c.exception.code, 403)
        finally:
            s.shutdown()

    def test_idle_connection_does_not_block_other_requests(self):
        import socket
        idle = socket.create_connection(('127.0.0.1', int(self.base[True].rsplit(':', 1)[1])))   # the ingress gateway keeps idle connections open
        try:
            self.assertEqual(urllib.request.urlopen(self.base[True] + '/healthz', timeout=3).status, 200)
        finally:
            idle.close()


class Contract(unittest.TestCase):
    def test_config_and_ports(self):
        cfg = (HERE.parent / 'config.yaml').read_text()
        for needle in ('boot: auto', 'watchdog: "http://[HOST]:[PORT:8850]/healthz"', 'ingress: true', 'ingress_port: 8851', 'panel_admin: true', 'version: "0.2.0"'):
            self.assertIn(needle, cfg)
        self.assertNotIn('auto_update', cfg.replace('# ', ''))
        self.assertEqual(E.VERSION, '0.2.0')
        self.assertEqual(json.loads((HERE.parent / 'app' / 'manifest.json').read_text())['version'], E.VERSION)

    def test_published_ports_do_not_clash(self):
        import re
        seen = {}
        for p in sorted(ROOT.glob('ih_*/config.yaml')):
            txt = p.read_text()
            for m in re.finditer(r'^  (\d+)/tcp:\s*(\d+)\s*$', txt, re.M):
                seen.setdefault(m.group(2), []).append(p.parent.name)
        self.assertEqual(seen.get('8850'), ['ih_assets'])
        self.assertEqual({k: v for k, v in seen.items() if len(v) > 1}, {})

    def test_ihlib_copy_matches_master(self):
        master = ROOT / 'ihlib'
        for f in master.glob('*.py'):
            self.assertEqual((HERE.parent / 'app' / 'ihlib' / f.name).read_bytes(), f.read_bytes(), f.name)

    def test_ui_assets_in_sync(self):
        ui = HERE.parent / 'app' / 'ui'
        self.assertEqual((ui / 'ih-ui.css').read_bytes(), (ROOT / 'ih_ui' / 'tokens.css').read_bytes())
        self.assertEqual((ui / 'ih-ui.js').read_bytes(), (ROOT / 'ih_ui' / 'components.js').read_bytes())
        self.assertEqual((ui / 'ih-patterns.css').read_bytes(), (ROOT / 'ih_ui' / 'patterns.css').read_bytes())

    def test_page_builds_no_markup_from_text(self):
        js = (HERE.parent / 'app' / 'ui' / 'ui.js').read_text()
        self.assertNotIn('innerHTML', js)

    def test_meta_file(self):
        d = json.loads((ROOT / 'ih_meta' / 'ih_assets.json').read_text())
        self.assertEqual(set(d), {'requires', 'ui'})


if __name__ == '__main__':
    unittest.main()
