"""task #7: say_list pauses music once and resumes; a late anchor DONE asks first; resume mode keeps the plan's times."""
import datetime, json, pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'app'))
import cookengine as C


class FakeHA:
    def __init__(self, playing=()): self.calls = []; self.playing = set(playing)
    def call(self, path, data=None):
        if data is None:
            e = path.rsplit('/', 1)[-1]; return {'state': 'playing' if e in self.playing else 'idle', 'attributes': {'volume_level': 0.3}}
        self.calls.append((path.split('/services/')[-1], data.get('entity_id')))
        if path.endswith('media_pause'): self.playing.discard(data['entity_id'])
        if path.endswith('media_play'): self.playing.add(data['entity_id'])
        return {}


class Base(unittest.TestCase):
    def mk(self, ha=None, hour=17, minute=0, mode='dry', cfg=None):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.t = [1000.0]; self.clock = datetime.datetime(2026, 11, 22, hour, minute)
        c = {'mode': mode, 'player_entity': 'media_player.k', 'player_group': ['media_player.k', 'media_player.d']}; c.update(cfg or {})
        self.e = C.Engine(c, self.tmp.name, ha=ha, now=lambda: self.clock, ts=lambda: self.t[0], sleep=lambda s: None, spawn=lambda f: f())
        return self.e


class SayList(Base):
    def test_music_paused_once_and_resumed(self):
        ha = FakeHA(playing=['media_player.d']); e = self.mk(ha, cfg={'mode': 'live', 'confirm': C.LIVE_PHRASE, 'other_none': True})
        self.assertTrue(e.live); e.discord_send = lambda *a, **k: None
        e.say_list('Intro', ['eggs', 'milk'], 'Done', True)
        names = [c[0] for c in ha.calls]
        self.assertEqual(names.count('media_player/media_pause'), 1)
        self.assertEqual(names.count('media_player/media_play'), 1)
        self.assertEqual(names.count('tts/speak'), 4)
        self.assertLess(names.index('media_player/media_pause'), names.index('tts/speak'))
        self.assertGreater(names.index('media_player/media_play'), len(names) - 3)


class LateDone(Base):
    def setup_plan(self, now_min):
        e = self.mk(hour=17, minute=now_min)
        steps = [{'at': '17:00', 'text': 'Roast in', 'short': 'Roast in', 'anchor': True}, {'at': '17:30', 'text': 'Gravy', 'short': 'Gravy'}]
        self.keys = [s['at'] + ' ' + s['text'] for s in steps]
        e.dump('plan.json', {'title': 'T', 'eat': '18:00', 'steps': steps}); e.dump('state.json', {'done': [], 'told': self.keys[:1]}); e.dump('shownext.json', self.keys[:1])
        e.discord_send = lambda ch, text=None, embed=None: self.sent.append(text); self.sent = []
        return e

    def test_on_time_done_does_not_ask(self):
        e = self.setup_plan(5); self.assertTrue(e.done(0)['ok'])

    def test_late_done_asks_then_second_tap_confirms_and_moves_plan(self):
        e = self.setup_plan(40)
        r = e.done(0); self.assertFalse(r['ok']); self.assertEqual(r['why'], 'confirm late'); self.assertEqual(r['late_min'], 40)
        self.assertEqual(e.load('state.json', {})['done'], []); self.assertEqual(len(self.sent), 1); self.assertIn('Did you really finish', self.sent[0])
        self.assertEqual(e.load('plan.json', {})['steps'][1]['at'], '17:30')   # nothing moved yet
        self.t[0] += 30; r = e.done(0); self.assertTrue(r['ok'])
        self.assertEqual(e.load('plan.json', {})['eat'], '18:40')

    def test_did_it_never_asks(self):
        e = self.setup_plan(40); self.assertTrue(e.done(0, did=True)['ok']); self.assertEqual(e.load('plan.json', {})['eat'], '18:00')

    def test_confirmation_expires(self):
        e = self.setup_plan(40); e.done(0); self.t[0] += 500
        self.assertEqual(e.done(0)['why'], 'confirm late')


class Resume(Base):
    R = {'title': 'Roast', 'steps': [{'before': 60, 'text': 'Oven on', 'short': 'Oven on'}, {'before': 30, 'text': 'Potatoes in', 'short': 'Pots', 'anchor': True},
                                      {'before': 5, 'text': 'Gravy', 'short': 'Gravy'}], 'shopping': ['beef']}

    def start(self, resume):
        e = self.mk(hour=17, minute=15); e.thread_started = False
        e.running = lambda: False
        orig = C.threading.Thread; C.threading.Thread = lambda **kw: type('T', (), {'start': lambda s: None})()
        self.addCleanup(lambda: setattr(C.threading, 'Thread', orig))
        e.start_cook(json.loads(json.dumps(self.R)), 'roast', '18:00', resume=resume); return e

    def test_resume_keeps_times_and_marks_past_done(self):
        e = self.start(True); st = e.load('state.json', {}); p = e.load('plan.json', {})
        self.assertEqual(sorted(x['at'] for x in p['steps'] if not x.get('auto')), ['16:59', '17:00', '17:30', '17:55'])
        self.assertTrue(all(k in st['done'] for k in ['16:59 Get the ingredients out: beef.', '17:00 Oven on']))
        self.assertNotIn('17:30 Potatoes in', st['done']); self.assertTrue(st['gate_released'])
        gate_open, lag, _ = C.gate_status(p, st, '17:15', lambda a, b: 0)
        self.assertFalse(gate_open)

    def test_without_resume_the_gate_would_slide_the_plan(self):
        e = self.start(False); p = e.load('plan.json', {}); st = e.load('state.json', {})
        gate_open, lag, _ = C.gate_status(p, st, '17:15', lambda a, b: 16)
        self.assertTrue(gate_open); self.assertGreater(lag, 0)


if __name__ == '__main__': unittest.main()
