import datetime, os, sys, tempfile, unittest, zoneinfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import reminders as R
from store import PersonaStore
from main import Assistant

D = datetime.datetime


class Pure(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(R.parse_local('2026-11-12 09:30'), D(2026, 11, 12, 9, 30))
        for bad in ('2026-11-12', '2026-02-30 09:00', '12/11/2026 09:00', '', None, '2026-11-12 25:00'):
            self.assertIsNone(R.parse_local(bad))

    def test_repeat_advance(self):
        d = D(2026, 11, 12, 9, 0)
        self.assertEqual(R.next_occurrence(d, 'daily'), D(2026, 11, 13, 9, 0))
        self.assertEqual(R.next_occurrence(d, 'weekly'), D(2026, 11, 19, 9, 0))
        self.assertEqual(R.next_occurrence(d, 'monthly'), D(2026, 12, 12, 9, 0))
        self.assertEqual(R.next_occurrence(D(2026, 12, 12, 9, 0), 'monthly'), D(2027, 1, 12, 9, 0))
        self.assertEqual(R.next_occurrence(d, 'yearly'), D(2027, 11, 12, 9, 0))
        self.assertIsNone(R.next_occurrence(d, 'none'))

    def test_month_end_clamp(self):
        self.assertEqual(R.next_occurrence(D(2026, 1, 31, 8, 0), 'monthly'), D(2026, 2, 28, 8, 0))
        self.assertEqual(R.next_occurrence(D(2026, 2, 28, 8, 0), 'monthly', 31), D(2026, 3, 31, 8, 0))
        self.assertEqual(R.next_occurrence(D(2028, 2, 29, 8, 0), 'yearly'), D(2029, 2, 28, 8, 0))
        self.assertEqual(R.next_occurrence(D(2026, 12, 31, 8, 0), 'monthly'), D(2027, 1, 31, 8, 0))

    def test_due_and_prune(self):
        items = [{'id': 'a', 'due': '2026-11-12T09:00', 'status': 'pending'}, {'id': 'b', 'due': '2026-11-13T09:00', 'status': 'pending'},
                 {'id': 'c', 'due': '2026-11-01T09:00', 'status': 'done'}]
        self.assertEqual([i['id'] for i in R.due_items(items, D(2026, 11, 12, 9, 0))], ['a'])
        old = {'id': 'o', 'status': 'done', 'fired_at': 0.0}
        self.assertEqual(R.prune([old, {'id': 'p', 'status': 'pending'}], R.DONE_KEEP_S + 5), [{'id': 'p', 'status': 'pending'}])


class Api(unittest.TestCase):
    def make(self, tz='UTC', start=None):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory()
        self.tz = zoneinfo.ZoneInfo(tz)
        self.t = start or D(2026, 11, 11, 10, 0, tzinfo=self.tz).timestamp()
        a.clock = lambda: self.t
        a.tz = self.tz
        self.logs = []
        a.log = lambda *x: self.logs.append(' '.join(str(i) for i in x))
        a.maybe_intro = lambda k: None
        a.cfg = {'aa': {}, 'bb': {}}
        a.stores = {k: PersonaStore(self.tmp.name, k, a.clock) for k in ('aa', 'bb')}
        return a

    def local(self, y, mo, d, h, mi):
        return D(y, mo, d, h, mi, tzinfo=self.tz).timestamp()

    def events(self, a, k='aa'):
        return a.stores[k].read_inbox(0, 50)

    def test_add_list_cancel_and_errors(self):
        a = self.make()
        c, r = a.remind_add('aa', {'when': '2026-11-12 09:00', 'text': 'Call the vet'})
        self.assertEqual(c, 200); rid = r['id']
        self.assertEqual(a.remind_list('aa')[1]['reminders'][0]['text'], 'Call the vet')
        self.assertEqual(a.remind_cancel('aa', {'id': rid})[0], 200)
        self.assertEqual(a.remind_list('aa')[1]['reminders'], [])
        self.assertEqual(a.remind_cancel('aa', {'id': rid})[0], 404)
        self.assertEqual(a.remind_add('zz', {'when': '2026-11-12 09:00', 'text': 'x'})[0], 404)
        self.assertEqual(a.remind_add('aa', {'when': 'soon', 'text': 'x'})[0], 400)
        self.assertEqual(a.remind_add('aa', {'when': '2026-11-11 09:00', 'text': 'x'})[0], 400)   # past
        self.assertEqual(a.remind_add('aa', {'when': '2026-11-11 10:00', 'text': 'x'})[0], 400)   # under min lead
        self.assertEqual(a.remind_add('aa', {'when': '2026-11-12 09:00', 'text': ''})[0], 400)
        self.assertEqual(a.remind_add('aa', {'when': '2026-11-12 09:00', 'text': 'x' * (R.MAX_TEXT + 1)})[0], 400)
        self.assertEqual(a.remind_add('aa', {'when': '2026-11-12 09:00', 'text': 'x', 'repeat': 'hourly'})[0], 400)

    def test_cap(self):
        a = self.make()
        for i in range(R.MAX_PENDING):
            self.assertEqual(a.remind_add('aa', {'when': '2026-11-12 09:00', 'text': 'n%d' % i})[0], 200)
        self.assertEqual(a.remind_add('aa', {'when': '2026-11-12 09:00', 'text': 'more'})[0], 429)
        self.assertEqual(a.remind_add('bb', {'when': '2026-11-12 09:00', 'text': 'other persona'})[0], 200)

    def test_fire_once_and_isolation(self):
        a = self.make()
        a.remind_add('aa', {'when': '2026-11-11 10:30', 'text': 'Secret text'})
        a.remind_add('bb', {'when': '2026-11-11 11:30', 'text': 'Other'})
        self.t = self.local(2026, 11, 11, 10, 31)
        self.assertEqual(a.tick_reminders('aa'), 1)
        self.assertEqual(a.tick_reminders('aa'), 0)
        ev = self.events(a)
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]['type'], ev[0]['source'], ev[0]['facts']['kind']), ('reminder_due', 'reminders', 'reminder'))
        self.assertTrue(ev[0]['suggested_text'].startswith('⏰ Reminder you set ('))
        self.assertIn('Secret text', ev[0]['suggested_text'])
        self.assertNotIn('late', ev[0]['facts'])
        self.assertEqual(self.events(a, 'bb'), [])
        self.assertEqual(a.remind_list('aa')[1]['reminders'], [])
        self.assertFalse(any('Secret text' in l for l in self.logs))

    def test_late_catch_up_and_drop(self):
        a = self.make()
        a.remind_add('aa', {'when': '2026-11-11 11:00', 'text': 'too old'})
        a.remind_add('aa', {'when': '2026-11-11 12:00', 'text': 'catch up'})
        self.t = self.local(2026, 11, 12, 11, 30)   # 24.5 h and 23.5 h overdue
        self.assertEqual(a.tick_reminders('aa'), 1)
        ev = self.events(a)
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]['facts']['late'])
        self.assertIn('catch up', ev[0]['suggested_text'])
        self.assertTrue(any('dropped' in l for l in self.logs))
        self.assertFalse(any('too old' in l or 'catch up' in l for l in self.logs))
        self.assertEqual(a.remind_list('aa')[1]['reminders'], [])

    def test_repeat_fires_and_advances(self):
        a = self.make()
        a.remind_add('aa', {'when': '2026-11-11 10:30', 'text': 'bins', 'repeat': 'daily'})
        self.t = self.local(2026, 11, 11, 10, 31)
        self.assertEqual(a.tick_reminders('aa'), 1)
        self.assertEqual(a.remind_list('aa')[1]['reminders'][0]['due'], '2026-11-12T10:30')
        self.t = self.local(2026, 11, 14, 10, 31)   # 2 days overdue: the 12th is dropped, the 13th too; none fire
        self.assertEqual(a.tick_reminders('aa'), 0)
        self.assertEqual(a.remind_list('aa')[1]['reminders'][0]['due'], '2026-11-15T10:30')

    def test_monthly_anchor_end_of_month(self):
        a = self.make()
        a.remind_add('aa', {'when': '2026-11-30 10:00', 'text': 'rent', 'repeat': 'monthly'})
        self.t = self.local(2026, 11, 30, 10, 1); a.tick_reminders('aa')
        self.assertEqual(a.remind_list('aa')[1]['reminders'][0]['due'], '2026-12-30T10:00')

    def test_dst(self):
        # UK spring forward 2027-03-28: 01:30 does not exist; the reminder fires once when the clock reaches 02:00.
        a = self.make('Europe/London', start=D(2027, 3, 27, 12, 0, tzinfo=zoneinfo.ZoneInfo('Europe/London')).timestamp())
        a.remind_add('aa', {'when': '2027-03-28 01:30', 'text': 'dst'})
        self.t = D(2027, 3, 28, 0, 59, tzinfo=self.tz).timestamp()
        self.assertEqual(a.tick_reminders('aa'), 0)
        self.t = D(2027, 3, 28, 2, 0, tzinfo=self.tz).timestamp()
        self.assertEqual(a.tick_reminders('aa'), 1)
        self.assertEqual(a.tick_reminders('aa'), 0)
        # autumn back 2026-10-25: 01:30 happens twice; once only.
        b = self.make('Europe/London', start=D(2026, 10, 24, 12, 0, tzinfo=zoneinfo.ZoneInfo('Europe/London')).timestamp())
        b.remind_add('aa', {'when': '2026-10-25 01:30', 'text': 'dst2'})
        self.t = D(2026, 10, 25, 1, 40, tzinfo=self.tz, fold=0).timestamp()
        self.assertEqual(b.tick_reminders('aa'), 1)
        self.t = D(2026, 10, 25, 1, 40, tzinfo=self.tz, fold=1).timestamp()
        self.assertEqual(b.tick_reminders('aa'), 0)

    def test_done_items_pruned_after_a_week(self):
        a = self.make()
        a.remind_add('aa', {'when': '2026-11-11 10:30', 'text': 'x'})
        self.t = self.local(2026, 11, 11, 10, 31); a.tick_reminders('aa')
        self.assertEqual(len(a.stores['aa'].state()['reminders']), 1)
        self.t += R.DONE_KEEP_S + 10; a.tick_reminders('aa')
        self.assertEqual(a.stores['aa'].state()['reminders'], [])


if __name__ == '__main__':
    unittest.main()
