import datetime as dt
import sqlite3
import sys
import unittest
import http.client
import json
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import main
import store
import delivery
import ui_server


class MoneyTests(unittest.TestCase):
    def setUp(self):
        self.db = store.open_store(':memory:')
        self.c = {'id': 'vehicle', 'name': 'Vehicle lease', 'kind': 'car lease', 'start': '2024-01-01',
                  'end': '2027-05-30', 'monthly_payment': 100, 'payment_day': 30,
                  'balance_snapshot': {'amount': 250, 'as_of': '2026-11-16'},
                  'settlement_quote': {'amount': 240, 'as_of': '2026-11-16'}}

    def tearDown(self):
        self.db.close()

    def test_insurance_details_survive_edits(self):
        policy = {'id': 'policy', 'name': 'House cover', 'kind': 'house insurance',
                  'provider': 'Example', 'policy_number': 'ABC123', 'notes': 'Buildings and contents'}
        store.save_contract(self.db, policy)
        saved = store.raw_records(self.db)[0]
        saved['end'] = '2027-05-01'
        store.save_contract(self.db, saved)
        self.assertEqual(store.raw_records(self.db)[0], dict(policy, end='2027-05-01'))

    def test_dated_loan_changes_and_overpayment_scenario(self):
        c = {'id': 'loan', 'name': 'Loan', 'kind': 'mortgage', 'monthly_payment': 100,
             'observed_balance': {'amount': 1000, 'as_of': '2026-12'}, 'interest_rate_percent': 0,
             'account_number': 'example', 'loan_changes': [
                 {'date': '2027-01-05', 'type': 'recurring_overpayment', 'value': 50},
                 {'date': '2027-02-01', 'type': 'balance', 'value': 700},
                 {'date': '2027-03-01', 'type': 'rate', 'value': 12},
                 {'date': '2027-03-02', 'type': 'overpayment', 'value': 100},
                 {'date': '2027-04-01', 'type': 'recurring_overpayment', 'value': 0},
                 {'date': '2027-04-02', 'type': 'provider', 'value': 'New lender'}]}
        store.save_contract(self.db, c)
        points = store.projection_series(store.raw_records(self.db)[0])
        self.assertEqual([p['balance'] for p in points[:5]], [1000, 850, 700, 455.5, 360.06])
        self.assertTrue(points[2]['recorded'])
        baseline = dict(c, loan_changes=[x for x in c['loan_changes'] if x['type'] not in ('overpayment', 'recurring_overpayment')])
        self.assertGreater(store.projection_series(baseline)[3]['balance'], points[3]['balance'])
        with self.assertRaises(ValueError):
            store.validate(dict(c, loan_changes=[{'date':'2026-12-01', 'type':'balance', 'value':1000}]))

    def test_new_deal_continues_after_original_end(self):
        c = {'id':'loan', 'name':'Loan', 'kind':'mortgage', 'monthly_payment':100,
             'observed_balance':{'amount':1000, 'as_of':'2027-04'}, 'interest_rate_percent':0,
             'interest_rate_until':'2027-05-31', 'end':'2027-05-31',
             'loan_changes':[{'date':'2027-06-01', 'type':'rate', 'value':12},
                             {'date':'2027-06-01', 'type':'balance', 'value':850}]}
        points = store.projection_series(c)
        self.assertEqual([(p['date'], p['balance']) for p in points[:4]],
                         [('2027-04',1000), ('2027-05',900), ('2027-06',850), ('2027-07',758.5)])

    def test_mortgage_remaining_term_carries_rate_and_compares_overpayments(self):
        c = {'id':'loan', 'name':'Mortgage', 'kind':'mortgage', 'monthly_payment':100,
             'observed_balance':{'amount':1000, 'as_of':'2027-04'}, 'interest_rate_percent':0,
             'interest_rate_until':'2027-05-31', 'mortgage_term_end':'2027-08-31',
             'loan_changes':[{'date':'2027-06-01', 'type':'recurring_overpayment', 'value':50}]}
        agreed = store.mortgage_term_series(c, False)
        plan = store.mortgage_term_series(c, True)
        self.assertEqual([p['date'] for p in agreed], ['2027-04','2027-05','2027-06','2027-07','2027-08'])
        self.assertEqual([p['balance'] for p in agreed], [1000, 900, 800, 700, 600])
        self.assertEqual([p['balance'] for p in plan], [1000, 900, 750, 600, 450])
        self.assertEqual(store.mortgage_term_series(dict(c, mortgage_term_end=''), True), [])

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

    def test_ingress_edit_preserves_contract_fields_and_history(self):
        c = dict(self.c, who=['person'], private_note='keep me', remind_months=[6, 3])
        store.import_once(self.db, [c], {'vehicle:6': '2026-11-30'})
        server = ui_server.make_server(self.db, ['person'], host='127.0.0.1', port=0, peer='127.0.0.1')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        self.addCleanup(conn.close)
        conn.request('GET', '/api/contracts')
        response = conn.getresponse()
        payload = json.loads(response.read())
        raw = payload['contracts'][0]
        self.assertEqual(payload['series']['vehicle'][0], {'date': '2026-11-16', 'balance': 250.0})
        self.assertIsInstance(raw['balance_snapshot'], dict)
        raw['monthly_payment'] = 110
        conn.request('POST', '/api/contracts', json.dumps(raw), {'Content-Type': 'application/json'})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        saved = store.raw_records(self.db)[0]
        self.assertEqual(saved['private_note'], 'keep me')
        self.assertEqual(saved['remind_months'], [6, 3])
        self.assertEqual(saved['monthly_payment'], 110)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sent').fetchone()[0], 1)

    def test_ingress_rejects_non_gateway(self):
        server = ui_server.make_server(self.db, [], host='127.0.0.1', port=0, peer='192.0.2.1')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        self.addCleanup(conn.close)
        conn.request('GET', '/api/contracts')
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        response.read()

    def test_partial_insurance_and_dated_mortgage_balance(self):
        insurance = {'id': 'policy', 'name': 'Pet policy', 'kind': 'pet insurance', 'end': '2026-12-10'}
        mortgage = {'id': 'mortgage', 'name': 'Mortgage', 'kind': 'mortgage', 'monthly_payment': 1000,
                    'observed_balance': {'amount': 200000, 'as_of': '2026-09'}}
        store.import_once(self.db, [insurance, mortgage], {})
        rows = store.records(self.db, dt.date(2026, 11, 16))
        loan = next(row for row in rows if row['id'] == 'mortgage')
        policy = next(row for row in rows if row['id'] == 'policy')
        self.assertNotIn('projected_balance', loan)
        self.assertNotIn('estimated_scheduled_total', loan)
        self.assertEqual(loan['observed_balance']['as_of'], '2026-09')
        self.assertEqual(policy['days_left'], 24)
        self.assertIsNone(policy['percent'])
        self.assertIn('Mortgage', store.markdown(rows))
        self.assertEqual([x['id'] for x in store.due(self.db, dt.date(2026, 11, 16))], ['policy'])

    def test_loan_is_never_projected_as_a_lease(self):
        c = dict(self.c, kind='mortgage')
        projected = store.project(c, dt.date(2027, 1, 1))
        self.assertNotIn('projected_balance', projected)
        self.assertEqual(projected['balance_snapshot'], c['balance_snapshot'])

    def test_graph_uses_snapshot_and_scheduled_month_end_payments(self):
        c = dict(self.c, payment_day=31, monthly_payment=100,
                 balance_snapshot={'amount': 250, 'as_of': '2027-01-31'})
        self.assertEqual(store.projection_series(c), [
            {'date': '2027-01-31', 'balance': 250.0},
            {'date': '2027-02-28', 'balance': 150.0},
            {'date': '2027-03-31', 'balance': 50.0},
            {'date': '2027-04-30', 'balance': 0.0}])

    def test_graph_requires_lease_data_and_is_bounded(self):
        self.assertEqual(store.projection_series(dict(self.c, kind='mortgage')), [])
        self.assertEqual(store.projection_series(dict(self.c, kind='loan lease')), [])
        c = dict(self.c, balance_snapshot={'amount': 1000000, 'as_of': '2026-11-16'})
        self.assertEqual(store.projection_series(c), [])
        c = dict(self.c, balance_snapshot={'amount': 0, 'as_of': '2026-11-16'})
        self.assertEqual(store.projection_series(c), [])

    def test_loan_graph_includes_interest_and_stops_at_rate_end(self):
        c = {'id': 'loan', 'name': 'Loan', 'kind': 'mortgage', 'monthly_payment': 100,
             'observed_balance': {'amount': 1000, 'as_of': '2026-12'},
             'interest_rate_percent': 12, 'interest_rate_until': '2027-02-28'}
        self.assertEqual(store.projection_series(c), [
            {'date': '2026-12', 'balance': 1000.0},
            {'date': '2027-01', 'balance': 910.0},
            {'date': '2027-02', 'balance': 819.1}])
        self.assertNotIn('projected_balance', store.project(c, dt.date(2027, 1, 1)))

    def test_loan_graph_zero_rate_and_insufficient_payment(self):
        c = {'id': 'loan', 'name': 'Loan', 'kind': 'loan', 'monthly_payment': 100,
             'observed_balance': {'amount': 250, 'as_of': '2026-11'}, 'interest_rate_percent': 0}
        self.assertEqual([p['balance'] for p in store.projection_series(c)], [250, 150, 50, 0])
        c.update(monthly_payment=1, observed_balance={'amount': 1000, 'as_of': '2026-11'}, interest_rate_percent=24)
        points = store.projection_series(c)
        self.assertEqual(len(points), 13)
        self.assertGreater(points[-1]['balance'], points[0]['balance'])
        c.pop('interest_rate_percent')
        self.assertEqual(store.projection_series(c), [])

    def test_loan_interest_rate_validation(self):
        for bad in (-1, 'NaN', 1001):
            with self.assertRaises(ValueError):
                store.validate(dict(self.c, interest_rate_percent=bad))


if __name__ == '__main__':
    unittest.main()
