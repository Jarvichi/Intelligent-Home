"""ih_speaker voice clips: ffmpeg wrapper (fake process), waveform, render through HA (fake), Discord voice message (fake opener), delivery,
fallback, dry run, guards. No network, no ffmpeg, nothing is sent."""
import base64
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import speaker as sp        # noqa: E402
import voice                # noqa: E402
from ihlib import discord   # noqa: E402
from test_speaker import FakeHA, OPTS, at, make as make_base, req   # noqa: E402

VOPTS = {'discord_token': 'tok-secret', 'discord_users': ['alex=1234567890', 'sam=987654321', 'bad=notanumber'],
         'discord_permissions': ['hearth=alex', 'cara=*']}


def pcm(vals):
    return b''.join(int(v).to_bytes(2, 'little', signed=True) for v in vals)


class FakeRun:
    """Stands in for subprocess.run: s16le request -> pcm, ogg request -> fake ogg bytes."""
    def __init__(self, samples=(0, 1000, -2000, 500) * 4000, rc=0):
        self.samples, self.rc, self.cmds = samples, rc, []

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        out = pcm(self.samples) if 's16le' in cmd else b'OggS-fake-opus'
        return subprocess.CompletedProcess(cmd, self.rc, stdout=out, stderr=b'')


class Encode(unittest.TestCase):
    def test_encode(self):
        run = FakeRun()
        ogg, secs, wave = voice.encode(b'wav-bytes', run)
        self.assertEqual(ogg, b'OggS-fake-opus')
        self.assertEqual(secs, 2.0)   # 16000 samples at 8 kHz
        raw = base64.b64decode(wave)
        self.assertEqual(len(raw), 256)
        self.assertEqual(max(raw), 255)
        ogg_cmd = [c for c in run.cmds if 'libopus' in c][0]
        for part in ('-ac', '1', '-ar', '48000', 'ogg'):
            self.assertIn(part, ogg_cmd)

    def test_waveform_short_and_silent(self):
        self.assertEqual(len(base64.b64decode(voice.waveform([0, 10, 5]))), 3)
        self.assertEqual(set(base64.b64decode(voice.waveform([0] * 50))), {0})
        self.assertEqual(len(base64.b64decode(voice.waveform([]))), 1)

    def test_errors(self):
        with self.assertRaises(voice.VoiceError):
            voice.encode(b'', FakeRun())
        with self.assertRaises(voice.VoiceError):
            voice.encode(b'x', FakeRun(rc=1))
        with self.assertRaises(voice.VoiceError):
            voice.encode(b'x', FakeRun(samples=[1] * (voice.RATE_PCM * (voice.MAX_SECONDS + 1))))

        def missing(cmd, **kw):
            raise FileNotFoundError
        with self.assertRaisesRegex(voice.VoiceError, 'not installed'):
            voice.encode(b'x', missing)


class FakeResp:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHAPost:
    rest_base = 'http://supervisor/core/api/'
    token = 'sup-token'

    def __init__(self, path='/api/tts_proxy/abc.wav'):
        self.posts, self.path = [], path

    def post(self, path, data=None, timeout=30):
        self.posts.append((path, data))
        return {'url': 'http://x' + self.path, 'path': self.path}


class Render(unittest.TestCase):
    def test_render_downloads_audio(self):
        ha, seen = FakeHAPost(), []

        def opener(r, timeout=0):
            seen.append(r)
            return FakeResp(b'RIFFaudio')
        self.assertEqual(voice.render(ha, 'tts.piper', 'hi', 'voice-h', opener), b'RIFFaudio')
        self.assertEqual(ha.posts, [('tts_get_url', {'engine_id': 'tts.piper', 'message': 'hi', 'options': {'voice': 'voice-h'}})])
        self.assertEqual(seen[0].full_url, 'http://supervisor/core/api/tts_proxy/abc.wav')

    def test_kokoro_gets_no_voice_and_bad_answers_raise(self):
        ha = FakeHAPost()
        voice.render(ha, 'tts.kokoro', 'hi', 'voice-h', lambda r, timeout=0: FakeResp(b'a'))
        self.assertNotIn('options', ha.posts[0][1])
        with self.assertRaises(voice.VoiceError):
            voice.render(FakeHAPost(path='http://evil/x'), 'tts.piper', 'hi', '', lambda r, timeout=0: FakeResp(b'a'))
        with self.assertRaises(voice.VoiceError):
            voice.render(ha, 'tts.piper', 'hi', '', lambda r, timeout=0: FakeResp(b''))


