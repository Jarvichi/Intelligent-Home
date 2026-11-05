"""Jobs page: filter/sort/summary logic (run with node) and the read-only API payload."""
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..', 'app'))
import ui_server


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class LogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, TZ='Europe/London')
        cls.r = json.loads(subprocess.run(['node', os.path.join(HERE, 'jobs_ui_logic_check.js')], check=True, capture_output=True, text=True, env=env).stdout)

    def test_next_run_sorts_by_time_not_text(self):
        # Due tomorrow must come after due later today (the mock-up sorted the text "03:20 tomorrow" first).
        self.assertEqual(self.r['next'], ['overdue', 'soon', 'later_today', 'tomorrow', 'never'])

    def test_last_run_newest_first_missing_last(self):
        self.assertEqual(self.r['last'], ['d', 'b', 'a', 'c'])

    def test_name_and_runs(self):
        self.assertEqual(self.r['name'], ['A', 'b', 'c'])
        self.assertEqual(self.r['runs'], ['y', 'x', 'z', 'w'])

    def test_filters(self):
        self.assertEqual(self.r['fLive'], ['car_log', 'birthday'])
        self.assertEqual(self.r['fDry'], ['bus_watch'])
        self.assertEqual(self.r['fOff'], ['brain_stats'])
        self.assertEqual(self.r['fWait'], ['birthday'])
        self.assertEqual(self.r['fSearch'], ['car_log'])
        self.assertEqual(self.r['fNone'], 0)
        self.assertEqual(self.r['counts'], {'all': 4, 'live': 2, 'dry': 1, 'off': 1, 'wait': 1})

    def test_summary(self):
        s = self.r['sum']
        self.assertEqual(s['failing'], ['bad'])
        self.assertEqual(s['errors'], 2)            # the switched-off job's errors are not counted
        self.assertEqual(s['last'], 'builtin')   # 30 s before the check beats 09:25
        self.assertEqual(s['next'], 'bad')
        self.assertEqual(s['waiting'], 2)
        self.assertEqual(s['groups'], {'daily': 1, 'on an event': 1})
        self.assertEqual((s['live'], s['dry'], s['off']), (4, 1, 1))

    def test_plain_words(self):
        self.assertEqual(self.r['sched'], ['daily at 18:00', 'every 10 min', 'every 1 hour', 'every 6 hours', 'always on, every 90 s', 'when something happens', 'not scheduled'])
        self.assertEqual(self.r['when'], ['09:25', '03:20 tomorrow', '22:30 yesterday', None])
        self.assertEqual(self.r['why'][0], 'Test mode: it has not been set live')
        self.assertEqual(self.r['why'][2:], ['something odd', 'Not reported'])
        self.assertEqual(self.r['words'], ['Test mode', 'Background task', 'Built-in job'])


class PayloadTests(unittest.TestCase):
    STATUS = {
        'jobs': {'backupstore': {'mode': 'dry', 'why': 'mode option is dry', 'runs': 0, 'errors': 0, 'last_ok': None, 'last_run_s': None, 'last_mode': None, 'last_error': None, 'last_trigger': 'x'}},
        'plugins': {
            'car_log': {'mode': 'live', 'why': 'live', 'runs': 3, 'errors': 1, 'last_ok': False, 'last_run': '2026-11-05T09:00:00+00:00',
                        'next_run': '2026-11-05T09:10:00+00:00', 'last_mode': 'live', 'last_error': 'boom', 'schedule': {'kind': 'every', 'minutes': 10, 'secret': 'x'}},
            'weird': {'mode': 'off', 'schedule': {'kind': 'bogus'}},
            'bad_item': 'nope'},
        'children': {'backupstore': {'name': 'ih_jobs_backup', 'title': 'Intelligent Home - Jobs - Backup', 'version': '0.1.6', 'mode': 'live', 'up': True, 'healthy': True, 'seen_s_ago': 4, 'sampled_at': 1}},
        'secret_option': 'must not pass'}

    def test_rows_keep_every_old_field(self):
        p = ui_server.jobs_payload(self.STATUS, 100.0)
        self.assertTrue(p['available'])
        self.assertEqual(p['checked_at'], 100.0)
        rows = {r['name']: r for r in p['jobs']}
        self.assertEqual(set(rows), {'backupstore', 'car_log', 'weird'})
        for field in ('mode', 'runs', 'errors', 'last_ok', 'last_run', 'last_run_s', 'next_run', 'last_mode', 'last_error', 'why', 'name', 'kind'):
            self.assertIn(field, rows['car_log'])
        self.assertEqual(rows['car_log']['kind'], 'Plugin')
        self.assertEqual(rows['backupstore']['kind'], 'Built-in job')
        self.assertNotIn('last_trigger', rows['backupstore'])

    def test_schedule_is_cleaned(self):
        rows = {r['name']: r for r in ui_server.jobs_payload(self.STATUS, 1)['jobs']}
        self.assertEqual(rows['car_log']['schedule'], {'kind': 'every', 'minutes': 10})
        self.assertIsNone(rows['weird']['schedule'])
        self.assertIsNone(rows['backupstore']['schedule'])

    def test_children_pass_only_known_fields(self):
        p = ui_server.jobs_payload(self.STATUS, 1)
        self.assertEqual(p['children'], [{'key': 'backupstore', 'title': 'Intelligent Home - Jobs - Backup', 'version': '0.1.6', 'mode': 'live', 'up': True, 'healthy': True, 'seen_s_ago': 4}])
        self.assertNotIn('must not pass', json.dumps(p))

    def test_missing_sections_do_not_crash(self):
        p = ui_server.jobs_payload({}, 1)
        self.assertEqual((p['jobs'], p['children']), ([], []))

    def test_page_assets_are_served_and_exist(self):
        for url, name in ui_server.ASSETS.items():
            self.assertTrue(os.path.exists(os.path.join(ui_server.ROOT, name)), name)
        self.assertEqual(ui_server.ASSETS['/jobs-logic.js'], 'jobs-logic.js')


if __name__ == '__main__':
    unittest.main()
