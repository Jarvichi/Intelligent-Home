"""ih_speaker: guards, engine choice, volume restore, skip-if-playing, queue, dry run. Fake Home Assistant, fake clock, no network."""
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import speaker as sp   # noqa: E402
import main as appmain  # noqa: E402

BED, KIT = 'media_player.bed', 'media_player.kit'


def at(h, m=0):
    return lambda: datetime(2026, 11, 18, h, m, tzinfo=timezone.utc)


class FakeHA:
    """states: entity -> {'state':..., 'attributes':{...}}. A tts.speak makes the speaker 'playing' until the main wait has passed."""
    def __init__(self, play=True):
        self.states = {BED: {'state': 'idle', 'attributes': {'volume_level': 0.55}},
                       KIT: {'state': 'idle', 'attributes': {'volume_level': 0.4}},
                       'tts.piper': {'state': '2026-11-18T10:00:00', 'attributes': {}},
                       'tts.kokoro': {'state': '2026-11-18T10:00:00', 'attributes': {}}}
        self.calls, self.play, self.fail = [], play, None

    def get(self, path, timeout=30):
        e = path.split('/', 1)[1]
        if e not in self.states:
            raise RuntimeError('404')
        return self.states[e]

    def call_service(self, domain, service, data=None):
        if self.fail == (domain, service):
            raise RuntimeError('boom')
        self.calls.append((domain, service, dict(data or {})))
        if (domain, service) == ('media_player', 'volume_set'):
            self.states[data['entity_id']]['attributes']['volume_level'] = data['volume_level']
        if (domain, service) == ('tts', 'speak') and self.play:
            self.states[data['media_player_entity_id']]['state'] = 'playing'

    def sleep(self, n):
        if n >= 3:   # the long wait for the message to finish
            for e in (BED, KIT):
                if self.states[e]['state'] == 'playing':
                    self.states[e]['state'] = 'idle'


OPTS = {
    'mode': 'live', 'api_token': 'tok-1234', 'timezone': 'UTC', 'alert_personas': ['hearth'],
    'speakers': ['bedroom=' + BED, 'kitchen=' + KIT, 'bad=switch.x'],
    'voices': ['hearth=voice-h', 'picard=voice-p'],
    'permissions': ['hearth=*', 'guest=kitchen', 'cara=bedroom,kitchen'],
    'night_deny': ['cara=bedroom'],
    'keep_volume_speakers': ['kitchen'],
}


def make(ha=None, now=at(12), **over):
    ha = ha or FakeHA()
    o = dict(OPTS, **over)
    return sp.Speaker(ha, o, clock=time.time, sleep=ha.sleep, now=now), ha


def req(**k):
    r = {'text': 'Hello there', 'persona': 'hearth', 'speaker': 'bedroom'}
    r.update(k)
    return r


class Options(unittest.TestCase):
    def test_parse(self):
        s, _ = make()
        c = s.cfg
        self.assertEqual(sorted(c['speakers']), ['bedroom', 'kitchen'])   # non media_player entries are dropped
        self.assertEqual(c['permissions']['hearth'], ['*'])
        self.assertEqual(c['engines'], ['tts.piper', 'tts.kokoro'])
        self.assertEqual(sp.parse_options({'mode': 'whatever'})['mode'], 'dry')

    def test_window(self):
        self.assertTrue(sp.in_window(at(23)(), '22:00', '07:00'))
        self.assertTrue(sp.in_window(at(3)(), '22:00', '07:00'))
        self.assertFalse(sp.in_window(at(7)(), '22:00', '07:00'))
        self.assertFalse(sp.in_window(at(12)(), '09:00', '09:00'))

    def test_seconds(self):
        self.assertEqual(sp.speech_seconds('hi'), 5)
        self.assertEqual(sp.speech_seconds('x' * 120), 13)


