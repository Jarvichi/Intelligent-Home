import json, os, sys, tempfile, unittest, urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import inbox_events as ie

ME, BOT = '1111', '9'


class Resp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Opener:
    def __init__(self, fail=False): self.sent, self.fail = [], fail
    def __call__(self, req, timeout=0):
        if self.fail: raise urllib.error.URLError('down')
        self.sent.append((req.full_url, req.headers.get('Authorization'), json.loads(req.data)))
        return Resp()


def msg(i, uid=ME, content='hi', bot=False, reactions=None, atts=None):
    return {'id': str(i), 'content': content, 'timestamp': '2026-11-24T10:15:00.000000+00:00', 'author': {'id': uid, 'bot': bot},
            'attachments': atts or [], 'reactions': reactions or []}


class Chan:
    """Fake Discord: get(path) over a message list and a reactions table {(msg, emoji): [users]}."""
    def __init__(self, msgs, users=None): self.msgs, self.users = msgs, users or {}
    def __call__(self, path):
        if '/reactions/' in path:
            mid = path.split('/messages/')[1].split('/')[0]
            emoji = path.split('/reactions/')[1].split('?')[0]
            from urllib.parse import unquote
            return [{'id': u, 'bot': u == BOT} for u in self.users.get((mid, unquote(emoji)), [])]
        if 'after=' in path:
            after = int(path.split('after=')[1].split('&')[0])
            return [m for m in self.msgs if int(m['id']) > after]
        return sorted(self.msgs, key=lambda m: -int(m['id']))[:25 if 'limit=25' in path else 1]


class T(unittest.TestCase):
    def mk(self, live=True, fail=False):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.op = Opener(fail); self.logs = []
        return ie.InboxEvents('http://x:8800/', 'tok', live, self.tmp.name, self.logs.append, opener=self.op)

    def test_safe_ref_and_lines(self):
        self.assertEqual(ie.safe_ref('55', '\U0001f44d'), '55-u1f44d')
        self.assertEqual(ie.safe_ref('55', 'blob:12'), '55-blob:12')
        self.assertEqual(ie.dm_line(msg(1, content='hello')), '10:15 UTC: hello')
        self.assertEqual(ie.dm_line(msg(1, content='', atts=[{'url': 'https://u/a.png'}])), '10:15 UTC: (attachment/sticker) https://u/a.png')
        self.assertEqual(ie.reaction_line('x', '5', 'a\nb'), 'x on message 5: a b')

    def test_dm_first_run_does_not_replay_then_posts_only_the_person(self):
        ib = self.mk(); ch = Chan([msg(10)])
        self.assertEqual(ib.step_dm('dm', ch, 'C', ME, 'dm-house'), 0)
        self.assertEqual(self.op.sent, [])
        ch.msgs += [msg(11, content='from me'), msg(12, uid=BOT, bot=True, content='bot reply'), msg(13, uid='77', content='stranger')]
        self.assertEqual(ib.step_dm('dm', ch, 'C', ME, 'dm-house'), 1)
        url, auth, body = self.op.sent[0]
        self.assertEqual((url, auth), ('http://x:8800/events', 'Bearer tok'))
        self.assertEqual(body, {'kind': 'dm', 'source': 'dm-house', 'ref': 'dm-11', 'text': '10:15 UTC: from me'})
        self.assertEqual(ib.step_dm('dm', ch, 'C', ME, 'dm-house'), 0)   # nothing twice
        self.assertEqual(len(self.op.sent), 1)

    def test_dm_failed_post_is_retried_not_lost(self):
        ib = self.mk(); ch = Chan([msg(10)])
        ib.step_dm('dm', ch, 'C', ME, 's')
        ch.msgs.append(msg(11))
        self.op.fail = True
        self.assertEqual(ib.step_dm('dm', ch, 'C', ME, 's'), 0)
        self.assertEqual(ib.failed, 1)
        self.op.fail = False
        self.assertEqual(ib.step_dm('dm', ch, 'C', ME, 's'), 1)
        self.assertEqual(self.op.sent[0][2]['ref'], 'dm-11')

    def test_shadow_sends_nothing_logs_no_text_and_moves_on(self):
        ib = self.mk(live=False); ch = Chan([msg(10)])
        ib.step_dm('dm', ch, 'C', ME, 's'); ch.msgs.append(msg(11, content='secret words'))
        self.assertEqual(ib.step_dm('dm', ch, 'C', ME, 's'), 1)
        self.assertEqual(self.op.sent, [])
        self.assertTrue(any('SHADOW' in l for l in self.logs))
        self.assertFalse(any('secret' in l for l in self.logs))
        self.assertEqual(ib.step_dm('dm', ch, 'C', ME, 's'), 0)
        self.assertEqual(ib.status()['mode'], 'shadow')

    def test_reactions_only_from_the_person_on_bot_messages(self):
        up = {'name': '\U0001f44d', 'id': None}
        ch = Chan([msg(20, uid=BOT, bot=True, content='question?', reactions=[{'emoji': up, 'count': 2, 'me': True}]),
                   msg(21, uid=ME, content='mine', reactions=[{'emoji': up, 'count': 1, 'me': False}])],
                  {('20', up['name']): [BOT, ME], ('21', up['name']): [ME]})
        ib = self.mk()
        self.assertEqual(ib.step_reactions('c', ch, 'C', [ME], 'claude-channel'), 0)   # first run: baseline
        ch.msgs.append(msg(22, uid=BOT, bot=True, content='second', reactions=[{'emoji': up, 'count': 2, 'me': True}]))
        ch.users[('22', up['name'])] = [BOT, '77']                       # a stranger reacted: ignored
        self.assertEqual(ib.step_reactions('c', ch, 'C', [ME], 'claude-channel'), 0)
        ch.users[('22', up['name'])] = [BOT, ME]
        self.assertEqual(ib.step_reactions('c', ch, 'C', [ME], 'claude-channel'), 1)
        body = self.op.sent[0][2]
        self.assertEqual((body['kind'], body['source'], body['ref']), ('reaction', 'claude-channel', '22-u1f44d'))
        self.assertEqual(body['text'], '\U0001f44d on message 22: second')
        self.assertEqual(ib.step_reactions('c', ch, 'C', [ME], 'claude-channel'), 0)

    def test_bots_own_reaction_only_is_skipped_without_a_users_call(self):
        up = {'name': 'x', 'id': None}; calls = []
        ch0 = Chan([msg(30, uid=BOT, bot=True, reactions=[{'emoji': up, 'count': 1, 'me': True}])])
        def ch(path): calls.append(path); return ch0(path)
        ib = self.mk(); ib.step_reactions('c', ch, 'C', [ME], 's'); ib.step_reactions('c', ch, 'C', [ME], 's')
        self.assertFalse(any('/reactions/' in c for c in calls))

    def test_state_survives_restart(self):
        ib = self.mk(); ch = Chan([msg(10)]); ib.step_dm('dm', ch, 'C', ME, 's'); ch.msgs.append(msg(11)); ib.step_dm('dm', ch, 'C', ME, 's')
        ib2 = ie.InboxEvents('http://x:8800', 'tok', True, self.tmp.name, self.logs.append, opener=self.op)
        self.assertEqual(ib2.step_dm('dm', ch, 'C', ME, 's'), 0)


