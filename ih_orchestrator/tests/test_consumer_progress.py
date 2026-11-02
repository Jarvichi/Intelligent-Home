import unittest,tempfile,json,os
from pathlib import Path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from consumer_progress import Monitor,evaluate

def sample(t,produced=3,acked=0,oldest=0,epoch='queue-v1',heartbeat=None):
    d={'epoch':epoch,'observed_at':t,'produced_seq':produced,'acked_seq':acked,'oldest_pending_at':oldest,'enabled':True}
    if heartbeat is not None:d['heartbeat_at']=heartbeat
    return d

class ProgressTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'progress.json'
    self.monitor=Monitor(self.path,stall_after=300,startup_grace=60,max_snapshot_age=120)
 def tearDown(self):self.tmp.cleanup()
 def test_idle_not_stalled_without_traffic(self):
    r=self.monitor.sample('worker',sample(1000,produced=0),1000)
    self.assertEqual(r['status'],'idle')
 def test_startup_grace(self):
    self.assertEqual(self.monitor.sample('worker',sample(100),100)['reason'],'observation_grace')
 def test_backlog_stall_even_with_healthy_heartbeat(self):
    self.monitor.sample('worker',sample(100),100)
    r=self.monitor.sample('worker',sample(401,heartbeat=401),401)
    self.assertEqual(r['status'],'stalled');self.assertEqual(r['heartbeat_age_s'],0)
 def test_restart_keeps_no_progress_period(self):
    self.monitor.sample('worker',sample(100),100)
    restarted=Monitor(self.path,stall_after=300,startup_grace=60,max_snapshot_age=120)
    self.assertEqual(restarted.sample('worker',sample(401),401)['status'],'stalled')
 def test_progress_marks_catching_up_then_idle(self):
    self.monitor.sample('worker',sample(100),100)
    self.assertEqual(self.monitor.sample('worker',sample(401,acked=1),401)['status'],'catching_up')
    self.assertEqual(self.monitor.sample('worker',sample(402,acked=3),402)['status'],'idle')
 def test_recent_pending_within_deadline(self):
    self.monitor.sample('worker',sample(100,oldest=100),100)
    self.assertEqual(self.monitor.sample('worker',sample(200,oldest=100),200)['reason'],'pending_within_deadline')
 def test_new_pending_does_not_inherit_old_stall(self):
    self.monitor.sample('worker',sample(100,produced=0),100)
    self.assertEqual(self.monitor.sample('worker',sample(1000,oldest=1000),1000)['reason'],'observation_grace')
 def test_stale_snapshot_unknown_not_healthy(self):
    self.assertEqual(self.monitor.sample('worker',sample(100),1000)['reason'],'stale_snapshot')
 def test_future_or_invalid_input_unknown(self):
    for d in [sample(200),sample(100,acked=4),sample(100,produced=True),sample(100,oldest=200),sample(100,oldest=None)]:
        with self.subTest(d=d):self.assertEqual(self.monitor.sample('worker',d,100)['status'],'unknown')
 def test_same_epoch_regression_refused(self):
    self.monitor.sample('worker',sample(100,acked=1),100)
    self.assertEqual(self.monitor.sample('worker',sample(110,acked=0),110)['reason'],'watermark_regression')
 def test_new_epoch_has_fresh_grace(self):
    self.monitor.sample('worker',sample(100,acked=1),100)
    self.assertEqual(self.monitor.sample('worker',sample(1000,epoch='queue-v2'),1000)['reason'],'observation_grace')
 def test_old_snapshot_does_not_rewind_checkpoint(self):
    self.monitor.sample('worker',sample(100),100);before=self.path.read_bytes()
    self.assertEqual(self.monitor.sample('worker',sample(90),110)['reason'],'out_of_order_snapshot')
    self.assertEqual(self.path.read_bytes(),before)
 def test_disabled_no_new_checkpoint(self):
    self.assertEqual(self.monitor.sample('worker',sample(100),100,enabled=False)['status'],'disabled')
    self.assertFalse(self.path.exists())
 def test_corrupt_state_unknown_and_preserved(self):
    self.path.write_text('{broken');before=self.path.read_bytes()
    self.assertEqual(self.monitor.sample('worker',sample(100),100)['reason'],'checkpoint_unavailable')
    self.assertEqual(self.path.read_bytes(),before)
 def test_private_mode_no_message_content(self):
    self.monitor.sample('worker',sample(100),100)
    self.assertEqual(self.path.stat().st_mode&0o777,0o600)
    d=json.loads(self.path.read_text());self.assertEqual(set(d),{'version','consumers'})
    self.assertNotIn('text',self.path.read_text())
 def test_independent_consumers(self):
    self.monitor.sample('one',sample(100),100);self.monitor.sample('two',sample(100,acked=3),100)
    self.assertEqual(self.monitor.sample('one',sample(401),401)['status'],'stalled')
    self.assertEqual(self.monitor.sample('two',sample(401,acked=3),401)['status'],'idle')
if __name__=='__main__':unittest.main()