class Guards(unittest.TestCase):
    def test_unknown_persona_denied(self):
        s, ha = make()
        r = s.speak(req(persona='stranger'))
        self.assertEqual(r['status'], 'blocked')
        self.assertEqual(ha.calls, [])

    def test_persona_speaker_permission(self):
        s, _ = make()
        self.assertEqual(s.speak(req(persona='guest', speaker='bedroom'))['status'], 'blocked')
        self.assertEqual(s.speak(req(persona='guest', speaker='kitchen'))['status'], 'spoken')

    def test_night_deny_but_day_ok(self):
        s, _ = make(now=at(23, 30))
        self.assertEqual(s.speak(req(persona='cara', speaker='bedroom'))['status'], 'blocked')
        self.assertEqual(s.speak(req(persona='cara', speaker='kitchen'))['status'], 'spoken')
        s, _ = make(now=at(15))
        self.assertEqual(s.speak(req(persona='cara', speaker='bedroom'))['status'], 'spoken')

    def test_quiet_hours_alerts_only(self):
        s, ha = make(now=at(3))
        r = s.speak(req())
        self.assertEqual(r['status'], 'blocked')
        self.assertIn('quiet', r['reason'])
        self.assertEqual(s.speak(req(urgency='alert'))['status'], 'spoken')

    def test_alert_not_honoured_for_other_personas(self):
        s, _ = make(now=at(3))
        self.assertEqual(s.speak(req(persona='guest', speaker='kitchen', urgency='alert'))['status'], 'blocked')

    def test_bad_input(self):
        s, _ = make()
        self.assertEqual(s.speak(req(text='  '))['status'], 'blocked')
        self.assertEqual(s.speak(req(text='x' * 601))['status'], 'blocked')
        self.assertEqual(s.speak(req(speaker='garage'))['status'], 'blocked')
        self.assertEqual(s.speak(req(voice='some other voice'))['status'], 'blocked')
        self.assertEqual(s.speak(req(volume='loud'))['status'], 'blocked')


class Playback(unittest.TestCase):
    def test_piper_first_with_voice_and_volume_restore(self):
        s, ha = make()
        r = s.speak(req(voice='picard'))
        self.assertEqual(r['status'], 'spoken')
        self.assertEqual(ha.calls[0], ('media_player', 'volume_set', {'entity_id': BED, 'volume_level': 0.2}))
        self.assertEqual(ha.calls[1][:2], ('tts', 'speak'))
        self.assertEqual(ha.calls[1][2]['entity_id'], 'tts.piper')
        self.assertEqual(ha.calls[1][2]['options'], {'voice': 'voice-p'})
        self.assertEqual(ha.calls[-1], ('media_player', 'volume_set', {'entity_id': BED, 'volume_level': 0.55}))
        self.assertEqual(ha.states[BED]['attributes']['volume_level'], 0.55)

    def test_default_voice_is_the_persona_voice(self):
        s, ha = make()
        s.speak(req())
        self.assertEqual(ha.calls[1][2]['options'], {'voice': 'voice-h'})

    def test_kokoro_gets_no_voice_option(self):
        ha = FakeHA()
        ha.states['tts.piper']['state'] = 'unavailable'
        s, _ = make(ha)
        self.assertEqual(s.speak(req())['status'], 'spoken')
        d = ha.calls[1][2]
        self.assertEqual(d['entity_id'], 'tts.kokoro')
        self.assertNotIn('options', d)

    def test_no_engine_returns_fallback_and_plays_nothing(self):
        ha = FakeHA()
        ha.states['tts.piper']['state'] = 'unavailable'
        del ha.states['tts.kokoro']
        s, _ = make(ha)
        r = s.speak(req())
        self.assertEqual(r['status'], 'fallback')
        self.assertEqual(ha.calls, [])

    def test_speaker_never_starts_returns_fallback_and_restores_volume(self):
        ha = FakeHA(play=False)
        s, _ = make(ha)
        r = s.speak(req())
        self.assertEqual(r['status'], 'fallback')
        self.assertEqual(ha.states[BED]['attributes']['volume_level'], 0.55)

    def test_keep_volume_group_leader(self):
        s, ha = make()
        r = s.speak(req(speaker='kitchen', voice='picard'))
        self.assertEqual(r['status'], 'spoken')
        self.assertFalse([c for c in ha.calls if c[1] == 'volume_set'])

    def test_volume_keep_and_explicit_volume(self):
        s, ha = make()
        s.speak(req(volume='keep'))
        self.assertFalse([c for c in ha.calls if c[1] == 'volume_set'])
        ha.calls.clear()
        s.speak(req(volume=0.35))
        self.assertEqual(ha.calls[0][2]['volume_level'], 0.35)

    def test_volume_restored_when_the_tts_call_fails(self):
        ha = FakeHA()
        ha.fail = ('tts', 'speak')
        s, _ = make(ha)
        r = s.speak(req())
        self.assertEqual(r['status'], 'error')
        self.assertEqual(ha.states[BED]['attributes']['volume_level'], 0.55)

    def test_skip_if_playing(self):
        ha = FakeHA()
        ha.states[BED]['state'] = 'playing'
        s, _ = make(ha)
        r = s.speak(req())
        self.assertEqual(r['status'], 'skipped')
        self.assertEqual(ha.calls, [])
        self.assertEqual(s.speak(req(skip_if_playing=False))['status'], 'spoken')

    def test_dry_run_plays_nothing_and_reports_the_plan(self):
        s, ha = make()
        r = s.speak(req(dry_run=True, voice='picard'))
        self.assertEqual(r['status'], 'dry')
        self.assertEqual((r['engine'], r['voice'], r['entity']), ('tts.piper', 'voice-p', BED))
        self.assertEqual(r['volume'], {'set': 0.2, 'restore_to': 0.55})
        self.assertTrue(r['allowed'])
        self.assertEqual(ha.calls, [])

    def test_dry_run_reports_blocked_with_reason(self):
        s, ha = make(now=at(2))
        r = s.speak(req(dry_run=True))
        self.assertEqual(r['status'], 'blocked')
        self.assertFalse(r['allowed'])
        self.assertTrue(r['reason'])

    def test_dry_mode_never_plays(self):
        s, ha = make(mode='dry')
        self.assertEqual(s.speak(req())['status'], 'dry')
        self.assertEqual(ha.calls, [])


