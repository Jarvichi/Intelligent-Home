import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import list_bridge


class FakeHA:
    def __init__(self):
        self.data = {'needs_action':[{'uid':'a','summary':'Example'}], 'completed':[{'uid':'b','summary':'Other'}]}
        self.calls = []
    def get_items(self, entity, status): return self.data[status]
    def call(self, domain, service, data): self.calls.append((domain, service, data))


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.ha = FakeHA()
        self.bridge = list_bridge.ShoppingList(self.ha, 'todo.fixture')

    def test_read_is_nonmutating_and_keeps_completed(self):
        rows = self.bridge.items()
        self.assertEqual([x['uid'] for x in rows['open']], ['a'])
        self.assertEqual([x['uid'] for x in rows['completed']], ['b'])
        self.assertEqual(self.ha.calls, [])

    def test_toggle_and_undo_require_existing_uid_in_right_state(self):
        self.bridge.set_completed('a', True)
        self.bridge.set_completed('b', False)
        self.assertEqual([call[2]['status'] for call in self.ha.calls], ['completed', 'needs_action'])
        for uid, state in [('a', False), ('missing', True)]:
            with self.assertRaises(list_bridge.ListError): self.bridge.set_completed(uid, state)
        self.assertEqual(len(self.ha.calls), 2)

    def test_add_validates_without_sending_or_removing(self):
        self.bridge.add(' Example item ')
        self.assertEqual(self.ha.calls[0][1], 'add_item')
        self.assertEqual(self.ha.calls[0][2]['item'], 'Example item')
        for bad in ('', 'a\nb', 'x' * 256):
            with self.assertRaises(list_bridge.ListError): self.bridge.add(bad)
        self.assertEqual(len(self.ha.calls), 1)


if __name__ == '__main__': unittest.main()
