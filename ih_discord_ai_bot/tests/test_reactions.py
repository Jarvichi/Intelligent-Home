"""Thumbs up / down on answers -> AI layer /feedback. Fake client and fake AI layer, no network."""
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault('IH_TEST', '1')
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import main

UP, DOWN = '\U0001F44D', '\U0001F44E'


class FakeClient:
    def __init__(self, watch_ok=True):
        self.posts, self.watches, self.watch_ok, self.dry = [], [], watch_ok, False

    def post(self, ck, text, reply_to=None, tag=None, **kw):
        self.posts.append((ck, text, reply_to))
        return {'ok': True, 'code': 'ok', 'discord_id': '9000%d' % len(self.posts)}

    def watch(self, ck, mid, emojis, ttl_s=180, **kw):
        self.watches.append((ck, mid, list(emojis), ttl_s))
        return {'ok': True, 'code': 'ok'} if self.watch_ok else {'ok': False, 'code': 'denied_op'}


class FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make(shadow=False, watch_ok=True):
    sent = []

    def opener(req, timeout=None):
        sent.append((req.full_url, json.loads(req.data), req.headers.get('Authorization')))
        return FakeResp()
    opts = main.load_options('/nonexistent')
    opts.update(shadow=shadow, allowed_user_ids=['111'], ai_layer_url='http://layer:8801', api_token='tok', channel_keys=['ask'])
    m = main.Module(opts, bus=object(), data=tempfile.mkdtemp(), threaded=False, opener=opener)
    m.client = FakeClient(watch_ok)
    return m, sent


def ev(mid, em, uid='111', added=True):
    return {'type': 'reaction', 'msg_id': mid, 'emoji': em, 'user_id': uid, 'added': added, 'channel_key': 'ask'}


class Reactions(unittest.TestCase):
    def answered(self, m):
        m.reply('ask', '1', 'an answer', rid='a' * 32)
        return m.client.posts and '90001'

    def test_answer_is_tracked_and_watched(self):
        m, _ = make()
        mid = self.answered(m)
        self.assertEqual(m.client.watches, [('ask', mid, [UP, DOWN], 3600)])
        self.assertEqual(m.reacted[mid]['rid'], 'a' * 32)

    def test_thumbs_become_feedback_once_per_user(self):
        m, sent = make()
        mid = self.answered(m)
        m.on_in(ev(mid, DOWN))
        m.on_in(ev(mid, DOWN))                      # same user again: ignored
        self.assertEqual(sent, [('http://layer:8801/feedback', {'request_id': 'a' * 32, 'score': -1}, 'Bearer tok')])
        m2, sent2 = make()
        mid = self.answered(m2)
        m2.on_in(ev(mid, UP))
        self.assertEqual(sent2[0][1]['score'], 1)
        self.assertEqual(m2.feedback_sent, 1)

    def test_ignored_cases(self):
        m, sent = make()
        mid = self.answered(m)
        m.on_in(ev(mid, DOWN, uid='222'))           # not an allowed user
        m.on_in(ev(mid, DOWN, added=False))         # removed reaction
        m.on_in(ev(mid, '\U0001F602'))              # other emoji
        m.on_in(ev('424242', DOWN))                 # not one of our answers
        self.assertEqual(sent, [])

    def test_errors_and_shadow_not_tracked(self):
        m, _ = make()
        m.reply('ask', '1', 'oops', error=True, rid='b' * 32)
        self.assertEqual(m.client.watches, [])
        s, _ = make(shadow=True)
        s.reply('ask', '1', 'an answer', rid='c' * 32)
        self.assertEqual(s.client.watches, [])

    def test_watch_denied_is_harmless(self):
        m, sent = make(watch_ok=False)
        self.answered(m)
        self.assertEqual(sent, [])

    def test_bounded_and_persisted(self):
        m, _ = make()
        for i in range(main.REACT_MAX + 20):
            m.track_answer('ask', str(1000 + i), 'r%d' % i)
        self.assertEqual(len(m.reacted), main.REACT_MAX)
        m2 = main.Module(m.opts, bus=object(), data=m.data, threaded=False)
        self.assertEqual(len(m2.reacted), main.REACT_MAX)

    def test_message_events_still_work(self):
        m, _ = make()
        m.on_in({'type': 'bogus'})
        self.assertEqual(m.in_count, 0)


if __name__ == '__main__':
    unittest.main()