class Queue(unittest.TestCase):
    def test_full_and_expired(self):
        t = sp.Turns(1)
        self.assertEqual(t.enter(5), 'ok')       # takes the turn, stays busy
        out = []
        th = threading.Thread(target=lambda: out.append(t.enter(0.2)))
        th.start()
        time.sleep(0.05)
        self.assertEqual(t.enter(5), 'full')      # one is already waiting
        th.join()
        self.assertEqual(out, ['expired'])
        t.leave()
        self.assertEqual(t.enter(1), 'ok')

    def test_first_come_first_served(self):
        t = sp.Turns(5)
        self.assertEqual(t.enter(5), 'ok')
        order = []

        def worker(n):
            if t.enter(5) == 'ok':
                order.append(n)
                t.leave()
        ths = []
        for n in range(3):
            th = threading.Thread(target=worker, args=(n,))
            th.start()
            ths.append(th)
            time.sleep(0.1)
        t.leave()
        for th in ths:
            th.join()
        self.assertEqual(order, [0, 1, 2])

    def test_speak_reports_queue_full(self):
        s, ha = make(queue_max=1)
        s.turns['bedroom'].enter(5)     # someone is speaking
        w = threading.Thread(target=lambda: s.turns['bedroom'].enter(1))
        w.start()
        time.sleep(0.1)                  # one is waiting: the line is full
        self.assertEqual(s.speak(req())['status'], 'queue_full')
        s.turns['bedroom'].leave()
        w.join()
        self.assertEqual(ha.calls, [])


class Http(unittest.TestCase):
    def test_auth(self):
        c = sp.parse_options(OPTS)
        self.assertTrue(appmain.authed(c, 'Bearer tok-1234'))
        self.assertFalse(appmain.authed(c, 'Bearer nope'))
        self.assertFalse(appmain.authed(c, None))
        self.assertFalse(appmain.authed(sp.parse_options({}), 'Bearer '))   # no token set = everything refused

    def test_history_has_no_message_text(self):
        s, _ = make()
        s.speak(req(text='a private sentence'))
        self.assertNotIn('private', str(list(s.history)))


if __name__ == '__main__':
    unittest.main()
