"""Taps held on the Kindle and sent together: add-on side."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import device_frames
import device_surface

PNG = b'\x89PNG\r\n\x1a\nfixture'


def zone(box, action, preview='none'):
    return {'box': box, 'power': 'battery', 'preview': preview, 'action': action}


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.single, self.many = [], []
        self.store = device_frames.DeviceFrames(lambda d: self.single.append(d) or {'ok': True}, dispatch_many=lambda ds: self.many.append(ds) or {'ok': True})
        key = self.store.issue(PNG, [zone([0, 0, 10, 10], {'kind': 'cook', 'revision': 'r', 'action': 'done', 'item_id': 'a'}, 'batch'),
                                      zone([0, 20, 10, 30], {'kind': 'cook', 'revision': 'r', 'action': 'done', 'item_id': 'b'}, 'batch'),
                                      zone([0, 40, 10, 50], {'kind': 'cook', 'revision': 'r', 'action': 'stop', 'item_id': None})])
        self.key = key
        self.store.activate(key, self.store.bundle(key)['sha256'])
        self.ids = [z['id'] for z in self.store.bundle(key)['zones']]

    def test_two_zones_are_dispatched_together_once(self):
        self.assertTrue(self.store.tap(self.key, self.ids[0] + ',' + self.ids[1])['ok'])
        self.assertEqual(len(self.many), 1)
        self.assertEqual([d['item_id'] for d in self.many[0]], ['a', 'b'])
        self.assertEqual(self.single, [])
        self.assertFalse(self.store.tap(self.key, self.ids[0] + ',' + self.ids[1])['ok'])   # both already used
        self.assertEqual(len(self.many), 1)

    def test_a_used_zone_is_skipped_but_the_rest_still_go(self):
        self.assertTrue(self.store.tap(self.key, self.ids[0])['ok'])
        self.assertTrue(self.store.tap(self.key, self.ids[0] + ',' + self.ids[1])['ok'])
        self.assertEqual([d['item_id'] for d in self.many[0]], ['b'])

    def test_bad_lists_are_refused(self):
        self.assertFalse(self.store.tap(self.key, self.ids[0] + ',' + self.ids[0])['ok'])
        self.assertFalse(self.store.tap(self.key, self.ids[0] + ',zzz')['ok'])
        self.assertFalse(self.store.tap('f' * 32, self.ids[0] + ',' + self.ids[1])['ok'])
        self.assertEqual(self.many, [])

    def test_surface_sends_one_cooking_request(self):
        posts = []
        surface = device_surface.DeviceSurface(None, None, None, type('C', (), {'post': lambda s, path, body: posts.append((path, body)) or {'ok': True}})(), None, None, None, lambda: {})
        result = surface.dispatch_many([{'kind': 'cook', 'revision': 'r', 'action': 'done', 'item_id': 'a'}, {'kind': 'cook', 'revision': 'r', 'action': 'tick', 'item_id': 'b'}])
        self.assertTrue(result['ok'])
        self.assertEqual(posts, [('/cook/display-actions', {'revision': 'r', 'actions': [{'action': 'done', 'item_id': 'a'}, {'action': 'tick', 'item_id': 'b'}]})])


if __name__ == '__main__': unittest.main()