class DiscordHelper(unittest.TestCase):
    def fake(self, fail_at=None):
        calls = []

        def opener(req, timeout=0):
            body = req.data
            calls.append((req.get_method(), req.full_url, req.headers, body))
            if fail_at == len(calls):
                raise OSError('refused')
            if req.full_url.endswith('/users/@me/channels'):
                return FakeResp(b'{"id": "555"}')
            if req.full_url.endswith('/channels/555/attachments'):
                return FakeResp(json.dumps({'attachments': [{'id': 0, 'upload_url': 'https://cdn.example.invalid/up?x=1', 'upload_filename': 'up/abc/voice-message.ogg'}]}).encode())
            if req.get_method() == 'PUT':
                return FakeResp(b'')
            return FakeResp(b'{"id": "777"}')
        return opener, calls

    def test_three_step_upload(self):
        op, calls = self.fake()
        mid = discord.send_voice_message('tok', 1234567890, b'OGGDATA', 3.14159, b'\x01\x02', opener=op)
        self.assertEqual(mid, '777')
        self.assertEqual([c[0] for c in calls], ['POST', 'POST', 'PUT', 'POST'])
        slot = json.loads(calls[1][3])
        self.assertEqual(slot['files'], [{'filename': 'voice-message.ogg', 'file_size': 7, 'id': '0'}])
        put = calls[2]
        self.assertEqual(put[1], 'https://cdn.example.invalid/up?x=1')
        self.assertEqual(put[3], b'OGGDATA')
        self.assertNotIn('Authorization', put[2])   # the pre-signed url needs no bot token
        msg = json.loads(calls[3][3])
        self.assertEqual(msg['flags'], 8192)
        self.assertNotIn('content', msg)
        a = msg['attachments'][0]
        self.assertEqual((a['filename'], a['uploaded_filename'], a['duration_secs']), ('voice-message.ogg', 'up/abc/voice-message.ogg', 3.14))
        self.assertEqual(a['waveform'], base64.b64encode(b'\x01\x02').decode())

    def test_failures_raise(self):
        for n in (1, 2, 3, 4):
            op, _ = self.fake(fail_at=n)
            with self.assertRaises(Exception):
                discord.send_voice_message('tok', 1, b'x', 1, 'AA==', opener=op)
        with self.assertRaises(RuntimeError):
            discord.send_voice_message('', 1, b'x', 1, 'AA==', opener=self.fake()[0])
        with self.assertRaises(ValueError):
            discord.send_voice_message('tok', 1, b'', 1, 'AA==', opener=self.fake()[0])


class FakeDiscord:
    def __init__(self, voice_fails=False, attach_fails=False, caption_fails=False):
        self.sent, self.voice_fails, self.attach_fails, self.caption_fails = [], voice_fails, attach_fails, caption_fails

    def send_voice_message(self, tok, user, ogg, secs, wave, **k):
        if self.voice_fails:
            raise RuntimeError('400')
        self.sent.append(('voice', tok, user, ogg, secs, wave))
        return '1'

    def send_dm_rich(self, tok, user, text='', embeds=None, file=None, **k):
        if self.attach_fails:
            raise RuntimeError('500')
        self.sent.append(('file', user, file))
        return '2'

    def send_dm(self, tok, user, text, **k):
        if self.caption_fails:
            raise RuntimeError('500')
        self.sent.append(('text', user, text))
        return '3'


