import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import watchdog, manager

OPTS = dict(watchdog.DEFAULTS, watchdog_mode='live', watchdog_never_restart=['ih_never'],
            media_players=['media_player.b2'], media_probe_enabled=True)
PFX = 'abcd1234_'


class Fake:
    """Fake Supervisor: infos by FULL slug; records restarts requested through the manager action."""
    def __init__(self):
        self.i = {}; self.restarted = []; self.log = 'line1\nBearer abc123 x\nip 10.1.2.3 ok\npassword=hunter2'

    def sup(self, method, path):
        return self.i[path.split('/')[2]]

    def restart(self, short):
        self.restarted.append(short); return 202, {'job': 1}

    def logs(self, short):
        return self.log


class Tasks:
    def __init__(self): self.added = []; self.comments = []; self.open = True
    def add(self, t, b): self.added.append((t, b)); return len(self.added)
    def comment(self, n, t): self.comments.append((n, t))
    def is_open(self, n): return self.open


class Media:
    def __init__(self): self.ok = [False, False]; self.reloaded = 0
    def play_probe(self, e): return self.ok.pop(0)
    def reload_entry(self, e): self.reloaded += 1


class T(unittest.TestCase):
    def setUp(self):
        self.db = watchdog.DB(os.path.join(tempfile.mkdtemp(), 'watchdog.db'))
        self.f, self.tasks, self.media, self.notes, self.now = Fake(), Tasks(), Media(), [], [1000000.0]
        self.opts = dict(OPTS)
        self.http = {'ok': True}
        self.cat = []          # catalogue [{slug,name}]
        self.inst = {}         # installed {short: {slug}}
        self.wd = watchdog.Watchdog(self.db, self.f.sup, lambda: watchdog.ih_targets(self.cat, self.inst), self.f.restart, self.f.logs, self.tasks,
                                    self.notes.append, self.media, lambda: self.opts, lambda: self.now[0],
                                    lambda url, t, ok: {'type': 'http', 'ok': self.http['ok'], 'detail': 'HTTP 200' if self.http['ok'] else 'timeout'})

    def run_cycles(self, n, step=60):
        for _ in range(n):
            self.wd.cycle(); self.now[0] += step

    def started(self, short='ih_a', **kw):
        full = PFX + short
        self.cat.append({'slug': short, 'name': short.upper()}); self.inst[short] = {'slug': full}
        self.f.i[full] = dict({'state': 'started', 'boot': 'auto', 'version': '1', 'version_latest': '1', 'ingress': True, 'ingress_port': 8000, 'ip_address': '1.2.3.4'}, **kw)

    def info(self, short='ih_a'):
        return self.f.i[PFX + short]

    def test_healthy_nothing_happens(self):
        self.started(); self.run_cycles(5)
        self.assertEqual(self.f.restarted, []); self.assertEqual(self.tasks.added, [])
        self.assertEqual(self.wd.health['ih_a']['state'], 'OK')

    def test_stuck_restarts_after_three_through_manager_action(self):
        self.started(); self.http['ok'] = False
        self.run_cycles(2); self.assertEqual(self.f.restarted, [])
        self.run_cycles(1); self.assertEqual(self.f.restarted, ['ih_a'])      # SHORT name: the manager's restart action takes catalogue slugs
        self.assertEqual(len(self.tasks.added), 1)
        self.assertTrue(self.tasks.added[0][0].startswith('Add-on'))
        self.assertEqual(len(self.notes), 1)

    def test_failed_restart_is_reported_not_counted(self):
        self.started(); self.http['ok'] = False
        self.f.restart = lambda s: (409, {'error': 'busy'})
        self.wd.restart = self.f.restart
        self.run_cycles(3)
        self.assertEqual(self.db.query('SELECT COUNT(*) FROM restarts')[0][0], 0)
        self.assertIn('restart FAILED', self.db.query('SELECT action FROM incidents')[0][0])

    def test_redaction(self):
        self.started(); self.http['ok'] = False; self.run_cycles(3)
        ex = self.db.query('SELECT excerpt FROM incidents')[0][0]
        for bad in ('abc123', '10.1.2.3', 'hunter2'): self.assertNotIn(bad, ex)

    def test_dry_restarts_nothing_marks_task(self):
        self.opts['watchdog_mode'] = 'dry'; self.started(); self.http['ok'] = False; self.run_cycles(4)
        self.assertEqual(self.f.restarted, [])
        self.assertTrue(self.tasks.added[0][0].startswith('[DRY]'))
        self.assertEqual(self.db.query('SELECT dry FROM incidents')[0][0], 1)

    def test_dry_default_never_restarts(self):
        self.assertEqual(watchdog.DEFAULTS['watchdog_mode'], 'dry')
        self.opts['watchdog_mode'] = watchdog.DEFAULTS['watchdog_mode']
        self.started(); self.http['ok'] = False; self.run_cycles(30)
        self.assertEqual(self.f.restarted, [])

    def test_page_switch_wins_over_option(self):
        self.opts['watchdog_mode'] = 'dry'; self.wd.set_mode('live'); self.assertEqual(self.wd.mode(), 'live')
        self.started(); self.http['ok'] = False; self.run_cycles(3); self.assertEqual(self.f.restarted, ['ih_a'])
        with self.assertRaises(ValueError): self.wd.set_mode('bogus')

    def test_cooldown_and_hourly_limit(self):
        self.started(); self.http['ok'] = False
        self.run_cycles(3)                         # restart 1
        self.run_cycles(5); self.assertEqual(len(self.f.restarted), 1)   # inside cool-down
        for _ in range(4): self.run_cycles(11, 60)
        self.assertEqual(len(self.f.restarted), 3)   # never more than 3 in the hour window while it keeps failing
        self.assertTrue(any('escalated' in r[0] for r in self.db.query('SELECT action FROM incidents')))

    def test_never_restart_list_gets_task_only(self):
        self.started('ih_never'); self.http['ok'] = False; self.run_cycles(3)
        self.assertEqual(self.f.restarted, [])
        self.assertEqual(len(self.tasks.added), 1)

    def test_suffix_match(self):
        self.assertTrue(watchdog.listed('abcd1234_ih_x', ['ih_x']))
        self.assertFalse(watchdog.listed('x_ih_x2', ['ih_x']))

    def test_stopped_boot_auto_after_seen_started(self):
        self.started(); self.run_cycles(1)
        self.info()['state'] = 'stopped'; self.run_cycles(3)
        self.assertEqual(self.f.restarted, ['ih_a'])

    def test_stopped_never_seen_or_manual_ignored(self):
        self.started('ih_m', state='stopped', boot='manual'); self.started('ih_n', state='stopped', boot='auto')
        self.run_cycles(6); self.assertEqual(self.f.restarted, [])
        self.assertEqual(self.wd.health['ih_m']['state'], 'OK')

    def test_dedupe_repeat_is_comment(self):
        self.opts['watchdog_cooldown_minutes'] = 1; self.opts['watchdog_max_restarts_per_hour'] = 9
        self.started(); self.http['ok'] = False; self.run_cycles(3); self.run_cycles(40)
        self.assertEqual(len(self.tasks.added), 1); self.assertTrue(self.tasks.comments)

    def test_disabled_addon_ignored(self):
        self.started(); self.run_cycles(1); self.wd.set_enabled('ih_a', False)
        self.http['ok'] = False; self.run_cycles(6); self.assertEqual(self.f.restarted, [])
        self.assertFalse(self.wd.health['ih_a']['enabled'])

    def test_toggle_unknown_slug_refused(self):
        with self.assertRaises(ValueError): self.wd.set_enabled('core_ssh', False)

    def test_media_retry_after_reload(self):
        self.media.ok = [False, True]; r = self.wd.media_cycle()
        self.assertEqual(self.media.reloaded, 1); self.assertIn('retry ok', r[0][1])
        self.assertEqual(self.wd.media_cycle(), [])   # rate limit 30 min

    def test_media_off_by_default_and_not_in_dry(self):
        self.assertFalse(watchdog.DEFAULTS['media_probe_enabled'])
        self.opts['watchdog_mode'] = 'dry'; self.assertEqual(self.wd.media_cycle(), [])
        self.opts['watchdog_mode'] = 'live'; self.opts['media_probe_enabled'] = False; self.assertEqual(self.wd.media_cycle(), [])

    # ---- new in the orchestrator: scope and healthlib ----
    def test_non_ih_slugs_are_skipped(self):
        cat = [{'slug': 'ih_a', 'name': 'A'}, {'slug': 'ih_orchestrator', 'name': 'Me'}, {'slug': 'codex_terminal', 'name': 'Codex'}]
        inst = {k: {'slug': PFX + k} for k in ('ih_a', 'ih_orchestrator', 'codex_terminal', 'mqtt', 'ih_not_in_catalogue')}
        self.assertEqual(watchdog.ih_targets(cat, inst), [('ih_a', PFX + 'ih_a', 'A')])
        self.assertEqual(watchdog.ih_targets([{'slug': 'ih_b'}], {}), [])        # catalogue entry that is not installed

    def test_cycle_only_reads_ih_addons_from_supervisor(self):
        self.started('ih_a')
        self.cat.append({'slug': 'codex_terminal', 'name': 'Codex'}); self.inst['codex_terminal'] = {'slug': 'xyz_codex'}
        self.run_cycles(2)
        self.assertEqual(set(self.wd.health), {'ih_a'})

    def test_uninstalled_addon_drops_out(self):
        self.started(); self.run_cycles(1); del self.inst['ih_a']; self.run_cycles(1)
        self.assertEqual(self.wd.health, {})

    def test_result_is_healthlib_shaped(self):
        self.started(); self.http['ok'] = False; self.run_cycles(1)
        r = self.wd.health['ih_a']
        self.assertEqual(r['state'], 'FAIL')
        self.assertEqual({c['type'] for c in r['checks']}, {'supervisor_addon', 'http'})
        self.started('ih_b'); self.http['ok'] = True; self.run_cycles(1)
        self.assertEqual(self.wd.health['ih_b']['state'], 'OK')

    def test_no_ingress_means_state_check_only(self):
        self.started(ingress=False); self.run_cycles(1)
        self.assertEqual([c['type'] for c in self.wd.health['ih_a']['checks']], ['supervisor_addon'])

    def test_unreadable_supervisor_is_unknown_and_not_a_failure(self):
        self.started()
        orig = self.wd.sup
        def bad(m, p): raise RuntimeError('x')
        self.wd.sup = bad; self.run_cycles(5)
        self.assertEqual(self.f.restarted, [])
        self.assertEqual(self.wd.health['ih_a']['state'], 'UNKNOWN')

    def test_report_lists_rows_and_incidents(self):
        self.started(); self.http['ok'] = False; self.run_cycles(3)
        rep = self.wd.report()
        self.assertEqual(rep['mode'], 'live'); self.assertEqual(rep['rows'][0]['slug'], 'ih_a'); self.assertEqual(len(rep['incidents']), 1)

    def test_migrations_reopen_keeps_data(self):
        self.started(); self.http['ok'] = False; self.run_cycles(3)
        path = self.db.c.execute('PRAGMA database_list').fetchall()[0][2]
        db2 = watchdog.DB(path)
        self.assertEqual(db2.query('SELECT COUNT(*) FROM incidents')[0][0], 1)


