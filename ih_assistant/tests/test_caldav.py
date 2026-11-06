import datetime, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import caldav as C
from store import PersonaStore
from main import Assistant

ICS = ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:ABC-123\r\nSUMMARY:Vet\\, Nacho\r\n'
       'DTSTART;TZID=Europe/London:20261107T150000\r\nDTEND;TZID=Europe/London:20261107T160000\r\nSEQUENCE:2\r\n'
       'DESCRIPTION:long line that is\r\n  folded\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
NOW = datetime.datetime(2026, 11, 6, 9, 0, tzinfo=datetime.timezone.utc)


class IcsTests(unittest.TestCase):
    def test_edit_title_and_time(self):
        out = C.edit_ics(ICS, {'summary': 'Vet; Nacho, jab', 'dtstart': ('utc', datetime.datetime(2026, 11, 7, 16, 0, tzinfo=datetime.timezone.utc)),
                               'dtend': ('utc', datetime.datetime(2026, 11, 7, 17, 0, tzinfo=datetime.timezone.utc))}, NOW)
        self.assertIn('SUMMARY:Vet\; Nacho\\, jab', out)
        self.assertIn('DTSTART:20261107T160000Z', out)
        self.assertNotIn('TZID', out)
        self.assertIn('SEQUENCE:3', out)
        self.assertIn('DTSTAMP:20261106T090000Z', out)
        self.assertIn('UID:ABC-123', out)
        self.assertIn('DESCRIPTION:long line that is folded', out)   # unfolded, kept

    def test_all_day_and_clearing_a_field(self):
        out = C.edit_ics(ICS, {'dtstart': ('date', datetime.date(2026, 12, 1)), 'dtend': ('date', datetime.date(2026, 12, 2)), 'description': ''}, NOW)
        self.assertIn('DTSTART;VALUE=DATE:20261201', out)
        self.assertNotIn('DESCRIPTION', out)

    def test_recurring_is_refused(self):
        with self.assertRaises(C.CalDAVError):
            C.edit_ics(ICS.replace('SEQUENCE:2', 'RRULE:FREQ=WEEKLY'), {'summary': 'x'}, NOW)


class FakeServer:
    def __init__(self, ics=ICS): self.ics, self.puts, self.dels, self.finds = ics, [], [], 0
    def find(self, entity, uid):
        self.finds += 1
        if uid != 'ABC-123':
            raise C.CalDAVError('event not found')
        return 'https://x/h.ics', '"e1"', self.ics
    def put(self, href, ics, etag): self.puts.append((href, ics, etag))
    def delete(self, href, etag): self.dels.append((href, etag))


class AssistantTests(unittest.TestCase):
    def make(self, live=True, ics=ICS, user='u'):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory(); self.srv = FakeServer(ics)
        a.opts = {'mode': 'live' if live else 'dry', 'caldav_user': user, 'caldav_password': 'p'}; a.log = lambda *x: None
        a.clock = lambda: 1793955600.0; a.tz = None; a._caldav = self.srv; a.caldav_factory = None
        a.cfg = {'aa': {'live': True, 'calendar_write': ['calendar.mine'], 'calendars': [], 'calendar_read': []}}
        a.stores = {'aa': PersonaStore(self.tmp.name, 'aa', a.clock)}
        return a

    def body(self, **k):
        b = {'calendar': 'calendar.mine', 'uid': 'ABC-123', 'summary_was': 'vet, nacho'}; b.update(k); return b

    def test_edit_changes_and_puts_with_etag(self):
        a = self.make()
        c, r = a.calendar_edit('aa', self.body(start='2026-11-07 16:00', end='2026-11-07 17:00', summary='Vet jab'))
        self.assertEqual((c, r['changed']), (200, True))
        href, ics, etag = self.srv.puts[0]
        self.assertEqual(etag, '"e1"')
        self.assertIn('DTSTART:20261107T160000Z', ics); self.assertIn('SUMMARY:Vet jab', ics)

    def test_wrong_name_wrong_calendar_and_no_login_are_refused(self):
        a = self.make()
        c, r = a.calendar_edit('aa', self.body(summary_was='Dentist', summary='x'))
        self.assertEqual((c, r['current']), (409, 'Vet, Nacho'))
        self.assertEqual(a.calendar_delete('aa', self.body(summary_was=''))[0], 409)
        self.assertEqual(a.calendar_delete('aa', self.body(calendar='calendar.fam'))[0], 403)
        self.assertEqual(a.calendar_delete('aa', self.body(uid='nope1'))[0], 404)
        b = self.make(user=''); b._caldav = None
        self.assertEqual(b.calendar_delete('aa', self.body())[0], 503)
        self.assertEqual((self.srv.puts, self.srv.dels), ([], []))

    def test_start_without_end_and_empty_change(self):
        a = self.make()
        self.assertEqual(a.calendar_edit('aa', self.body(start='2026-11-07 16:00'))[0], 400)
        self.assertEqual(a.calendar_edit('aa', self.body())[0], 400)

    def test_delete_and_dry_run_and_recurring(self):
        a = self.make()
        c, r = a.calendar_delete('aa', self.body())
        self.assertEqual((c, r['deleted'], self.srv.dels), (200, True, [('https://x/h.ics', '"e1"')]))
        a = self.make(live=False)
        self.assertEqual(a.calendar_delete('aa', self.body())[1]['dry'], True)
        self.assertEqual(self.srv.dels, [])
        a = self.make(ics=ICS.replace('SEQUENCE:2', 'RRULE:FREQ=WEEKLY'))
        self.assertEqual(a.calendar_delete('aa', self.body())[0], 400)
        self.assertEqual(a.calendar_edit('aa', self.body(summary='x'))[0], 400)


if __name__ == '__main__':
    unittest.main()