class ModuleWiring(unittest.TestCase):
    """The add-on's Module: options -> thread setup -> one round against a fake Discord."""
    def setUp(self):
        import main as m
        self.m = m; self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)

    def opts(self, **kw):
        p = os.path.join(self.tmp.name, 'o.json')
        json.dump(dict({'discord_token': 'T', 'inbox_user_id': ME}, **kw), open(p, 'w'))
        return self.m.load_options(p)

    def test_defaults_are_shadow_and_off_without_a_user(self):
        o = self.opts()
        self.assertFalse(o['inbox_live'])
        self.assertEqual(o['inbox_url'], 'http://172.30.32.1:8800')
        mod = self.m.Module(self.opts(inbox_user_id=''), discord=object(), data=self.tmp.name)
        mod.tdiscord = object()
        self.assertIsNone(mod.build_inbox())

    def test_live_without_token_does_not_start(self):
        mod = self.m.Module(self.opts(inbox_events='live'), discord=object(), data=self.tmp.name)
        mod.tdiscord = object()
        self.assertIsNone(mod.build_inbox())

    def test_round_posts_dm_and_both_reaction_sources(self):
        up = {'name': 'ok', 'id': None}
        dm = Chan([msg(10)])
        chan = Chan([msg(40, uid=BOT, bot=True, reactions=[{'emoji': up, 'count': 2, 'me': True}])], {('40', 'ok'): [BOT, ME]})
        class FakeD:
            def req(s, method, path, body=None):
                if method == 'POST': return 200, {'id': 'DMCH'}
                return 200, (dm if '/channels/DMCH/' in path else chan)(path)
        mod = self.m.Module(self.opts(inbox_events='live', inbox_token='t', inbox_claude_channel='CC'), discord=object(), data=self.tmp.name)
        mod.tdiscord = FakeD()
        ib = mod.build_inbox(); ib.opener = Opener()
        self.assertTrue(ib.live)
        self.assertEqual(mod.inbox_tick(), 0)                      # baseline round
        dm.msgs.append(msg(11, content='ping'))
        chan.msgs.append(msg(41, uid=BOT, bot=True, content='new', reactions=[{'emoji': up, 'count': 2, 'me': True}]))
        chan.users[('41', 'ok')] = [BOT, ME]
        self.assertEqual(mod.inbox_tick(), 2)
        got = sorted((b['kind'], b['source']) for _, _, b in ib.opener.sent)
        self.assertEqual(got, [('dm', 'dm-house'), ('reaction', 'claude-channel')])
        self.assertEqual(mod.status()['inbox_events']['sent'], 2)


if __name__ == '__main__':
    unittest.main()
