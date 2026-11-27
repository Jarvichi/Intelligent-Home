import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from ihlib import db as ihdb
import core
import clients
from unittest import mock
from urllib.error import HTTPError
import threading
import urllib.request
from http.server import ThreadingHTTPServer

OPTS = {'mode': 'live', 'failed_probes': 3, 'cooldown_minutes': 10, 'max_restarts_per_hour': 3, 'never_restart': ['core', 'ih_orchestrator'],
        'media_players': ['media_player.b2'], 'media_probe_enabled': True}


class Sup:
    def __init__(self): self.i = {}; self.restarted = []; self.log = 'line1\nBearer abc123 x\nip 10.1.2.3 ok\npassword=hunter2'
    def addons(self): return [{'slug': s, 'name': s.upper()} for s in self.i]
    def info(self, s): return self.i[s]
    def logs(self, s): return self.log
    def restart(self, s): self.restarted.append(s)


class Tasks:
    def __init__(self): self.added = []; self.comments = []; self.open = True
    def add(self, t, b): self.added.append((t, b)); return len(self.added)
    def comment(self, n, t): self.comments.append((n, t))
    def is_open(self, n): return self.open


class Media:
    def __init__(self): self.ok = [False, False]; self.reloaded = 0
    busy_now = False
    def busy(self, e): return self.busy_now
    def play_probe(self, e): return self.ok.pop(0)
    def reload_entry(self, e): self.reloaded += 1


class Page(unittest.TestCase):
    def test_shared_ui_assets_are_served_to_ingress(self):
        import main
        with mock.patch.object(main, 'PEER', '127.0.0.1'):
            server = ThreadingHTTPServer(('127.0.0.1', 0), main.handler(None, None, None, None))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                for path in ('/', '/ih-ui.css', '/ih-ui.js'):
                    with urllib.request.urlopen('http://127.0.0.1:%d%s' % (server.server_port, path), timeout=3) as response:
                        self.assertEqual(response.status, 200)
            finally:
                server.shutdown(); thread.join(); server.server_close()