def make(dc=None, audio=None, encode=None, ha=None, now=at(12), **over):
    ha = ha or FakeHA()
    o = dict(OPTS, **dict(VOPTS, **over))
    audio_calls = []

    def a(engine, text, v):
        audio_calls.append((engine, text, v))
        return b'wav'
    s = sp.Speaker(ha, o, sleep=ha.sleep, now=now, audio=audio or a, encode=encode or (lambda b: (b'OGG', 2.5, 'AQID')), discord=dc or FakeDiscord())
    s.audio_calls = audio_calls
    return s, ha


def vreq(**k):
    r = {'text': 'Hello there', 'persona': 'hearth', 'to': 'discord', 'discord_user': 'alex'}
    r.update(k)
    return r


class Delivery(unittest.TestCase):
    def test_sends_voice_message_and_never_touches_the_speaker(self):
        dc = FakeDiscord()
        s, ha = make(dc)
        r = s.speak(vreq())
        self.assertEqual((r['status'], r['ok'], r['delivery']), ('sent', True, 'voice_message'))
        self.assertEqual(dc.sent, [('voice', 'tok-secret', '1234567890', b'OGG', 2.5, 'AQID')])
        self.assertEqual(s.audio_calls, [('tts.piper', 'Hello there', 'voice-h')])   # same engine and persona voice as the speaker
        self.assertEqual(ha.calls, [])   # no volume change, nothing played
        self.assertNotIn('tok-secret', json.dumps(r))

    def test_quiet_hours_do_not_block_a_dm(self):
        s, _ = make(now=at(3))
        self.assertEqual(s.speak(vreq())['status'], 'sent')

    def test_falls_back_to_a_plain_attachment(self):
        dc = FakeDiscord(voice_fails=True)
        r = make(dc)[0].speak(vreq())
        self.assertEqual((r['status'], r['delivery']), ('sent', 'attachment'))
        self.assertEqual(dc.sent, [('file', '1234567890', ('voice-message.ogg', b'OGG'))])
        self.assertIn('plain audio file', r['reason'])

    def test_both_sends_fail_is_an_error(self):
        r = make(FakeDiscord(voice_fails=True, attach_fails=True))[0].speak(vreq())
        self.assertEqual(r['status'], 'error')
        self.assertFalse(r['ok'])

    def test_caption_is_a_separate_message_and_off_by_default(self):
        dc = FakeDiscord()
        make(dc)[0].speak(vreq())
        self.assertEqual([x[0] for x in dc.sent], ['voice'])
        dc = FakeDiscord()
        make(dc)[0].speak(vreq(caption=True))
        self.assertEqual([x[0] for x in dc.sent], ['voice', 'text'])
        dc = FakeDiscord(caption_fails=True)
        r = make(dc)[0].speak(vreq(caption=True))
        self.assertEqual(r['status'], 'sent')
        self.assertIn('caption failed', r['reason'])

    def test_audio_failure_is_an_error_and_sends_nothing(self):
        def boom(b):
            raise voice.VoiceError('ffmpeg is not installed')
        dc = FakeDiscord()
        r = make(dc, encode=boom)[0].speak(vreq())
        self.assertEqual(r['status'], 'error')
        self.assertIn('ffmpeg is not installed', r['reason'])
        self.assertEqual(dc.sent, [])

    def test_no_engine_is_fallback(self):
        ha = FakeHA()
        ha.states['tts.piper']['state'] = ha.states['tts.kokoro']['state'] = 'unavailable'
        dc = FakeDiscord()
        self.assertEqual(make(dc, ha=ha)[0].speak(vreq())['status'], 'fallback')
        self.assertEqual(dc.sent, [])

    def test_missing_token_is_an_error(self):
        dc = FakeDiscord()
        r = make(dc, discord_token='')[0].speak(vreq())
        self.assertEqual(r['status'], 'error')
        self.assertEqual(dc.sent, [])


