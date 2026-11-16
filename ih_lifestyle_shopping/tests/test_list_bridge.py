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

    def test_adjust_quantity_changes_only_one_uid(self):
        self.ha.data['needs_action'] = [{'uid':'a','summary':'Example x2'}, {'uid':'b','summary':'Example'}]
        self.bridge.adjust_quantity(['a','b'], -1)
        self.assertEqual(self.ha.calls, [('todo','update_item',{'entity_id':'todo.fixture','item':'a','rename':'Example'})])
        self.ha.calls.clear()
        self.bridge.adjust_quantity(['a','b'], 1)
        self.assertEqual(self.ha.calls[0][2]['rename'], 'Example x3')
        self.assertEqual(len(self.ha.calls), 1)

    def test_decrement_duplicates_removes_only_one_explicit_item(self):
        self.ha.data['needs_action'] = [{'uid':'a','summary':'Example'}, {'uid':'b','summary':'Example'}]
        self.bridge.adjust_quantity(['a','b'], -1)
        self.assertEqual(self.ha.calls, [('todo','remove_item',{'entity_id':'todo.fixture','item':'b'})])
        self.ha.calls.clear()
        with self.assertRaises(list_bridge.ListError): self.bridge.adjust_quantity(['a'], -1)
        with self.assertRaises(list_bridge.ListError): self.bridge.adjust_quantity(['a','missing'], 1)
        self.assertEqual(self.ha.calls, [])

    def test_group_tick_rejects_unrelated_rows_and_reports_ambiguous_failure(self):
        self.ha.data['needs_action'] = [{'uid':'a','summary':'Example'}, {'uid':'b','summary':'Example x2'}, {'uid':'c','summary':'Different'}]
        with self.assertRaises(list_bridge.ListError): self.bridge.complete_group(['a','c'])
        self.assertEqual(self.ha.calls, [])
        def fail_second(domain, service, data):
            if data['item'] == 'b': raise TimeoutError()
            self.ha.calls.append((domain,service,data))
        self.ha.call = fail_second
        result = self.bridge.complete_group(['a','b'])
        self.assertEqual(result, {'updated':['a'], 'uncertain':'b', 'complete':False})
        self.assertEqual(len(self.ha.calls), 1)


if __name__ == '__main__': unittest.main()