class T(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.db = ihdb.open_db(os.path.join(d, 'ih.db'), core.MIGRATIONS)
        self.kv = ihdb.KV(self.db, 'w')
        self.sup, self.tasks, self.media, self.notes, self.now = Sup(), Tasks(), Media(), [], [1000000.0]
        self.opts = dict(OPTS)
        self.http = {'ok': True}
        self.wd = core.Watchdog(self.db, self.kv, self.sup, lambda u: (self.http['ok'], 'x'), self.tasks, self.notes.append, self.media, lambda: self.opts, lambda: self.now[0])

    def run_cycles(self, n, step=60):
        for _ in range(n):
            self.wd.cycle(); self.now[0] += step

    def started(self, slug='a', **kw):
        self.sup.i[slug] = dict({'state': 'started', 'boot': 'auto', 'ingress': True, 'ingress_port': 8000, 'ip_address': '1.2.3.4'}, **kw)

    def test_healthy_nothing_happens(self):
        self.started(); self.run_cycles(5)
        self.assertEqual(self.sup.restarted, []); self.assertEqual(self.tasks.added, [])

    def test_failed_task_is_retried_after_addon_recovers(self):
        self.started('core'); self.http['ok'] = False
        add = self.tasks.add
        self.tasks.add = mock.Mock(side_effect=OSError('unavailable'))
        self.run_cycles(3)
        self.assertIn('could not be filed', self.notes[-1])
        self.assertIsNone(self.db.query('SELECT task FROM incidents')[0][0])
        self.tasks.add = add
        self.http['ok'] = True
        self.run_cycles(1)
        self.assertEqual(len(self.tasks.added), 1)
        self.assertEqual(self.db.query('SELECT task FROM incidents')[0][0], 1)

    def test_http_server_error_is_unhealthy_and_auth_can_fallback(self):
        with mock.patch.object(clients.urllib.request, 'urlopen', side_effect=HTTPError('http://x', 503, 'error', {}, None)):
            self.assertFalse(clients.http_probe('http://x')[0])
        self.started()
        probe = mock.Mock(side_effect=[(True, 'HTTP 404'), (True, 'HTTP 200')])
        self.wd.http_probe = probe
        self.assertTrue(self.wd.probe('a', self.sup.i['a'])[0])
        self.assertTrue(probe.call_args_list[0].args[0].endswith('/healthz'))
        self.assertTrue(probe.call_args_list[1].args[0].endswith('/'))

    def test_stuck_restarts_after_three(self):
        self.started(); self.http['ok'] = False
        self.run_cycles(2); self.assertEqual(self.sup.restarted, [])
        self.run_cycles(1); self.assertEqual(self.sup.restarted, ['a'])
        self.assertEqual(len(self.tasks.added), 1)
        self.assertEqual(self.tasks.added[0][0].startswith('Add-on'), True)
        self.assertEqual(len(self.notes), 1)

    def test_redaction(self):
        self.started(); self.http['ok'] = False; self.run_cycles(3)
        ex = self.db.query('SELECT excerpt FROM incidents')[0][0]
        for bad in ('abc123', '10.1.2.3', 'hunter2'): self.assertNotIn(bad, ex)

    def test_dry_restarts_nothing_marks_task(self):
        self.opts['mode'] = 'dry'; self.started(); self.http['ok'] = False; self.run_cycles(4)
        self.assertEqual(self.sup.restarted, [])
        self.assertTrue(self.tasks.added[0][0].startswith('[DRY]'))
        self.assertEqual(self.db.query('SELECT dry FROM incidents')[0][0], 1)

    def test_cooldown_and_hourly_limit(self):
        self.started(); self.http['ok'] = False
        self.run_cycles(3)                         # restart 1
        self.run_cycles(5); self.assertEqual(len(self.sup.restarted), 1)   # inside cool-down
        for _ in range(4): self.run_cycles(11, 60)
        self.assertEqual(len(self.sup.restarted), 3)   # never more than 3 in the hour window while it keeps failing
        self.assertTrue(any('escalated' in r[0] for r in self.db.query('SELECT action FROM incidents')))

    def test_never_restart_list_gets_task_only(self):
        self.started('core'); self.started('32c8114c_ih_orchestrator'); self.http['ok'] = False; self.run_cycles(3)
        self.assertEqual(self.sup.restarted, [])
        self.assertEqual(len(self.tasks.added), 2)

    def test_suffix_match(self):
        self.assertTrue(core.listed('32c8114c_ih_orchestrator', ['ih_orchestrator']))
        self.assertFalse(core.listed('x_ih_orchestrator2', ['ih_orchestrator']))

    def test_stopped_boot_auto_after_seen_started(self):
        self.started(); self.run_cycles(1)
        self.sup.i['a']['state'] = 'stopped'; self.run_cycles(3)
        self.assertEqual(self.sup.restarted, ['a'])

    def test_stopped_never_seen_or_manual_ignored(self):
        self.sup.i['m'] = {'state': 'stopped', 'boot': 'manual'}; self.sup.i['n'] = {'state': 'stopped', 'boot': 'auto'}
        self.run_cycles(6); self.assertEqual(self.sup.restarted, [])

    def test_dedupe_repeat_is_comment(self):
        self.opts['cooldown_minutes'] = 1; self.opts['max_restarts_per_hour'] = 9
        self.started(); self.http['ok'] = False; self.run_cycles(3); self.run_cycles(40)
        self.assertEqual(len(self.tasks.added), 1); self.assertTrue(self.tasks.comments)

    def test_disabled_addon_ignored(self):
        self.started(); s = self.wd.st('a'); s['enabled'] = False; self.wd.save('a', s)
        self.http['ok'] = False; self.run_cycles(6); self.assertEqual(self.sup.restarted, [])

    def test_media_retry_after_reload(self):
        self.media.ok = [False, True]; r = self.wd.media_cycle()
        self.assertEqual(self.media.reloaded, 1); self.assertIn('retry ok', r[0][1])
        self.assertEqual(self.wd.media_cycle(), [])   # rate limit 30 min

    def test_media_skips_busy_player(self):
        self.media.busy_now = True; self.media.ok = [False, False]
        self.assertEqual(self.wd.media_cycle(), []); self.assertEqual(self.media.reloaded, 0)
        self.media.busy_now = False; self.media.ok = [True]
        self.assertEqual(self.wd.media_cycle()[0][1], 'ok')   # checked as soon as it is idle

    def test_media_not_in_dry(self):
        self.opts['mode'] = 'dry'; self.assertEqual(self.wd.media_cycle(), [])


if __name__ == '__main__':
    unittest.main()