class DryRun(unittest.TestCase):
    def test_dry_run_reports_duration_and_size_and_sends_nothing(self):
        dc = FakeDiscord()
        s, ha = make(dc)
        r = s.speak(vreq(dry_run=True))
        self.assertEqual((r['status'], r['ok']), ('dry', True))
        self.assertEqual((r['duration_s'], r['audio_bytes'], r['waveform_bytes']), (2.5, 3, 3))
        self.assertTrue(r['token_set'])
        self.assertEqual(dc.sent, [])
        self.assertEqual(ha.calls, [])
        self.assertNotIn('tok-secret', json.dumps(r))

    def test_dry_mode_never_sends(self):
        dc = FakeDiscord()
        r = make(dc, mode='dry')[0].speak(vreq())
        self.assertEqual(r['status'], 'dry')
        self.assertEqual(dc.sent, [])


class Guards(unittest.TestCase):
    def blocked(self, why, **k):
        dc = FakeDiscord()
        r = make(dc)[0].speak(vreq(**k))
        self.assertEqual(r['status'], 'blocked')
        self.assertIn(why, r['reason'])
        self.assertEqual(dc.sent, [])

    def test_blocked_cases(self):
        self.blocked('unknown discord_user', discord_user='nobody')
        self.blocked('unknown discord_user', discord_user='bad')       # not a numeric id
        self.blocked('may not send', persona='picard')                 # persona not listed
        self.blocked('may not send', persona='hearth', discord_user='sam')
        self.blocked('persona is required', persona='')
        self.blocked('text is empty', text=' ')
        self.blocked('longer than', text='x' * 601)
        self.blocked('to must be', to='everyone')
        self.blocked('unknown voice', voice='made-up')

    def test_raw_user_id_is_not_accepted(self):
        self.blocked('unknown discord_user', discord_user='1234567890')

    def test_default_to_is_speaker_and_unchanged(self):
        dc = FakeDiscord()
        s, ha = make(dc)
        r = s.speak({'text': 'Hi', 'persona': 'hearth', 'speaker': 'bedroom'})
        self.assertEqual(r['status'], 'spoken')
        self.assertEqual(dc.sent, [])
        self.assertEqual(s.audio_calls, [])


class Both(unittest.TestCase):
    def test_both_speaks_and_sends(self):
        dc = FakeDiscord()
        s, ha = make(dc)
        r = s.speak(vreq(to='both', speaker='bedroom'))
        self.assertEqual(r['status'], 'spoken')
        self.assertEqual(r['discord']['status'], 'sent')
        self.assertTrue(any(c[:2] == ('tts', 'speak') for c in ha.calls))

    def test_both_in_quiet_hours_still_sends_the_dm(self):
        dc = FakeDiscord()
        s, ha = make(dc, now=at(3))
        r = s.speak(vreq(to='both', speaker='bedroom'))
        self.assertEqual(r['status'], 'blocked')
        self.assertEqual(r['discord']['status'], 'sent')
        self.assertEqual(ha.calls, [])


class Options(unittest.TestCase):
    def test_parse(self):
        c = sp.parse_options(dict(OPTS, **VOPTS))
        self.assertEqual(c['discord_users'], {'alex': '1234567890', 'sam': '987654321'})
        self.assertEqual(c['discord_permissions']['cara'], ['*'])
        c = sp.parse_options({})
        self.assertEqual((c['discord_token'], c['discord_users'], c['discord_permissions']), ('', {}, {}))

    def test_history_and_status_have_no_text_or_token(self):
        s, _ = make()
        s.speak(vreq(text='a private sentence'))
        self.assertNotIn('private', str(list(s.history)))
        self.assertNotIn('tok-secret', str(list(s.history)))


if __name__ == '__main__':
    unittest.main()
