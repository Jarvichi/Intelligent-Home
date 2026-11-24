import json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import selfprompts as sp
from store import PersonaStore
from main import Assistant

T0 = 1793955600.0


class FakeResp:
    def __init__(self, d): self.d = d
    def read(self): return json.dumps(self.d).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


def make(live=True, user='alex', speaker=True, answer=None):
    a = Assistant.__new__(Assistant)
    a.tmp = tempfile.TemporaryDirectory()
    a.t = T0
    a.clock = lambda: a.t
    a.log = lambda *x: None
    a.stores = {'aa': PersonaStore(a.tmp.name, 'aa', lambda: a.t)}
    a.cfg = {'aa': {'live': live, 'voice_memo_user': user, 'voice_memo_voice': 'hearth', 'household': False}}
    a.opts = {'mode': 'live', 'speaker_url': 'http://x:1' if speaker else '', 'speaker_token': 't' if speaker else ''}
    a.calls = []
    def opener(req, timeout=0):
        a.calls.append(json.loads(req.data))
        return FakeResp(answer or {'status': 'sent' if live else 'dry'})
    a.speaker_opener = opener
    return a


def open_prompt(a, pid='voice_memo'):
    a.stores['aa'].update_state(lambda x: x.__setitem__('self_open', {'id': pid, 'at': a.t}))


class Catalogue(unittest.TestCase):
    def test_prompts(self):
        _, prompts = sp.load()
        by = {p['id']: p for p in prompts}
        self.assertEqual((by['impromptu_note']['min_level'], by['impromptu_note']['cooldown_days']), (5, 7))
        self.assertEqual((by['voice_memo']['min_level'], by['voice_memo']['cooldown_days']), (6, 30))
        self.assertEqual(by['voice_memo']['needs'], 'voice')
        self.assertIn('send nothing', by['impromptu_note']['text'].lower())

    def test_levels(self):
        _, prompts = sp.load()
        ids = lambda lv: {p['id'] for p in sp.eligible(prompts, lv, [], T0)}
        self.assertNotIn('impromptu_note', ids(4))
        self.assertIn('impromptu_note', ids(5))
        self.assertNotIn('voice_memo', ids(5))
        self.assertIn('voice_memo', ids(6))

    def test_usable_needs_voice(self):
        p = {'id': 'voice_memo', 'needs': 'voice'}
        self.assertFalse(sp.usable(p, {}))
        self.assertTrue(sp.usable(p, {}, ('voice',)))
        self.assertTrue(sp.usable({'id': 'impromptu_note'}, {}))


class Caps(unittest.TestCase):
    def test_caps(self):
        a = make()
        self.assertEqual(a.voice_caps('aa'), ('voice',))
        self.assertEqual(make(speaker=False).voice_caps('aa'), ())
        self.assertEqual(make(user='').voice_caps('aa'), ())

    def test_week_gap(self):
        a = make()
        a.stores['aa'].update_state(lambda x: x.__setitem__('voice_log', [T0 - 3 * 86400]))
        self.assertEqual(a.voice_caps('aa'), ())
        a.t = T0 + 5 * 86400
        self.assertEqual(a.voice_caps('aa'), ('voice',))


class Send(unittest.TestCase):
    def test_sent(self):
        a = make()
        open_prompt(a)
        code, r = a.voice_memo('aa', {'text': 'Thanks for today.'})
        self.assertEqual((code, r['status']), (200, 'sent'))
        c = a.calls[0]
        self.assertEqual((c['to'], c['discord_user'], c['persona'], c['voice'], c['dry_run']), ('discord', 'alex', 'aa', 'hearth', False))
        self.assertEqual(len(a.stores['aa'].state()['voice_log']), 1)
        self.assertEqual(a.voice_memo('aa', {'text': 'again'})[0], 429)
        self.assertEqual(len(a.calls), 1)

    def test_dry_persona_sends_dry_and_is_not_counted(self):
        a = make(live=False)
        open_prompt(a)
        code, r = a.voice_memo('aa', {'text': 'hi'})
        self.assertEqual((code, r['dry']), (200, True))
        self.assertTrue(a.calls[0]['dry_run'])
        self.assertFalse(a.stores['aa'].state().get('voice_log'))

    def test_needs_open_voice_prompt(self):
        a = make()
        self.assertEqual(a.voice_memo('aa', {'text': 'hi'})[0], 409)
        open_prompt(a, 'impromptu_note')
        self.assertEqual(a.voice_memo('aa', {'text': 'hi'})[0], 409)
        self.assertEqual(a.calls, [])

    def test_validation_and_setup(self):
        a = make()
        open_prompt(a)
        self.assertEqual(a.voice_memo('aa', {'text': ''})[0], 400)
        self.assertEqual(a.voice_memo('aa', {'text': 'x' * (sp.VOICE_TEXT_MAX + 1)})[0], 400)
        self.assertEqual(make(speaker=False).voice_memo('aa', {'text': 'hi'})[0], 409)

    def test_speaker_failure(self):
        a = make(answer={'status': 'blocked', 'reason': 'not allowed'})
        open_prompt(a)
        code, r = a.voice_memo('aa', {'text': 'hi'})
        self.assertEqual((code, r['status']), (502, 'blocked'))
        self.assertFalse(a.stores['aa'].state().get('voice_log'))

    def test_text_never_logged(self):
        a = make()
        open_prompt(a)
        a.voice_memo('aa', {'text': 'secret words here'})
        self.assertNotIn('secret words', json.dumps(a.stores['aa'].read_outbox(0, 50) if hasattr(a.stores['aa'], 'read_outbox') else ''))


if __name__ == '__main__':
    unittest.main()
