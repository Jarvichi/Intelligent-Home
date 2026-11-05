"""Backup 'Today' card: normal, missing, stale, no secrets. No network."""
import json, os, sys, unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import today

SECRET = 'sk-SECRET-TOKEN-123'
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc).timestamp()


def iso(delta_h):
    return (datetime.fromtimestamp(NOW, timezone.utc) + timedelta(hours=delta_h)).isoformat()


def store(used, total, backups, count, upd=-0.5, known=True):
    a = {'backups_gb': backups, 'backups_count': count, 'unit_of_measurement': 'GB', 'friendly_name': 'x', 'updated': iso(upd), 'token': SECRET}
    if known:
        a.update(total_gb=total, used_pct=round(100 * used / total, 1), other_gb=1, free_gb=1)
    else:
        a['disk_known'] = False
    return {'state': str(used), 'attributes': a}


def sensor(h):
    return {'state': iso(h), 'attributes': {'device_class': 'timestamp'}}


def states(**kw):
    s = {today.LOCAL: store(45.1, 443.7, 8.7, 79), today.DRIVE: store(6.3, 15.0, 6.3, 3), today.LAST: sensor(-5), today.NEXT: sensor(19)}
    s.update(kw)
    return s


class T(unittest.TestCase):
    def test_normal(self):
        out = today.build(states(), NOW)
        r = {x['name']: x for x in out['rows']}
        self.assertEqual(r['Local disk']['value'], '10%')
        self.assertEqual(r['Local disk']['note'], '45.1 of 443.7 GB used')
        self.assertEqual(r['Local disk']['model'], '79 backups, 8.7 GB')
        self.assertEqual(r['Google Drive']['note'], '6.3 of 15.0 GB used')
        self.assertEqual(r['Last automatic backup']['value'], '5 h ago')
        self.assertEqual(r['Last automatic backup']['note'], 'Next: in 19 h')
        self.assertEqual(r['Last automatic backup']['state'], 'ok')

    def test_missing(self):
        self.assertIsNone(today.build({today.LOCAL: None, today.DRIVE: None, today.LAST: None, today.NEXT: None}, NOW))
        self.assertIsNone(today.build(None))
        out = today.build(states(**{today.DRIVE: None, today.LAST: {'state': 'unknown', 'attributes': {}}}), NOW)
        r = {x['name']: x for x in out['rows']}
        self.assertEqual(r['Google Drive']['note'], 'Not set up')
        self.assertIn('Not reported', r['Last automatic backup']['note'])

    def test_unknown_disk_size(self):
        out = today.build(states(**{today.LOCAL: store(8.7, 0, 8.7, 79, known=False)}), NOW)
        row = out['rows'][0]
        self.assertEqual(row['note'], 'Disk size not known')
        self.assertNotIn('pct', row)

    def test_stale_and_late(self):
        out = today.build(states(**{today.LOCAL: store(45.1, 443.7, 8.7, 79, upd=-6), today.LAST: sensor(-72)}), NOW)
        r = {x['name']: x for x in out['rows']}
        self.assertEqual(r['Local disk']['state'], 'old')
        self.assertEqual(r['Last automatic backup']['state'], 'late')
        self.assertEqual(r['Last automatic backup']['value'], '3 days ago')

    def test_nearly_full(self):
        out = today.build(states(**{today.LOCAL: store(420, 443.7, 8.7, 79)}), NOW)
        self.assertEqual(out['rows'][0]['state'], 'nearly full')

    def test_no_secrets(self):
        out = json.dumps(today.build(states(), NOW))
        self.assertNotIn(SECRET, out)
        self.assertNotIn('token', out)
        self.assertNotIn('http', out)

    def test_cache_and_failure(self):
        today._cache.update(t=0.0, v=None)
        calls = []
        def f():
            calls.append(1); return states()
        a = today.today(f, lambda: NOW); b = today.today(f, lambda: NOW + 1)
        self.assertIs(a, b); self.assertEqual(len(calls), 1)
        today._cache.update(t=0.0, v=None)
        def boom():
            raise OSError('x')
        self.assertIsNone(today.today(boom, lambda: NOW))

    def test_fetch_states_tolerates_errors(self):
        class H:
            def get(self, p, t=0):
                if 'drive' in p:
                    raise OSError('no')
                return {'state': '1', 'attributes': {}}
        out = today.fetch_states(H())
        self.assertIsNone(out[today.DRIVE]); self.assertIsNotNone(out[today.LOCAL])


if __name__ == '__main__':
    unittest.main()
