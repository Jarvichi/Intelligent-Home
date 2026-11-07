import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from store import PersonaStore
from main import Assistant


class FakeDiscord:
    def __init__(self):
        self.last_id = '555'
    def call(self, method, path, body=None):
        if path.endswith('/messages/555'):
            return {'reactions': [{'emoji': {'name': '❤️', 'id': None}, 'count': 1, 'me': False}]}
        if '/reactions/' in path:
            return [{'id': '42'}]
        return {}


class WatchTests(unittest.TestCase):
    def make(self):
        a = Assistant.__new__(Assistant)
        self.tmp = tempfile.TemporaryDirectory(); self.now = [100000.0]
        a.clock = lambda: self.now[0]; a.log = lambda *x: None
        a.cfg = {'pp': {'user_id': '42'}}
        a.stores = {'pp': PersonaStore(self.tmp.name, 'pp', a.clock)}
        a.discord = {'pp': FakeDiscord()}; a._channel = lambda k: 'ch'
        return a

    def test_plain_message_reaction_becomes_event(self):
        a = self.make()
        a.watch_sent('pp')
        self.assertEqual(a.stores['pp'].state()['watch'][0]['msg_id'], '555')
        self.assertEqual(a.tick_reactions('pp'), 1)
        self.assertEqual(a.stores['pp'].state()['react_seen'], ['555:❤️'])
        self.now[0] += 30
        self.assertEqual(a.tick_reactions('pp'), 0)   # not reported twice

    def test_watch_list_is_capped_and_expires(self):
        a = self.make()
        for i in range(12):
            a.discord['pp'].last_id = str(i); a.watch_sent('pp')
        self.assertEqual(len(a.stores['pp'].state()['watch']), 8)
        self.now[0] += 22000
        a.discord['pp'].last_id = '99'; a.watch_sent('pp')
        self.assertEqual([x['msg_id'] for x in a.stores['pp'].state()['watch']], ['99'])


if __name__ == '__main__':
    unittest.main()
