import datetime as dt
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import main
import store
import delivery


class MoneyTests(unittest.TestCase):
    def setUp(self):
        self.db = store.open_store(':memory:')
        self.c = {'id': 'vehicle', 'name': 'Vehicle lease', 'kind': 'car lease', 'start': '2024-01-01',
                  'end': '2027-05-30', 'monthly_payment': 100, 'payment_day': 30,
                  'balance_snapshot': {'amount': 250, 'as_of': '2026-11-16'},
                  'settlement_quote': {'amount': 240, 'as_of': '2026-11-16'}}

    def tearDown(self):
        self.db.close()

    def test_projection_and_short_month(self):
        self.assertEqual(store.project(self.c, dt.date(2026, 11, 16))['remaining_payments'], 3)
        self.assertEqual(store.project(self.c, dt.date(2026, 11, 30))['projected_balance'], 150)
        self.assertEqual(store.project(self.c, dt.date(2027, 2, 28))['projected_balance'], 0)

    def test_import_is_atomic_and_preserves_sent(self):
        with self.assertRaises((sqlite3.IntegrityError, ValueError)):
            store.import_once(self.db, [self.c, self.c], {})
        self.assertEqual(store.records(self.db, dt.date(2026, 11, 16)), [])
        store.import_once(self.db, [self.c], {'vehicle:6': '2026-11-30'})
        self.assertEqual(store.due(self.db, dt.date(2026, 11, 30)), [])
        with self.assertRaises(ValueError):
            store.import_once(self.db, [self.c], {})

    def test_auth_and_missing_history(self):
        app = main.App(self.db, 'secret', today=lambda: dt.date(2026, 11, 16))
        self.assertEqual(app.route('GET', '/healthz')[0], 200)
        self.assertEqual(app.route('GET', '/contracts')[0], 401)
        self.assertEqual(app.route('POST', '/import', 'Bearer secret', {'contracts': [self.c]})[0], 400)
        self.assertEqual(app.route('POST', '/import', 'Bearer secret', {'contracts': [self.c], 'sent': {}})[0], 200)
        self.assertEqual(app.route('GET', '/contracts', 'Bearer secret')[1]['contracts'][0]['settlement_quote']['amount'], 240)

    def test_invalid_amounts_and_dates(self):
        for amount in ('NaN', -1, '0.001'):
            with self.assertRaises(ValueError):
                store.money(amount)
        c = dict(self.c, payment_day=0)
        with self.assertRaises(ValueError):
            store.validate(c)

    def test_dashboard_fields(self):
        store.import_once(self.db, [self.c], {})
        rows = store.records(self.db, dt.date(2026, 11, 16))
        self.assertEqual(rows[0]['days_left'], 195)
        self.assertEqual(rows[0]['reminders'], ['2026-11-30', '2027-02-28'])
        self.assertIn('Vehicle lease', store.markdown(rows))

    def test_reminders_preserve_history_and_do_not_repeat_uncertain_delivery(self):
        c = dict(self.c, who=['first', 'second'])
        store.import_once(self.db, [c], {})
        calls = []
        def send(persona, text):
            calls.append(persona)
            return persona == 'persona_one'
        today = dt.date(2026, 11, 30)
        delivery.run(self.db, today, {'first': 'persona_one', 'second': 'persona_two'}, send)
        delivery.run(self.db, today, {'first': 'persona_one', 'second': 'persona_two'}, send)
        self.assertEqual(calls, ['persona_one', 'persona_two'])
        self.assertEqual(dict(self.db.execute('SELECT recipient, status FROM deliveries')), {'first': 'sent', 'second': 'uncertain'})
        app = main.App(self.db, 'secret', today=lambda: today)
        self.assertEqual(app.route('POST', '/deliveries/resolve', 'Bearer secret',
            {'key': 'vehicle:6', 'recipient': 'second', 'resolution': 'sent'})[0], 200)
        delivery.run(self.db, today, {'first': 'persona_one', 'second': 'persona_two'}, send)
        self.assertEqual(store.due(self.db, today), [])
        self.assertEqual(calls, ['persona_one', 'persona_two'])

    def test_imported_reminder_is_not_sent(self):
        store.import_once(self.db, [dict(self.c, who=['first'])], {'vehicle:6': '2026-11-30'})
        calls = []
        delivery.run(self.db, dt.date(2026, 11, 30), {'first': 'persona'}, lambda *args: calls.append(args))
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
