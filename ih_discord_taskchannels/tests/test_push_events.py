"""Push events: claims follow the open task channels, events hold no text, /events needs the token. In-memory bus only, no network."""
import json, os, sys, tempfile, threading, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import bus_client, events, main

KEY = 'k' * 32
TOKEN = 't' * 24
MOD = 'ih_discord_taskchannels'
SECRET_TEXT = 'PRIVATE-MESSAGE-TEXT'


def opts(**kw):
    o = {'module': MOD, 'signing_key': KEY, 'log_payloads': False, 'shadow': False, 'command_channel_key': 'tasks', 'wip_category': 'wip',
         'done_category': 'done', 'template': '', 'cleanup_days': 30, 'mqtt_host': 'x', 'mqtt_port': 1, 'mqtt_user': 'x', 'mqtt_password': 'x',
         'ingest_url': 'http://x', 'ingest_token': '', 'push_events': True, 'events_token': TOKEN}
    o.update(kw)
    return o


def task(key, state='wip'):
    return {'name': 'n', 'key': key, 'id': '1', 'state': state, 'created': 1, 'moved': None, 'summary': ''}


class Base(unittest.TestCase):
    def make(self, tasks=None, **kw):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        if tasks is not None:
            with open(os.path.join(self.tmp.name, 'state.json'), 'w') as f:
                json.dump({'tasks': tasks}, f)
        self.bus = bus_client.FakeBus()
        self.n = 0
        m = main.Module(opts(**kw), bus=self.bus, data=self.tmp.name, threaded=False)
        self.assertTrue(m.start())
        return m

    def send_in(self, ck, allowed=True, typ='message', mid=None):
        self.n += 1
        topic = bus_client.topic_in(MOD, ck)
        body = {'type': typ, 'id': 'm:%d' % self.n, 'msg_id': mid or str(1000 + self.n), 'channel_key': ck, 'allowed': allowed, 'content': SECRET_TEXT}
        self.bus.publish(topic, bus_client.encode(KEY, 'in', MOD, topic, body))

    def claimed(self, m):
        return sorted(c['channel_key'] for c in m.client.claims)

    def hellos(self):
        return len(self.bus.topics('hello'))


class Claims(Base):
    def test_off_claims_only_commands_and_events_404(self):
        m = self.make({'a': task(MOD + '.a')}, push_events=False)
        self.assertEqual(self.claimed(m), ['tasks'])
        self.send_in(MOD + '.a')
        self.assertEqual(m.events.last, 0)
        self.assertEqual(m.events_answer('Bearer ' + TOKEN, 0, 0)[0], 404)
        self.assertFalse(m.status()['push_events']['on'])

    def test_on_claims_open_channels_only(self):
        m = self.make({'a': task(MOD + '.a'), 'b': task(MOD + '.b', 'done'), 'bad': task('not a key!')})
        self.assertEqual(self.claimed(m), [MOD + '.a', 'tasks'])
        self.assertEqual(m.status()['push_events']['claimed'], 1)

    def test_done_and_reopen_change_claims_and_say_hello(self):
        m = self.make({'a': task(MOD + '.a')})
        h = self.hellos()
        m.tasks['a']['state'] = 'done'
        self.assertTrue(m.claim_tasks())
        self.assertEqual(self.claimed(m), ['tasks'])
        self.assertEqual(self.hellos(), h + 1)
        self.assertFalse(m.claim_tasks())            # nothing changed: no extra hello
        self.assertEqual(self.hellos(), h + 1)
        self.send_in(MOD + '.a')                     # a done channel wakes nobody
        self.assertEqual(m.events.last, 0)
        m.tasks['a']['state'] = 'wip'
        self.assertTrue(m.claim_tasks())
        self.send_in(MOD + '.a')
        self.assertEqual(m.events.last, 1)           # exactly one event, although the topic was subscribed twice

    def test_channel_gone_drops_claim(self):
        m = self.make({'a': task(MOD + '.a')})
        m.on_admin({'type': 'channel_gone', 'channel_key': MOD + '.a'})
        self.assertEqual(self.claimed(m), ['tasks'])


class Events(Base):
    def test_event_has_ids_and_no_text(self):
        m = self.make({'a': task(MOD + '.a')})
        self.send_in(MOD + '.a', mid='555')
        code, d = m.events_answer('Bearer ' + TOKEN, 0, 0)
        self.assertEqual(code, 200)
        self.assertEqual([(e['n'], e['channel_key'], e['msg_id']) for e in d['events']], [(1, MOD + '.a', '555')])
        self.assertNotIn(SECRET_TEXT, json.dumps(d))
        self.assertNotIn(SECRET_TEXT, json.dumps(m.status()))
        self.assertNotIn(SECRET_TEXT, ' '.join(m.ring.lines))
        self.assertEqual(m.events_answer('Bearer ' + TOKEN, 1, 0)[1]['events'], [])

    def test_not_allowed_and_reactions_ignored(self):
        m = self.make({'a': task(MOD + '.a')})
        self.send_in(MOD + '.a', allowed=False)
        self.send_in(MOD + '.a', typ='reaction')
        self.assertEqual(m.events.last, 0)

    def test_task_message_is_not_a_command(self):
        m = self.make({'a': task(MOD + '.a')})
        out = len(self.bus.topics('out'))
        self.send_in(MOD + '.a')
        self.assertEqual(len(self.bus.topics('out')), out)   # nothing posted, created or moved
        self.assertEqual(m.in_count, 0)

    def test_token(self):
        m = self.make({})
        for bad in (None, '', 'Bearer ', 'Bearer wrong', TOKEN, 'Basic ' + TOKEN):
            self.assertEqual(m.events_answer(bad, 0, 0)[0], 401)
        for tok in ('', 'short'):
            m.opts['events_token'] = tok
            self.assertEqual(m.events_answer('Bearer ' + tok, 0, 0)[0], 401)   # empty or short token: all refused


class Log(unittest.TestCase):
    def test_gap_boot_and_bad_numbers(self):
        e = events.EventLog(max_events=3, boot='b1')
        for i in range(5):
            e.add('k', i)
        d = e.since(0)
        self.assertEqual(([x['n'] for x in d['events']], d['gap'], d['boot'], d['last']), ([3, 4, 5], True, 'b1', 5))
        self.assertFalse(e.since(2)['gap'])
        self.assertEqual(len(e.since(99)['events']), 3)       # a number from an older run: start again
        self.assertEqual(len(e.since('zz', 'zz')['events']), 3)
        self.assertEqual(e.since(5)['events'], [])

    def test_long_poll_wakes_on_event(self):
        e = events.EventLog()
        threading.Timer(0.05, lambda: e.add('k', '1')).start()
        d = e.since(0, wait=5)
        self.assertEqual(len(d['events']), 1)
        self.assertEqual(e.since(1, wait=0.01)['events'], [])   # times out empty


class Options(unittest.TestCase):
    def test_default_off(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, 'o.json')
            for raw, want in (({}, False), ({'push_events': 'true'}, False), ({'push_events': True, 'events_token': ' x '}, True)):
                json.dump(raw, open(p, 'w'))
                o = main.load_options(p)
                self.assertIs(o['push_events'], want)
            self.assertEqual(o['events_token'], 'x')


if __name__ == '__main__':
    unittest.main()
