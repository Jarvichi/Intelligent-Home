"""ctx.publish + Host.republish_all: sensors published by REST come back after an HA restart. Fake opener, tmp /data, no network."""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import pluginhost as P
from jobs import common


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHA:
    """states: entity -> state text. mode: 'ok' | 'down'. Records POSTs."""
    def __init__(self):
        self.states, self.posts, self.down = {}, [], False

    def opener(self, req, timeout=30):
        if self.down:
            raise urllib.error.URLError('down')
        path = req.full_url.split('/api/', 1)[1]
        ent = path[len('states/'):]
        if req.method == 'POST':
            body = json.loads(req.data)
            self.posts.append((ent, body))
            self.states[ent] = body['state']
            return Resp(b'{}')
        if ent not in self.states:
            raise urllib.error.HTTPError(req.full_url, 404, 'nf', {}, None)
        return Resp(json.dumps({'state': self.states[ent]}).encode())


class FakeMod:
    DEFAULTS = {}
    SCHEDULE = {'kind': 'every', 'minutes': 60}
    NAME = 'pub'

    @staticmethod
    def run(ctx):
        ctx.publish('sensor.x', '42', {'unit': 'u'})
        return True


def make(live=True, ha=None):
    d = tempfile.mkdtemp()
    ha = ha or FakeHA()
    opts = {'plugins': [{'name': 'pub', 'live': live, 'settings': ''}]}
    h = P.Host(opts, data=d, log=lambda *_: None, token='t', opener=ha.opener, connect=lambda url: (_ for _ in ()).throw(OSError('down')), plugins={'pub': FakeMod})
    return h, ha, d


class T(unittest.TestCase):
    def test_publish_saves_and_posts(self):
        h, ha, d = make()
        self.assertTrue(h.run('pub'))
        self.assertEqual(ha.posts[0][0], 'sensor.x')
        with open(os.path.join(d, 'pub', 'published.json')) as f:
            saved = json.load(f)
        self.assertEqual(saved['sensor.x']['state'], '42')
        self.assertEqual(saved['sensor.x']['attributes'], {'unit': 'u'})

    def test_republish_after_missing_or_unavailable(self):
        h, ha, d = make()
        h.run('pub')
        ha.posts.clear()
        del ha.states['sensor.x']                      # HA restarted: 404
        self.assertEqual(h.republish_all(only_missing=True), 1)
        self.assertEqual(ha.posts[0][1]['state'], '42')
        ha.posts.clear()
        ha.states['sensor.x'] = 'unavailable'
        self.assertEqual(h.republish_all(only_missing=True), 1)
        ha.states['sensor.x'] = 'unknown'
        self.assertEqual(h.republish_all(only_missing=True), 1)

    def test_no_repost_when_state_fine(self):
        h, ha, d = make()
        h.run('pub')
        ha.posts.clear()
        self.assertEqual(h.republish_all(only_missing=True), 0)
        self.assertEqual(ha.posts, [])

    def test_republish_all_always_posts(self):
        h, ha, d = make()
        h.run('pub')
        ha.posts.clear()
        self.assertEqual(h.republish_all(), 1)

    def test_dry_plugin_posts_and_saves_nothing(self):
        h, ha, d = make(live=False)
        h.run('pub')
        self.assertEqual(ha.posts, [])
        self.assertFalse(os.path.exists(os.path.join(d, 'pub', 'published.json')))
        self.assertEqual(h.republish_all(), 0)

    def test_dry_after_saved_posts_nothing(self):
        h, ha, d = make()
        h.run('pub')
        ha.posts.clear()
        h.cfg['pub']['live'] = False
        self.assertEqual(h.republish_all(), 0)

    def test_kill_switch_posts_nothing(self):
        h, ha, d = make()
        h.run('pub')
        ha.posts.clear()
        open(os.path.join(d, 'STOP_pub'), 'w').close()
        self.assertEqual(h.republish_all(), 0)
        os.remove(os.path.join(d, 'STOP_pub'))
        open(os.path.join(d, 'STOP_LIVE'), 'w').close()
        self.assertEqual(h.republish_all(), 0)
        self.assertEqual(ha.posts, [])

    def test_ha_down_does_not_raise(self):
        h, ha, d = make()
        h.run('pub')
        ha.down = True
        self.assertEqual(h.republish_all(only_missing=True), 0)
        self.assertEqual(h.republish_all(), 0)
        self.assertFalse(h.republish_events_once())    # subscribe refused -> False, no raise

    def test_started_event_triggers_republish(self):
        h, ha, d = make()
        h.run('pub')
        ha.posts.clear()
        seen = []

        def sub(event_type, on_event, max_seconds=600, on_idle=None, poll=1.0):
            seen.append(event_type)
            on_event({})
        h.ha.subscribe_events = sub
        self.assertTrue(h.republish_events_once())
        self.assertEqual(seen, ['homeassistant_started'])
        self.assertEqual(len(ha.posts), 1)

    def test_legacy_set_state_saved_and_republished(self):
        h, ha, d = make()
        c = common.Ctx(dry=False, log=lambda *_: None, token='t', opener=ha.opener, data_dir=d)
        self.assertTrue(c.set_state('sensor.old', 'ok', {'a': 1}))
        ha.posts.clear()
        del ha.states['sensor.old']
        self.assertEqual(h.republish_all(only_missing=True), 1)
        self.assertEqual(ha.posts[0][0], 'sensor.old')
        d2 = common.Ctx(dry=True, log=lambda *_: None, token='t', opener=ha.opener, data_dir=tempfile.mkdtemp())
        self.assertFalse(d2.set_state('sensor.old', 'ok', {}))


if __name__ == '__main__':
    unittest.main()
