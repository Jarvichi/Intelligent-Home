"""Run phases (0.5.0): DRAIN -> PLAN -> BLOCK, block sizing, count cap, repeat, backlog end, pace end cancels, dry mode, settings."""
import os, sys, unittest
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import runs as R
from service import Service
from store import StoreError
from test_runs import FakeHA, T0, todo
from test_service import Base, opts


class Phases(Base):
    def make(self, items=12, block=5, max_items=None, **o):
        super().make(T0, FakeHA(todo(items)), **o)
        self.svc.set_run_settings({'night': {'block_size': block, 'max_items': max_items, 'pace_target': 0.0}})
        self.pace(-3.0)
        return self.svc

    def tick(self, secs=31):
        self.c.move(seconds=secs)
        self.svc.tick()
        return self.svc.load_state()

    def kinds(self, status='queued'):
        return [i['kind'] for i in self.svc.store.list((status,))]

    def work(self, kind=None):
        """Host runner: claim and finish one job. -> its kind or None."""
        c = self.svc.claim('w1')
        if not c['claimed']:
            return None
        self.svc.store.finish(c['item']['id'], 'w1', True, summary='ok')
        return c['item']['kind']

    def test_drain_first_nothing_fed_while_queue_has_jobs(self):
        self.make()
        self.add('x'); self.add('y')
        for _ in range(3):
            st = self.tick()
        self.assertEqual(sorted(self.kinds()), ['other', 'other'])        # no plan job, no to-do fed
        self.assertEqual(st['run']['phase'], 'drain')
        self.assertEqual(self.work(), 'other'); self.tick()
        self.assertEqual(self.kinds(), ['other'])
        self.assertEqual(self.work(), 'other')
        st = self.tick()                                                  # queue empty now -> plan
        self.assertEqual((st['run']['phase'], self.kinds()), ('plan', ['plan_todos']))

    def test_plan_once_then_block_of_n(self):
        self.make(block=5)
        st = self.tick()
        self.assertEqual(self.kinds(), ['plan_todos'])
        self.tick(); self.tick()                                          # waiting for the plan: no second job, no block
        self.assertEqual(self.kinds(), ['plan_todos'])
        self.assertEqual(self.work(), 'plan_todos')
        st = self.tick()
        self.assertEqual(self.kinds(), ['todo'] * 5)
        self.assertEqual((st['run']['phase'], st['run']['block_no'], st['run']['fed']), ('block', 1, 5))
        self.assertEqual(len({i['ref'] for i in self.svc.store.list()}), 6)  # plan + 5 distinct to-dos
        self.tick(); self.assertEqual(self.kinds(), ['todo'] * 5)           # block not consumed yet: nothing more fed

    def test_status_shows_block_progress(self):
        self.make(block=3)
        self.tick(); self.work(); self.tick()
        self.work()
        a = self.svc.runs_view()['active']
        self.assertEqual((a['block_no'], a['block_total'], a['block_done'], a['block_size']), (1, 3, 1, 3))
        self.assertEqual(a['phase_text'], 'block 1: 1 of 3 done')

    def test_repeat_blocks_then_backlog_empty_ends(self):
        self.make(items=7, block=5)
        self.tick(); self.work(); self.tick()                             # block 1: 5
        while self.work():
            pass
        self.tick()                                                       # block consumed -> drain
        st = self.tick()                                                  # -> plan again
        self.assertEqual(self.kinds(), ['plan_todos'])
        self.work(); st = self.tick()
        self.assertEqual((st['run']['block_no'], self.kinds()), (2, ['todo'] * 2))   # a short block: the backlog is nearly empty
        while self.work():
            pass
        st = self.tick()
        self.assertNotIn('run', st)
        self.assertEqual(st['run_last']['reason'], 'backlog empty')
        self.assertEqual(st['run_last']['started'], 7)

    def test_max_items_caps_the_last_block(self):
        self.make(items=20, block=5, max_items=7)
        self.tick(); self.work(); self.tick()
        self.assertEqual(self.kinds(), ['todo'] * 5)
        while self.work():
            pass
        self.tick(); self.tick(); self.work(); st = self.tick()
        self.assertEqual(self.kinds(), ['todo'] * 2)                      # never above the remaining count
        self.assertEqual(st['run']['fed'], 7)
        while self.work():
            pass
        st = self.tick()
        self.assertIn('item count reached (7)', st['run_last']['reason'])
        self.assertEqual(len([i for i in self.svc.store.list() if i['kind'] == 'todo']), 7)

    def test_block_never_repeats_a_queued_item(self):
        self.make(items=10, block=4)
        self.svc.store.enqueue('todo', 'u0', 'IH: job 0', 'x')            # already in the queue (drain waits, so work it first as a todo)
        self.tick(); self.assertEqual(self.kinds(), ['todo'])             # drain: only the old job
        self.work(); self.tick(); self.work(); self.tick()
        refs = [i['ref'] for i in self.svc.store.list(('queued',))]
        self.assertEqual(len(refs), len(set(refs)))

    def test_pace_end_cancels_unstarted_block_jobs(self):
        self.make(block=5)
        self.tick(); self.work(); self.tick()
        self.assertEqual(self.work(), 'todo')                             # one started and done
        self.pace(0.5)                                                    # pace target reached
        st = self.tick()
        self.assertNotIn('run', st)
        self.assertIn('pace target reached', st['run_last']['reason'])
        self.assertEqual(self.kinds(), [])                                # the 4 unstarted jobs are cancelled, none left behind
        self.assertEqual(self.kinds('cancelled'), ['todo'] * 4)
        self.assertEqual(self.kinds('done').count('todo'), 1)

    def test_other_queued_jobs_are_not_cancelled_by_the_run(self):
        self.make(block=2)
        self.tick(); self.work(); self.tick()
        other = self.add('late')                                          # someone else's job, not fed by the run
        self.svc.run_stop()
        self.assertEqual([i['id'] for i in self.svc.store.list(('queued',))], [other['id']])

    def test_plan_wait_is_bounded_then_block_goes_on(self):
        self.make(block=2)
        self.tick()
        self.assertEqual(self.kinds(), ['plan_todos'])
        self.tick(secs=R.PLAN_WAIT_MIN * 60 + 60)
        self.assertEqual(self.kinds(), ['todo'] * 2)                      # plan job cancelled, block fed
        self.assertEqual(self.kinds('cancelled'), ['plan_todos'])

    def test_failed_plan_job_carries_on(self):
        self.make(block=2)
        self.tick()
        c = self.svc.claim('w1')
        self.svc.store.finish(c['item']['id'], 'w1', False, error='quota')
        self.tick()
        self.assertEqual(self.kinds(), ['todo'] * 2)

    def test_plan_job_does_not_count_as_a_run_item(self):
        self.make(block=2, max_items=2)
        self.tick(); self.assertEqual(self.work(), 'plan_todos')
        self.assertEqual(self.svc.load_state()['run']['started'], 0)

    def test_gate_shut_feeds_nothing(self):
        self.make()
        self.pace(2.0)                                                    # above target, nothing started: the run waits
        self.tick(); self.tick()
        self.assertEqual(self.svc.store.list(), [])

    def test_dry_mode_logs_per_phase_and_queues_nothing(self):
        self.make(items=12, block=3, max_items=5, mode='dry')
        self.add('x')                                                     # a queued job: dry does not wait for it
        st = self.tick()
        self.assertEqual(len(self.svc.store.list()), 1)
        L = [l for l in self.logs if l.startswith('[DRY]')]
        self.assertTrue(any('would wait first: 1 job' in l for l in L))
        self.assertTrue(any('would queue the planning job' in l for l in L))
        self.assertEqual(len([l for l in L if 'would feed:' in l]), 3)
        self.assertEqual(st['run']['fed'], 3)
        st = self.tick()
        self.assertEqual(len([l for l in self.logs if 'would feed:' in l]), 5)      # second block capped at the remaining 2
        st = self.tick()
        self.assertIn('item count reached (5)', st['run_last']['reason'])

    def test_run_now_takes_block_size(self):
        self.make(mode='dry')
        self.svc.run_stop() if self.svc.load_state().get('run') else None
        self.svc.set_run_settings({'night': {'enabled': False}})
        self.svc.save_state({})
        v = self.svc.run_start_now(0, 4, 2)
        self.assertEqual(v['active']['block_size'], 2)
        self.svc.run_stop()
        self.assertEqual(self.svc.run_start_now(0, 4)['active']['block_size'], R.BLOCK_DEFAULT)
        self.svc.run_stop()
        for bad in (0, 21, 'x', True, 2.5):
            with self.assertRaises(StoreError):
                self.svc.run_start_now(0, 4, bad)


class Settings(Base):
    def test_block_size_validation_and_defaults(self):
        self.assertEqual(R.DEFAULTS['night']['block_size'], 5)
        self.assertEqual(R.clean_block(None), 5)
        self.assertEqual(R.clean_block('20'), 20)
        for bad in (0, 21, -1, 'x', True, 2.5):
            with self.assertRaises(ValueError):
                R.clean_block(bad)
        d = R.defaults_from({'night_run_block_size': 8, 'day_run_block_size': 99})
        self.assertEqual((d['night']['block_size'], d['day']['block_size']), (8, 5))   # bad option: built-in default
        self.make()
        self.svc.set_run_settings({'day': {'block_size': 3}})
        self.assertEqual(self.svc.run_settings()['day']['block_size'], 3)
        with self.assertRaises(StoreError):
            self.svc.set_run_settings({'day': {'block_size': 50}})
        self.assertEqual(self.svc.run_settings()['day']['block_size'], 3)


if __name__ == '__main__':
    unittest.main()
