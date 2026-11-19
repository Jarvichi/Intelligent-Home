import pathlib
import sys
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
from device_frames import DeviceFrames

PNG = b'\x89PNG\r\n\x1a\nfixture'


class DeviceFrameTests(unittest.TestCase):
    def setUp(self):
        self.now = [100]
        self.called = []
        self.frames = DeviceFrames(lambda action: self.called.append(action) or {'ok': True}, clock=lambda: self.now[0])
        self.zones = [{'box': [10, 20, 100, 120], 'action': {'kind': 'message', 'expected': 'one'}}]

    def frame(self):
        key = self.frames.issue(PNG, self.zones)
        return self.frames.bundle(key)

    def test_public_bundle_never_contains_server_action(self):
        frame = self.frame()
        self.assertEqual(frame['image'], PNG)
        self.assertNotIn('action', frame['zones'][0])
        self.assertNotIn('expected', repr(frame))
        self.assertFalse(self.frames.activate(frame['frame'], 'bad'))
        self.assertTrue(self.frames.activate(frame['frame'], frame['sha256']))
        self.assertTrue(self.frames.tap(frame['frame'], frame['zones'][0]['id'])['ok'])
        self.assertEqual(self.called, [self.zones[0]['action']])
        self.assertFalse(self.frames.tap(frame['frame'], frame['zones'][0]['id'])['ok'])

    def test_switching_screen_invalidates_old_and_cannot_reactivate_it(self):
        first, second = self.frame(), self.frame()
        self.assertTrue(self.frames.activate(first['frame'], first['sha256']))
        self.assertTrue(self.frames.activate(second['frame'], second['sha256']))
        self.assertFalse(self.frames.activate(first['frame'], first['sha256']))
        self.assertFalse(self.frames.tap(first['frame'], first['zones'][0]['id'])['ok'])
        self.assertTrue(self.frames.tap(second['frame'], second['zones'][0]['id'])['ok'])

    def test_expiration_and_forged_ids_fail_closed(self):
        frame = self.frame(); self.frames.activate(frame['frame'], frame['sha256'])
        self.assertFalse(self.frames.tap(frame['frame'], '0' * 32)['ok'])
        self.now[0] += 900
        self.assertTrue(self.frames.tap(frame['frame'], frame['zones'][0]['id'])['ok'])
        pending=self.frame();self.now[0] += 900
        self.assertIsNone(self.frames.bundle(pending['frame']))

    def test_duplicate_concurrent_taps_dispatch_once(self):
        frame = self.frame(); self.frames.activate(frame['frame'], frame['sha256'])
        barrier = threading.Barrier(3); results = []
        def tap():
            barrier.wait(); results.append(self.frames.tap(frame['frame'], frame['zones'][0]['id']))
        threads = [threading.Thread(target=tap) for _ in range(2)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        self.assertEqual(sum(x['ok'] for x in results), 1)
        self.assertEqual(len(self.called), 1)

    def test_bad_geometry_and_exception_are_rejected(self):
        with self.assertRaises(ValueError): self.frames.issue(PNG, [{'box': [0, 0, 1073, 5], 'action': {}}])
        failing = DeviceFrames(lambda action: 1 / 0)
        key = failing.issue(PNG, self.zones); frame = failing.bundle(key); failing.activate(key, frame['sha256'])
        self.assertFalse(failing.tap(key, frame['zones'][0]['id'])['ok'])
        self.assertFalse(failing.tap(key, frame['zones'][0]['id'])['ok'])

    def test_inputs_copied_at_issue(self):
        frame = self.frame(); self.zones[0]['action']['expected'] = 'two'
        self.frames.activate(frame['frame'], frame['sha256'])
        self.frames.tap(frame['frame'], frame['zones'][0]['id'])
        self.assertEqual(self.called[0]['expected'], 'one')

if __name__ == '__main__': unittest.main()
