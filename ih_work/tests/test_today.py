"""Today card: whitelisted fields, rows, footnote, stale snapshot, corrupt file, no secrets."""
import json, os, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import today
from store import Store

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
SECRET = 'sk-SECRET-INSTRUCTION'


def item(seq, status, title=None, finished=None, **k):
    d = dict(id='i%d' % seq, seq=seq, kind='other', ref='r', title=title or 'Job %d' % seq, instruction=SECRET, approved=False, status=status,
             queued_at=NOW.isoformat(), claimed_by='runner-secret-name', result_summary=SECRET, error=SECRET, finished_at=finished)
    d.update(k)
    return d


def snap(state='waiting for pace', reason='day band: pace -3.2, above -4, nothing runs', at=NOW):
    return {'worker': {'state': state, 'reason': reason, 'band': 'day'}, 'at': at.isoformat()}


class T(unittest.TestCase):
    def rows(self, out):
        return {r['name']: r for r in out['rows']}

    def test_empty_queue(self):
        out = today.build([], snap('idle', 'queue empty'), NOW)
        r = self.rows(out)
        self.assertEqual((r['Done today']['value'], r['Failed today']['value']), ('0', '0'))
        self.assertEqual(out['footnote'], 'queue empty')

    def test_rows(self):
        its = [item(1, 'running', 'Now job'), item(2, 'queued'), item(3, 'queued'), item(4, 'done', finished=(NOW - timedelta(hours=1)).isoformat()),
               item(5, 'done', finished=(NOW - timedelta(days=1)).isoformat()), item(6, 'failed', finished=NOW.isoformat())]
        out = today.build(its, snap(), NOW)
        names = [r['name'] for r in out['rows']]
        self.assertEqual(names[:3], ['Now job', 'Job 2', 'Job 3'])
        r = self.rows(out)
        self.assertEqual(r['Now job']['state'], 'running')
        self.assertEqual(r['Job 2']['note'], 'queued #2 / runs when the pace is low enough')
        self.assertEqual((r['Done today']['value'], r['Failed today']['value'], r['Failed today']['state']), ('1', '1', 'failed'))
        self.assertIn('nothing runs', out['footnote'])

    def test_only_five_queued_shown(self):
        out = today.build([item(n, 'queued') for n in range(1, 9)], snap(), NOW)
        self.assertEqual(len([r for r in out['rows'] if r.get('state') == 'queued']), 5)
        self.assertIn('3 more waiting', [r['name'] for r in out['rows']])

    def test_ready_first_row(self):
        out = today.build([item(1, 'queued')], snap('ready', 'day band: pace -6.0, draining, stops at -4'), NOW)
        self.assertEqual(out['rows'][0]['note'], 'queued #1 / ready to run')

    def test_stale_snapshot(self):
        out = today.build([], snap(at=NOW - timedelta(minutes=10)), NOW)
        self.assertTrue(out['footnote'].startswith('Status is old'))
        self.assertTrue(today.build([], None, NOW)['footnote'].startswith('No status yet'))

    def test_no_secrets(self):
        out = json.dumps(today.build([item(1, 'running'), item(2, 'queued'), item(3, 'failed', finished=NOW.isoformat())], snap(), NOW))
        for bad in (SECRET, 'runner-secret-name', 'instruction', 'error', 'result'):
            self.assertNotIn(bad, out)

    def test_fetch_from_files_and_corrupt(self):
        d = tempfile.mkdtemp(); s = Store(d)
        s.enqueue('other', 'a', 'Real job', 'do')
        with open(os.path.join(d, 'items', '000009-bad.json'), 'w') as f:
            f.write('{nope')
        Store.write_json(os.path.join(d, 'state.json'), {'snapshot': snap('idle', 'x')})
        today._cache.update(t=0.0, v=None)
        out = today.today(lambda: today.fetch(d), lambda: NOW.timestamp(), 'UTC')
        self.assertIn('Real job', [r['name'] for r in out['rows']])

    def test_failure_is_none_and_cached(self):
        today._cache.update(t=0.0, v=None)
        def boom():
            raise OSError('x')
        self.assertIsNone(today.today(boom, lambda: NOW.timestamp(), 'UTC'))

    def test_local_day_uses_timezone(self):
        late = datetime(2026, 11, 5, 23, 30, tzinfo=timezone.utc)
        tz = timezone(timedelta(hours=2))
        its = [item(1, 'done', finished=late.isoformat())]
        self.assertEqual(self.rows(today.build(its, snap(), NOW, tz))['Done today']['value'], '0')
        self.assertEqual(self.rows(today.build(its, snap(), late, tz))['Done today']['value'], '1')


if __name__ == '__main__':
    unittest.main()