class HealthCards(unittest.TestCase):
    """The watchdog result reaches the manager's Health data (existing cards) and the page switch."""
    def make(self, report=None, existing=()):
        class W:
            def __init__(s): s.modes = []; s.toggles = []
            def report(s): return report
            def set_mode(s, m): s.modes.append(m)
            def set_enabled(s, k, v):
                if k != 'ih_a': raise ValueError('not an IH add-on')
                s.toggles.append((k, v))
        w = W()
        snap = {'services': [{'name': n, 'state': 'OK', 'checks': []} for n in existing], 'counts': {'OK': len(existing)}, 'total': len(existing), 'stale': False, 'age_s': 1}
        return manager.Manager([], health=lambda: snap, wd=lambda: w, log=lambda m: None), w

    ROW = {'slug': 'ih_a', 'name': 'A', 'state': 'FAIL', 'probe': 'timeout', 'checks': [{'detail': 'started'}, {'detail': 'timeout'}]}

    def test_rows_feed_cards_and_counts(self):
        m, _ = self.make({'enabled': True, 'mode': 'dry', 'failed_probes': 3, 'rows': [self.ROW], 'incidents': []}, existing=['x'])
        code, h = m.health()
        self.assertEqual(h['counts'], {'OK': 1, 'FAIL': 1}); self.assertEqual(h['total'], 2)
        self.assertEqual([s['name'] for s in h['services']], ['x', 'ih_a'])
        self.assertEqual(h['watchdog']['mode'], 'dry')

    def test_manifest_name_wins(self):
        m, _ = self.make({'enabled': True, 'mode': 'dry', 'failed_probes': 3, 'rows': [self.ROW], 'incidents': []}, existing=['ih_a'])
        self.assertEqual(m.health()[1]['total'], 1)

    def test_off_or_missing_changes_nothing(self):
        m, _ = self.make({'enabled': False, 'mode': 'dry', 'failed_probes': 3, 'rows': [self.ROW], 'incidents': []})
        self.assertEqual(m.health()[1]['total'], 0)
        m2 = manager.Manager([], health=lambda: {'services': [], 'counts': {}, 'total': 0}, log=lambda x: None)
        self.assertIsNone(m2.health()[1]['watchdog'])
        self.assertEqual(m2.watchdog_mode('live')[0], 400)

    def test_mode_and_toggle_endpoints(self):
        m, w = self.make({'enabled': True, 'mode': 'dry', 'failed_probes': 3, 'rows': [], 'incidents': []})
        self.assertEqual(m.watchdog_mode('live')[0], 200); self.assertEqual(w.modes, ['live'])
        self.assertEqual(m.watchdog_mode('x')[0], 400)
        self.assertEqual(m.watchdog_toggle('ih_a', False)[0], 200); self.assertEqual(w.toggles, [('ih_a', False)])
        self.assertEqual(m.watchdog_toggle('core_ssh', False)[0], 404)


if __name__ == '__main__':
    unittest.main()
