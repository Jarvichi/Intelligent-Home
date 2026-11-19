"""Display actions time out before entering the engine; oven retries stop with their cook."""
import pathlib
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'app'))
import cookengine

class Engine(cookengine.Engine):
    @property
    def live(self):return True

class Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.queued=[];self.engine=Engine({'mode':'live'},tmp.name,spawn=self.queued.append)
    def test_busy_action_does_not_execute_after_lock_is_released(self):
        held=threading.Event();release=threading.Event()
        def hold():
            with self.engine.lock:held.set();release.wait(5)
        owner=threading.Thread(target=hold);owner.start();self.assertTrue(held.wait(2))
        result=self.engine.display_action('a'*64,'stop');self.assertEqual(result['why'],'controller busy')
        with self.assertRaises(TimeoutError):self.engine.display_snapshot()
        release.set();owner.join(2);self.assertFalse(owner.is_alive())
        self.assertFalse(self.engine.cook_cancel.is_set())
    def test_busy_start_lock_does_not_reserve_request(self):
        held=threading.Event();release=threading.Event()
        def hold():
            with self.engine.start_lock:held.set();release.wait(5)
        owner=threading.Thread(target=hold);owner.start();self.assertTrue(held.wait(2))
        result=self.engine.start_from_display({},'example','19:00','a'*32)
        self.assertEqual(result['why'],'controller busy');self.assertFalse(pathlib.Path(self.engine.p('display-requests.json')).exists())
        release.set();owner.join(2)
    def test_queued_start_and_set_are_cancelled_by_stop(self):
        calls=[];self.engine._svc=lambda *args:(calls.append(args) or (True,''));self.engine._signpost=lambda *args:None
        self.engine.oven_action({'action':'start'},session=True);self.engine.oven_action({'action':'set','temp':180},session=True)
        self.engine.stop_cook()
        for work in self.queued:work()
        self.assertEqual(calls,[])
    def test_running_retry_is_cancelled_by_stop(self):
        started=threading.Event();calls=[]
        self.engine._signpost=lambda *args:None
        def send(*args):calls.append(args);started.set();return False,'busy'
        self.engine._svc=send
        self.engine.oven_action({'action':'start'},session=True);worker=threading.Thread(target=self.queued[0]);worker.start()
        self.assertTrue(started.wait(2));self.engine.stop_cook();worker.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(len(calls),1)

    def test_stop_waits_for_inflight_session_dispatch_and_blocks_later_calls(self):
        started=threading.Event();release=threading.Event();calls=[]
        self.addCleanup(release.set)
        def svc(*args):
            calls.append(args);started.set();release.wait(3);return True,''
        self.engine._svc=svc;self.engine._signpost=lambda *args:None
        self.engine.oven_action({'action':'start'},session=True)
        worker=threading.Thread(target=self.queued[0]);worker.start();self.assertTrue(started.wait(2))
        # A bounded device STOP reports busy rather than acknowledging while an
        # oven request is still being dispatched.
        self.assertEqual(self.engine.stop_cook(lock_timeout=.05)['why'],'controller busy')
        self.assertFalse(self.engine.cook_cancel.is_set())
        release.set();self.assertTrue(self.engine.stop_cook(lock_timeout=2)['ok'])
        worker.join(2);self.assertFalse(worker.is_alive());self.assertEqual(len(calls),1)
        self.assertEqual(self.engine._cook_svc(self.engine.cook_cancel,'start_program',{}),(False,'cancelled'))
        self.assertEqual(len(calls),1)

    def test_manual_oven_dispatch_is_not_bound_to_stopped_session(self):
        calls=[];self.engine._svc=lambda *args:(calls.append(args) or (True,''))
        self.engine.stop_cook()
        self.assertEqual(self.engine._cook_svc(None,'start_program',{}),(True,''))
        self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()
