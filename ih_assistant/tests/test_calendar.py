import datetime, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
from main import Assistant


class FakeHA:
    def __init__(self): self.calls = []; self.fail = False
    def calendar_events(self, ent, a, b):
        if ent == 'calendar.broken':
            raise OSError('x')
        return [{'summary': 'Vet', 'start': {'dateTime': '2026-11-07T15:00:00+00:00'}, 'end': {'dateTime': '2026-11-07T16:00:00+00:00'}},
                {'summary': 'Bins', 'start': {'date': '2026-11-06'}, 'end': {'date': '2026-11-07'}}] if ent != 'calendar.empty' else []
    def call_service(self, d, s, data):
        if self.fail: raise OSError('no')
        self.calls.append((d, s, data))


class CalTests(unittest.TestCase):
    def make(self, live=True):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory()
        a.opts = {'mode': 'live' if live else 'dry'}; a.log = lambda *x: None; a.clock = lambda: 1793900000.0; a.tz = None
        a.cfg = {'aa': {'live': True, 'calendars': ['calendar.plan'], 'calendar_read': ['calendar.fam', 'calendar.broken'], 'calendar_write': ['calendar.mine']},
                 'bb': {'live': True, 'calendars': [], 'calendar_read': [], 'calendar_write': []}}
        a.stores = {k: PersonaStore(self.tmp.name, k, a.clock) for k in ('aa', 'bb')}
        a.ha = FakeHA()
        return a

    def test_read_merges_sorts_and_flags_unreadable(self):
        c, b = self.make().calendar_view('aa', 7)
        self.assertEqual(c, 200)
        self.assertEqual(b['unreadable'], ['calendar.broken'])
        self.assertEqual(len(b['events']), 6)   # 3 readable calendars x 2 events (one calendar fails)
        self.assertEqual(b['events'][0]['start'], '2026-11-06')
        self.assertTrue(b['events'][0]['all_day'])
        self.assertEqual(b['writable'], ['calendar.mine'])

    def test_read_without_calendars_or_bad_input(self):
        a = self.make()
        self.assertEqual(a.calendar_view('bb')[0], 404)
        self.assertEqual(a.calendar_view('aa', 'x')[0], 400)
        self.assertEqual(a.calendar_view('aa', 5, '2026-13-45')[0], 400)

    def test_add_timed_and_all_day(self):
        a = self.make()
        c, b = a.calendar_add('aa', {'calendar': 'calendar.mine', 'summary': ' Vet  visit ', 'start': '2026-11-07 15:00'})
        self.assertEqual((c, b['added']), (200, True))
        d = a.ha.calls[-1][2]
        self.assertEqual((d['summary'], d['start_date_time'], d['end_date_time']), ('Vet visit', '2026-11-07 15:00:00', '2026-11-07 16:00:00'))
        a.calendar_add('aa', {'calendar': 'calendar.mine', 'summary': 'Trip', 'start': '2026-12-01', 'end': '2026-12-04'})
        d = a.ha.calls[-1][2]
        self.assertEqual((d['start_date'], d['end_date']), ('2026-12-01', '2026-12-04'))

    def test_add_is_limited_to_own_write_list_and_validated(self):
        a = self.make()
        self.assertEqual(a.calendar_add('aa', {'calendar': 'calendar.fam', 'summary': 'x', 'start': '2026-11-07'})[0], 403)   # read-only for aa
        self.assertEqual(a.calendar_add('bb', {'calendar': 'calendar.mine', 'summary': 'x', 'start': '2026-11-07'})[0], 403)   # other persona's calendar
        self.assertEqual(a.calendar_add('aa', {'calendar': 'calendar.mine', 'summary': '', 'start': '2026-11-07'})[0], 400)
        self.assertEqual(a.calendar_add('aa', {'calendar': 'calendar.mine', 'summary': 'x', 'start': 'tomorrow'})[0], 400)
        self.assertEqual(a.ha.calls, [])

    def test_dry_mode_adds_nothing_and_ha_failure_is_reported(self):
        a = self.make(live=False)
        c, b = a.calendar_add('aa', {'calendar': 'calendar.mine', 'summary': 'x', 'start': '2026-11-07'})
        self.assertEqual((c, b['dry'], a.ha.calls), (200, True, []))
        a = self.make(); a.ha.fail = True
        self.assertEqual(a.calendar_add('aa', {'calendar': 'calendar.mine', 'summary': 'x', 'start': '2026-11-07'})[0], 502)


if __name__ == '__main__':
    unittest.main()
