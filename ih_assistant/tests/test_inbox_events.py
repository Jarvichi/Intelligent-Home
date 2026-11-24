"""Persona reactions -> orchestration inbox (0.24.0). The shared module itself is tested in ih_discord/tests; here the persona wiring."""
import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
from main import Assistant, load_options

UP = {'name': '\U0001f44d', 'id': None}


class Resp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeDiscord:
    def __init__(self): self.msgs, self.users = [], {}
    def open_dm(self, uid): return 'DM'
    def call(self, method, path, body=None):
        if '/reactions/' in path:
            mid = path.split('/messages/')[1].split('/')[0]
            return [{'id': u} for u in self.users.get(mid, [])]
        return list(self.msgs)


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = [1000.0]; self.sent = []

    def make(self, live, react=True, token='tok'):
        p = os.path.join(self.tmp.name, 'o.json')
        json.dump({'inbox_events': 'live' if live else 'shadow', 'inbox_token': token,
                   'personas': [{'key': 'pp', 'user_id': '42', 'bot_token': 'bt', 'inbox_reactions': react}]}, open(p, 'w'))
        opts, _ = load_options(p)
        a = Assistant(opts, self.tmp.name, lambda *x: None, clock=lambda: self.now[0])
        a.discord['pp'] = FakeDiscord()
        if a.inbox:
            a.inbox.opener = lambda req, timeout=0: (self.sent.append(json.loads(req.data)), Resp())[1]
        return a

    def react(self, a, mid, who):
        a.discord['pp'].msgs.append({'id': mid, 'content': 'question', 'author': {'id': '9', 'bot': True},
                                     'reactions': [{'emoji': UP, 'count': 2, 'me': True}]})
        a.discord['pp'].users[mid] = who

    def test_options_default_to_shadow_and_off_per_persona(self):
        a = self.make(False, react=False)
        self.assertIsNone(a.inbox)
        self.assertEqual(self.make(False).opts['inbox_url'], 'http://172.30.32.1:8800')
        self.assertFalse(self.make(False).opts['inbox_live'])

    def test_live_posts_only_the_persons_new_reaction(self):
        a = self.make(True)
        self.assertEqual(a.tick_inbox('pp'), 0)                 # baseline
        self.now[0] += 30
        self.react(a, '77', ['9', '99']); self.assertEqual(a.tick_inbox('pp'), 0)   # stranger
        self.now[0] += 30
        a.discord['pp'].users['77'] = ['9', '42']; self.assertEqual(a.tick_inbox('pp'), 1)
        self.assertEqual(self.sent, [{'kind': 'reaction', 'source': 'dm-pp', 'ref': '77-u1f44d', 'text': '\U0001f44d on message 77: question'}])
        self.now[0] += 30; self.assertEqual(a.tick_inbox('pp'), 0)

    def test_throttled_and_shadow_sends_nothing(self):
        a = self.make(False)
        a.tick_inbox('pp'); self.react(a, '78', ['42'])
        self.assertEqual(a.tick_inbox('pp'), 0)                 # same second: throttled
        self.now[0] += 30
        self.assertEqual(a.tick_inbox('pp'), 1)
        self.assertEqual(self.sent, [])

    def test_live_without_token_is_off(self):
        self.assertIsNone(self.make(True, token='').inbox)


if __name__ == '__main__':
    unittest.main()
