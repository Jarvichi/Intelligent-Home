"""0.6.0: REVIEW phase (DRAIN -> PLAN -> REVIEW -> BLOCK), review_batch_size setting, bounded wait, cancel on end, dry mode."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import runs as R
import test_phases as TP


class Review(TP.Phases):
    # reuse the Phases helpers (make/tick/kinds/work); the inherited tests run again with review off (default review=0 in make)
    def test_phase_order_drain_plan_review_block(self):
        self.make(block=3, review=7)
        self.add('x')
        st = self.tick()
        self.assertEqual((st['run']['phase'], self.kinds()), ('drain', ['other']))          # DRAIN: nothing fed
        self.assertEqual(self.work(), 'other')
        st = self.tick()
        self.assertEqual((st['run']['phase'], self.kinds()), ('plan', ['plan_todos']))
        self.assertEqual(self.work(), 'plan_todos')
        st = self.tick()
        self.assertEqual((st['run']['phase'], self.kinds()), ('review', ['review_batch']))
        it = self.svc.store.list(('queued',))[0]
        self.assertTrue(it['ref'].startswith('review-%s-1' % st['run']['id']))
        self.tick(); self.assertEqual(self.kinds(), ['review_batch'])                       # waiting: no second job, no block
        self.assertEqual(self.work(), 'review_batch')
        st = self.tick()
        self.assertEqual((st['run']['phase'], self.kinds()), ('block', ['todo'] * 3))

    def test_review_job_does_not_count_as_item(self):
        self.make(block=2, review=5)
        self.tick(); self.work(); self.tick(); self.work(); self.tick()
        st = self.svc.load_state()
        self.assertEqual((st['run']['fed'], st['run']['started']), (2, 0))
        self.work()
        self.assertEqual(self.svc.load_state()['run']['started'], 1)                         # only the to-do counts

    def test_zero_skips_review(self):
        self.make(block=2, review=0)
        self.tick(); self.assertEqual(self.work(), 'plan_todos')
        st = self.tick()
        self.assertEqual((st['run']['phase'], self.kinds()), ('block', ['todo'] * 2))
        self.assertNotIn('review_batch', [i['kind'] for i in self.svc.store.list()])

    def test_wait_is_bounded_and_queued_job_cancelled(self):
        self.make(block=2, review=5)
        self.tick(); self.work(); self.tick()
        self.assertEqual(self.kinds(), ['review_batch'])
        self.tick(secs=R.REVIEW_WAIT_MIN * 60 - 120)
        self.assertEqual(self.kinds(), ['review_batch'])                                     # still waiting
        st = self.tick(secs=200)
        self.assertEqual(self.kinds('cancelled'), ['review_batch'])
        self.assertEqual((st['run']['phase'], self.kinds()), ('block', ['todo'] * 2))
        self.assertTrue(any('review job took more than' in l for l in self.logs))

    def test_failed_review_carries_on(self):
        self.make(block=2, review=5)
        self.tick(); self.work(); self.tick()
        c = self.svc.claim('w1')
        self.assertEqual(c['item']['kind'], 'review_batch')
        self.svc.store.finish(c['item']['id'], 'w1', False, error='boom')
        st = self.tick()
        self.assertEqual((st['run']['phase'], self.kinds()), ('block', ['todo'] * 2))
        self.assertTrue(any('review job failed' in l for l in self.logs))

    def test_run_end_cancels_queued_review_job(self):
        self.make(block=2, review=5)
        self.tick(); self.work(); self.tick()
        self.assertEqual(self.kinds(), ['review_batch'])
        self.pace(2.0)                                                                       # pace target reached? run waits until something started: stop it instead
        self.svc.run_stop()
        self.assertEqual(self.kinds('cancelled'), ['review_batch'])
        self.assertEqual(self.kinds(), [])

    def test_dry_mode_only_logs_the_review_job(self):
        self.make(items=4, block=2, review=6, mode='dry')
        self.tick()
        self.assertEqual(self.svc.store.list(), [])
        self.assertTrue(any('[DRY] would queue the review job (review_batch, 6 answers)' in l for l in self.logs))

    def test_dry_zero_logs_nothing_about_review(self):
        self.make(items=4, block=2, review=0, mode='dry')
        self.tick()
        self.assertFalse(any('review job' in l for l in self.logs))

    def test_phase_text(self):
        self.assertIn('reviewing', R.phase_text({'phase': 'review'}))


class Settings(unittest.TestCase):
    def test_validation(self):
        base = R.DEFAULTS['night']
        self.assertEqual(R.clean_one({}, base)['review_batch_size'], 10)
        for v, want in ((0, 0), (30, 30), ('7', 7), (None, 10), ('', 10)):
            self.assertEqual(R.clean_one({'review_batch_size': v}, base)['review_batch_size'], want)
        for bad in (-1, 31, 2.5, True, 'x', [], {}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                R.clean_one({'review_batch_size': bad}, base)

    def test_defaults_from_options(self):
        s = R.defaults_from({'night_run_review_batch_size': 4, 'day_run_review_batch_size': 99})
        self.assertEqual((s['night']['review_batch_size'], s['day']['review_batch_size']), (4, 10))   # bad option -> built-in default

    def test_new_run_carries_it(self):
        import datetime
        self.assertEqual(R.new_run('now', 0, None, datetime.datetime.now(), review_batch_size=3)['review_batch_size'], 3)


if __name__ == '__main__':
    unittest.main()
