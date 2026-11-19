"""ih_speaker clip cache, fillers, eviction, expiry, dry-run report, signed clip links and the admin page API. Fake TTS, fake speaker, temp folder, no network."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import cache as cachelib    # noqa: E402
import main as appmain     # noqa: E402
import speaker as sp       # noqa: E402
import ui_server           # noqa: E402
from test_speaker import BED, FakeHA, OPTS, at, req   # noqa: E402

BASE = 'http://ha.test:8831'
COPTS = dict(OPTS, base_url=BASE, seed_fillers=False)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeTTS:
    """Stands in for the HA renderer: bytes depend on engine, voice and text. Counts calls."""
    def __init__(self, size=100, fail=False):
        self.calls, self.size, self.fail = [], size, fail

    def __call__(self, engine, text, voice):
        self.calls.append((engine, voice, text))
        if self.fail:
            raise RuntimeError('tts down')
        return (b'ID3' + ('%s|%s|%s' % (engine, voice, text)).encode()).ljust(self.size, b'x')


class PlayHA(FakeHA):
    """play_media starts 'playing'; the clip ends after `len_polls` polls of the state. Records every call in order."""
    def __init__(self, len_polls=2, **k):
        super().__init__(**k)
        self.len_polls, self.left, self.events = len_polls, 0, []

    def call_service(self, domain, service, data=None):
        super().call_service(domain, service, data)
        self.events.append((domain, service, (data or {}).get('media_content_id') or (data or {}).get('message') or ''))
        if (domain, service) == ('media_player', 'play_media'):
            self.states[data['entity_id']]['state'] = 'playing'
            self.left = self.len_polls
        if (domain, service) == ('media_player', 'media_stop'):
            self.states[data['entity_id']]['state'] = 'idle'

    def get(self, path, timeout=30):
        r = super().get(path)
        if path.endswith(BED) and self.left > 0 and r['state'] == 'playing':
            self.left -= 1
            if self.left == 0:
                r['state'] = 'idle'
        return r


_OPEN = []


def tearDownModule():
    for c in _OPEN:
        c.db.close()


def build(tmp, ha=None, tts=None, clock=None, **over):
    ha = ha or PlayHA()
    tts = tts or FakeTTS()
    clock = clock or Clock()
    c = cachelib.Cache(os.path.join(tmp, 'cache'), os.path.join(tmp, 'ih.db'),
                       max_bytes=int(over.get('cache_mb', 1) * 1024 * 1024), days=over.get('cache_days', 30), clock=clock)
    _OPEN.append(c)
    o = dict(COPTS, **over)
    s = sp.Speaker(ha, o, clock=clock, sleep=ha.sleep, now=at(12), audio=tts, cache=c)
    return s, ha, tts, c, clock


class CacheStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)

    def mk(self, **k):
        d = dict(max_bytes=1000, days=30, clock=Clock())
        d.update(k)
        c = cachelib.Cache(os.path.join(self.tmp.name, 'c'), os.path.join(self.tmp.name, 'ih.db'), **d)
        _OPEN.append(c)
        return c

    def test_layout_and_normalised_key(self):
        c = self.mk()
        r = c.put('tts.piper', 'voice-a', 'Hello  There', b'ID3abc')
        self.assertEqual(os.path.relpath(c.path(r), c.dir), os.path.join('tts.piper', 'voice-a', cachelib.text_key('hello there') + '.mp3'))
        self.assertEqual(c.find('tts.piper', 'voice-a', ' hello there ')['id'], r['id'])
        self.assertEqual(len(cachelib.text_key('x')), 40)

    def test_same_text_other_voice_or_engine_is_separate(self):
        c = self.mk()
        ids = {c.put(e, v, 'Hi', b'ID3').get('id') for e, v in (('tts.piper', 'a'), ('tts.piper', 'b'), ('tts.kokoro', ''))}
        self.assertEqual(len(ids), 3)
        self.assertEqual(c.total()[1], 3)

    def test_hit_counts_and_miss(self):
        clock = Clock()
        c = self.mk(clock=clock)
        c.put('e', '', 'a', b'ID3')
        clock.t += 50
        r = c.get('e', '', 'A')
        self.assertEqual((r['hits'], r['last_used']), (1, clock.t))
        self.assertIsNone(c.get('e', '', 'other'))

    def test_missing_file_is_a_miss_and_row_dropped(self):
        c = self.mk()
        r = c.put('e', '', 'a', b'ID3')
        os.remove(c.path(r))
        self.assertIsNone(c.get('e', '', 'a'))
        self.assertEqual(c.total()[1], 0)

    def test_extension_follows_audio(self):
        c = self.mk()
        self.assertEqual(c.put('e', '', 'w', b'RIFFxxxx')['ext'], 'wav')
        self.assertEqual(c.put('e', '', 'o', b'OggSxxxx')['ext'], 'ogg')

    def test_lru_eviction_keeps_pinned(self):
        clock = Clock()
        c = self.mk(max_bytes=350, clock=clock)
        a = c.put('e', '', 'a', b'ID3'.ljust(100, b'x'), pinned=True)
        clock.t += 1
        b = c.put('e', '', 'b', b'ID3'.ljust(100, b'x'))
        clock.t += 1
        cc = c.put('e', '', 'c', b'ID3'.ljust(100, b'x'))
        clock.t += 1
        c.get('e', '', 'b')   # b is now newer than c
        clock.t += 1
        c.put('e', '', 'd', b'ID3'.ljust(100, b'x'))
        left = {r['id'] for r in c.list()}
        self.assertEqual(left, {a['id'], b['id'], c.find('e', '', 'd')['id']})   # c (least recently used) went
        self.assertFalse(os.path.exists(c.path(cc)))
        self.assertLessEqual(c.total()[0], 350)

    def test_pinned_never_deleted_even_over_cap(self):
        c = self.mk(max_bytes=50)
        c.put('e', '', 'a', b'ID3'.ljust(100, b'x'), pinned=True)
        c.put('e', '', 'b', b'ID3'.ljust(100, b'x'), pinned=True)
        self.assertEqual(c.total()[1], 2)

    def test_expiry_30_days_not_pinned(self):
        clock = Clock()
        c = self.mk(clock=clock)
        c.put('e', '', 'old', b'ID3')
        c.put('e', '', 'keep', b'ID3', pinned=True)
        clock.t += 31 * 86400
        c.put('e', '', 'new', b'ID3')   # a store also expires
        self.assertEqual(sorted(r['text'] for r in c.list()), ['keep', 'new'])

    def test_plan_evict_changes_nothing(self):
        c = self.mk(max_bytes=250)
        c.put('e', '', 'a', b'ID3'.ljust(100, b'x'))
        c.put('e', '', 'b', b'ID3'.ljust(100, b'x'))
        self.assertEqual([r['text'] for r in c.plan_evict(extra=100)], ['a'])
        self.assertEqual(c.total()[1], 2)

    def test_list_filters(self):
        c = self.mk()
        c.put('tts.piper', 'a', 'Good morning', b'ID3', label='morning')
        c.put('tts.kokoro', '', 'Good night', b'ID3')
        self.assertEqual(len(c.list(engine='tts.kokoro')), 1)
        self.assertEqual(len(c.list(voice='a')), 1)
        self.assertEqual(len(c.list(voice='')), 1)
        self.assertEqual(len(c.list(q='MORNING')), 1)
        self.assertEqual(len(c.list(q='good')), 2)

    def test_pin_unpin_delete(self):
        c = self.mk()
        r = c.put('e', '', 'a', b'ID3')
        c.set_pinned(r['id'], True)
        self.assertTrue(c.by_id(r['id'])['pinned'])
        self.assertTrue(c.delete(r['id']))
        self.assertFalse(c.delete(r['id']))
        self.assertFalse(os.path.exists(c.path(r)))

    def test_reopens_with_same_index(self):
        c = self.mk()
        c.put('e', '', 'a', b'ID3')
        c.db.close()
        c2 = self.mk()
        self.assertEqual(c2.total()[1], 1)


class SpeakWithCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)

    def test_miss_then_hit(self):
        s, ha, tts, c, _ = build(self.tmp.name)
        r1 = s.speak(req())
        self.assertEqual((r1['status'], r1['via']), ('spoken', 'cached clip'))
        self.assertEqual(len(tts.calls), 1)
        ha.states[BED]['state'] = 'idle'
        r2 = s.speak(req(text='hello  THERE'))
        self.assertEqual(r2['status'], 'spoken')
        self.assertEqual(len(tts.calls), 1)   # second time came from the cache
        row = c.list()[0]
        self.assertEqual(row['hits'], 1)
        play = [e for e in ha.events if e[1] == 'play_media']
        self.assertEqual(len(play), 2)
        self.assertTrue(play[0][2].startswith(BASE + '/clip/%d.mp3?exp=' % row['id']))

    def test_voices_do_not_share_clips(self):
        s, ha, tts, c, _ = build(self.tmp.name)
        s.speak(req(voice='hearth'))
        ha.states[BED]['state'] = 'idle'
        s.speak(req(voice='picard'))
        self.assertEqual(len(tts.calls), 2)
        self.assertEqual(c.total()[1], 2)

    def test_without_base_url_uses_tts_speak_and_still_caches(self):
        s, ha, tts, c, _ = build(self.tmp.name, base_url='')
        r = s.speak(req())
        self.assertEqual((r['status'], r['via']), ('spoken', 'tts.speak'))
        time.sleep(0.2)
        self.assertEqual(c.total()[1], 1)

    def test_render_failure_falls_back_to_tts_speak(self):
        s, ha, tts, c, _ = build(self.tmp.name, tts=FakeTTS(fail=True))
        r = s.speak(req())
        self.assertEqual((r['status'], r['via']), ('spoken', 'tts.speak'))
        self.assertEqual(c.total()[1], 0)

    def test_store_evicts_over_cap(self):
        s, ha, tts, c, clock = build(self.tmp.name, tts=FakeTTS(size=400 * 1024))
        for i in range(4):
            clock.t += 1
            ha.states[BED]['state'] = 'idle'
            s.speak(req(text='message %d' % i))
        self.assertLessEqual(c.total()[0], 1024 * 1024)
        self.assertEqual(c.total()[1], 2)
        self.assertEqual(sorted(r['text'] for r in c.list()), ['message 2', 'message 3'])

    def test_dry_run_reports_hit_miss_and_evictions_and_stores_nothing(self):
        s, ha, tts, c, clock = build(self.tmp.name, mode='dry', cache_mb=1, tts=FakeTTS(size=600 * 1024))
        c.put('tts.piper', 'voice-h', 'old one', b'ID3'.ljust(600 * 1024, b'x'))
        r = s.speak(req(dry_run=True, text='brand new'))
        self.assertEqual(r['status'], 'dry')
        self.assertEqual(r['cache']['status'], 'miss')
        self.assertEqual(len(r['cache']['would_evict']), 1)
        self.assertNotIn('text', r['cache']['would_evict'][0])
        self.assertEqual(r['cache']['max_bytes'], 1024 * 1024)
        self.assertEqual(c.total()[1], 1)
        self.assertEqual(tts.calls, [])
        self.assertEqual(ha.calls, [])
        r = s.speak(req(dry_run=True, text='OLD one'))
        self.assertEqual((r['cache']['status'], r['cache']['clip_bytes']), ('hit', 600 * 1024))
        self.assertEqual(c.list()[0]['hits'], 0)   # a dry run does not count as a use

    def test_dry_mode_discord_render_is_not_stored(self):
        s, ha, tts, c, _ = build(self.tmp.name, mode='dry', discord_users=['alex=1234567890'], discord_permissions=['hearth=alex'])
        s._encode = lambda a: (b'ogg', 1.0, 'AAA=')
        r = s.speak({'text': 'Hi', 'persona': 'hearth', 'to': 'discord', 'discord_user': 'alex', 'dry_run': True})
        self.assertEqual(r['status'], 'dry')
        self.assertEqual(c.total()[1], 0)

    def test_discord_render_uses_cache_when_live(self):
        sent = []

        class D:
            def send_voice_message(self, *a):
                sent.append(a)
        s, ha, tts, c, _ = build(self.tmp.name, discord_token='t', discord_users=['alex=1234567890'], discord_permissions=['hearth=alex'])
        s._discord, s._encode = D(), lambda a: (b'ogg', 1.0, 'AAA=')
        body = {'text': 'Hi', 'persona': 'hearth', 'to': 'discord', 'discord_user': 'alex'}
        self.assertEqual(s.speak(body)['status'], 'sent')
        self.assertEqual(s.speak(body)['status'], 'sent')
        self.assertEqual(len(tts.calls), 1)


class Fillers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)

    def test_seed_makes_pinned_phrases_per_voice(self):
        s, ha, tts, c, _ = build(self.tmp.name)
        self.assertEqual(s.seed('voice-h'), 5)
        self.assertEqual(s.seed('voice-h'), 0)
        rows = c.list(voice='voice-h')
        self.assertEqual(sorted(r['label'] for r in rows), ['alarm armed', 'alarm disarmed', 'good morning', 'just checking', 'one moment'])
        self.assertTrue(all(r['pinned'] for r in rows))
        s.seed('voice-p')
        self.assertEqual(c.total()[1], 10)
        pinned_total = c.total()[0]
        c.max_bytes = 10
        c.evict()
        self.assertEqual(c.total()[0], pinned_total)   # pinned survive any cap

    def test_seed_all_covers_default_and_configured_voices(self):
        s, ha, tts, c, _ = build(self.tmp.name)
        self.assertEqual(s.seed_all(), 15)   # '', voice-h, voice-p
        self.assertEqual(s.seed_all(), 0)

    def test_filler_plays_first_and_finishes_before_real_clip(self):
        s, ha, tts, c, _ = build(self.tmp.name)
        s.seed('voice-h')
        ha.states[BED]['state'] = 'idle'
        r = s.speak(req(voice='hearth', filler='One moment', text='The alarm is armed'))
        self.assertEqual((r['status'], r['filler']), ('spoken', 'played'))
        order = [e for e in ha.events if e[1] in ('play_media', 'media_stop')]
        self.assertEqual(len(order), 2)
        urls = [e[2] for e in order]
        self.assertIn('/clip/%d.' % c.find('tts.piper', 'voice-h', 'One moment')['id'], urls[0])
        self.assertIn('/clip/%d.' % c.find('tts.piper', 'voice-h', 'The alarm is armed')['id'], urls[1])
        self.assertEqual(ha.states[BED]['state'], 'idle')

    def test_filler_that_will_not_end_is_stopped_before_the_real_clip(self):
        s, ha, tts, c, _ = build(self.tmp.name, ha=PlayHA(len_polls=10 ** 6))
        s.seed('voice-h')
        ha.states[BED]['state'] = 'idle'
        calls = []
        orig = ha.call_service

        def spy(d, sv, data=None):
            calls.append(sv)
            orig(d, sv, data)
            if sv == 'media_stop':
                ha.left = 0
        ha.call_service = spy
        s.speak(req(voice='hearth', filler='one moment'))
        self.assertLess(calls.index('media_stop'), len(calls) - 1)
        stop = calls.index('media_stop')
        self.assertEqual(calls[stop - 1], 'play_media')      # the filler
        self.assertEqual(calls[stop + 1], 'play_media')      # the real clip only after the stop

    def test_unknown_filler_is_skipped_and_message_still_spoken(self):
        s, ha, tts, c, _ = build(self.tmp.name)
        r = s.speak(req(filler='nonsense phrase'))
        self.assertEqual((r['status'], r['filler']), ('spoken', 'skipped: not cached'))
        self.assertEqual(len([e for e in ha.events if e[1] == 'play_media']), 1)

    def test_filler_without_base_url_is_skipped(self):
        s, ha, tts, c, _ = build(self.tmp.name, base_url='')
        s.seed('voice-h')
        r = s.speak(req(voice='hearth', filler='one moment'))
        self.assertEqual(r['status'], 'spoken')
        self.assertTrue(r['filler'].startswith('skipped'))

    def test_dry_run_reports_filler(self):
        s, ha, tts, c, _ = build(self.tmp.name)
        s.seed('voice-h')
        r = s.speak(req(voice='hearth', filler='one moment', dry_run=True))
        self.assertEqual(r['cache']['filler'], 'hit')
        r = s.speak(req(voice='picard', filler='one moment', dry_run=True))
        self.assertIn('not cached', r['cache']['filler'])

    def test_first_use_seeds_in_background_in_live_mode_only(self):
        s, ha, tts, c, _ = build(self.tmp.name, seed_fillers=True)
        s.speak(req(voice='hearth'))
        for _ in range(50):
            if c.total()[1] >= 6:
                break
            time.sleep(0.05)
        time.sleep(0.3)
        self.assertEqual(c.total()[1], 6)
        d = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(d.cleanup)
        s2, ha2, tts2, c2, _ = build(d.name, mode='dry', seed_fillers=True)
        s2.speak(req(voice='hearth'))
        time.sleep(0.2)
        self.assertEqual(c2.total()[1], 0)


class Links(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.s, self.ha, self.tts, self.c, self.clock = build(self.tmp.name, clock=Clock(time.time()))
        self.srv = appmain.ThreadingHTTPServer(('127.0.0.1', 0), appmain.make_handler(self.s))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.srv.server_address[1]
        self.row = self.c.put('tts.piper', 'v', 'hi', b'ID3audio')

    def get(self, url):
        try:
            with urllib.request.urlopen(url) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b''

    def link(self):
        return self.s.clip_url(self.row).replace(BASE, self.base)

    def test_signed_link_serves_without_bearer(self):
        self.assertEqual(self.get(self.link()), (200, b'ID3audio'))

    def test_bad_signature_expiry_and_other_id_refused(self):
        u = self.link()
        self.assertEqual(self.get(u[:-3] + 'abc')[0], 404)
        self.assertEqual(self.get(u.replace('exp=', 'exp=1'))[0], 404)
        other = self.c.put('tts.piper', 'v', 'other', b'ID3x')
        self.assertEqual(self.get(u.replace('/clip/%d.' % self.row['id'], '/clip/%d.' % other['id']))[0], 404)
        self.assertEqual(self.get(self.base + '/clip/%d.mp3' % self.row['id'])[0], 404)
        self.assertEqual(self.get(self.base + '/clip/..%2fx')[0], 404)

    def test_expired_link(self):
        self.s.clock = lambda: time.time() - 10_000
        u = self.link()
        self.assertEqual(self.get(u)[0], 404)

    def test_no_link_without_base_url_or_token(self):
        self.s.cfg['base_url'] = ''
        self.assertEqual(self.s.clip_url(self.row), '')
        self.s.cfg['base_url'], self.s.cfg['api_token'] = BASE, ''
        self.assertEqual(self.s.clip_url(self.row), '')

    def test_other_routes_still_need_bearer(self):
        self.assertEqual(self.get(self.base + '/status')[0], 401)


class AdminPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.s, self.ha, self.tts, self.c, _ = build(self.tmp.name)
        self.srv = ui_server.make_server(self.s, 0, peer='127.0.0.1', host='127.0.0.1')
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = 'http://127.0.0.1:%d/' % self.srv.server_address[1]

    def call(self, path, body=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        rq = urllib.request.Request(self.base + path, data=data, headers={'Content-Type': 'application/json'} if data else {})
        try:
            with urllib.request.urlopen(rq) as r:
                b = r.read()
                return r.status, b if raw else json.loads(b)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}')

    def test_only_the_ingress_peer_may_connect(self):
        self.srv.shutdown()
        srv = ui_server.make_server(self.s, 0, peer='172.30.32.2', host='127.0.0.1')
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/api/info' % srv.server_address[1])
            self.fail('allowed')
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)

    def test_page_assets_and_info(self):
        st, html = self.call('', raw=True)
        self.assertEqual(st, 200)
        self.assertIn(b'Speaker clips', html)
        st, i = self.call('api/info')
        self.assertEqual((i['speakers'], i['can_play'], i['voices']), (['bedroom', 'kitchen'], True, ['hearth', 'picard']))

    def test_generate_list_filter_pin_delete(self):
        st, d = self.call('api/generate', {'text': 'Hello world', 'voice': 'hearth', 'label': 'hello', 'pin': True})
        self.assertEqual(st, 200)
        cid = d['clip']['id']
        self.assertTrue(d['clip']['pinned'])
        self.call('api/generate', {'text': 'Other', 'voice': 'picard'})
        self.assertEqual(self.call('api/clips')[1]['total'], 2)
        self.assertEqual(self.call('api/clips?voice=hearth')[1]['total'], 1)
        self.assertEqual(self.call('api/clips?voice=*')[1]['total'], 2)
        self.assertEqual(self.call('api/clips?voice=')[1]['total'], 0)
        self.assertEqual(self.call('api/clips?q=WORLD')[1]['total'], 1)
        self.assertEqual(self.call('api/clips?engine=tts.kokoro')[1]['total'], 0)
        self.assertNotIn('key', self.call('api/clips')[1]['clips'][0])
        self.assertEqual(self.call('api/clips/%d/pin' % cid, {'pinned': False})[1]['clip']['pinned'], False)
        st, audio = self.call('api/clips/%d/audio' % cid, raw=True)
        self.assertEqual((st, audio[:3]), (200, b'ID3'))
        self.assertTrue(self.call('api/clips/%d/delete' % cid, {})[1]['ok'])
        self.assertEqual(self.call('api/clips/%d/audio' % cid, raw=True)[0], 404)

    def raw(self, path, headers=None, method='GET'):
        rq = urllib.request.Request(self.base + path, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(rq) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_audio_streams_through_the_ingress_with_ranges(self):
        data = b'ID3' + bytes(range(97))
        row = self.c.put('tts.piper', 'v', 'range me', data)
        path = 'api/clips/%d/audio' % row['id']
        st, h, b = self.raw(path)
        self.assertEqual((st, b, h['Content-Type'], h['Accept-Ranges']), (200, data, 'audio/mpeg', 'bytes'))
        st, h, b = self.raw(path, {'Range': 'bytes=0-1'})   # what Safari and iOS ask first
        self.assertEqual((st, b, h['Content-Range']), (206, data[:2], 'bytes 0-1/100'))
        st, h, b = self.raw(path, {'Range': 'bytes=10-'})
        self.assertEqual((st, b, h['Content-Range']), (206, data[10:], 'bytes 10-99/100'))
        st, h, b = self.raw(path, {'Range': 'bytes=-5'})
        self.assertEqual((st, b), (206, data[-5:]))
        st, h, b = self.raw(path, {'Range': 'bytes=50-9999'})
        self.assertEqual((st, b, h['Content-Range']), (206, data[50:], 'bytes 50-99/100'))
        st, h, _ = self.raw(path, {'Range': 'bytes=100-'})
        self.assertEqual((st, h['Content-Range']), (416, 'bytes */100'))
        self.assertEqual(self.raw(path, {'Range': 'items=1-2'})[2], data)   # unusable Range: whole file
        self.assertEqual(self.raw(path, {'Range': 'bytes=0-1,5-6'})[2], data)

    def test_audio_content_type_follows_the_sniffed_format(self):
        for i, (blob, ct) in enumerate([(b'RIFF....WAVE', 'audio/wav'), (b'OggS....', 'audio/ogg'), (b'fLaC....', 'audio/flac'),
                                        (b'\x00\x00\x00\x18ftypM4A ', 'audio/mp4'), (b'\xff\xf1....', 'audio/aac'), (b'ID3....', 'audio/mpeg'), (b'\xff\xfb....', 'audio/mpeg')]):
            row = self.c.put('tts.piper', 'v', 'clip %d' % i, blob + b'x' * 20)
            self.assertEqual(self.raw('api/clips/%d/audio' % row['id'])[1]['Content-Type'], ct)

    def test_page_player_uses_a_relative_ingress_url_not_the_signed_link(self):
        js = open(os.path.join(os.path.dirname(ui_server.__file__), 'ui', 'ui.js')).read()
        self.assertIn("'api/clips/' + c.id + '/audio'", js)
        self.assertNotIn('/clip/', js)
        self.assertNotIn('sig=', js)
        self.assertNotIn("'/api/", js)   # no absolute path: it would escape the ingress prefix

    def test_pinned_filter_and_use_counts_in_the_list(self):
        a = self.call('api/generate', {'text': 'Keep me', 'pin': True})[1]['clip']['id']
        self.call('api/generate', {'text': 'Not me'})
        self.assertEqual([c['text'] for c in self.call('api/clips?pinned=1')[1]['clips']], ['Keep me'])
        self.assertEqual([c['text'] for c in self.call('api/clips?pinned=0')[1]['clips']], ['Not me'])
        self.assertEqual(self.call('api/clips?pinned=')[1]['total'], 2)
        self.s.speak(req(text='Keep me', voice=''))   # a hit
        c = [x for x in self.call('api/clips')[1]['clips'] if x['id'] == a][0]
        self.assertEqual((c['hits'], c['uses_30d'], c['auto_pinned']), (0, 0, False))   # dry mode (the page build is dry): not a use

    def test_stats_progress_after_generate_pin_delete(self):
        st = self.call('api/clips')[1]['stats']
        self.assertEqual((st['bytes'], st['percent']), (0, 0.0))
        cid = self.call('api/generate', {'text': 'Hello'})[1]['clip']['id']
        st = self.call('api/clips')[1]['stats']
        self.assertEqual(st['bytes'], 100)
        self.assertEqual(st['percent'], round(100 * 100 / (1024 * 1024), 1))
        self.assertEqual(self.call('api/info')[1]['stats']['bytes'], 100)
        self.call('api/clips/%d/pin' % cid, {'pinned': True})
        self.assertEqual(self.call('api/clips')[1]['stats']['pinned'], 1)
        self.call('api/clips/%d/pin' % cid, {'pinned': False})
        self.assertEqual(self.call('api/clips/%d/pin' % cid, {'pinned': False})[1]['clip']['auto_pinned'], False)
        self.call('api/clips/%d/delete' % cid, {})
        self.assertEqual(self.call('api/clips')[1]['stats']['bytes'], 0)

    def test_status_api_carries_cache_percent(self):
        s, *_ = build(self.tmp.name + '-x')
        s.cache.put('tts.piper', 'v', 'a', b'ID3' + b'a' * 97)
        srv = appmain.ThreadingHTTPServer(('127.0.0.1', 0), appmain.make_handler(s))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        rq = urllib.request.Request('http://127.0.0.1:%d/status' % srv.server_address[1], headers={'Authorization': 'Bearer tok-1234'})
        with urllib.request.urlopen(rq) as r:
            cache = json.loads(r.read())['cache']
        self.assertEqual((cache['bytes'], cache['max_bytes'], cache['auto_pin_uses'], cache['auto_pin_days']), (100, 1048576, 5, 30))
        self.assertIn('percent', cache)

    def test_user_unpin_through_the_page_sticks(self):
        cid = self.call('api/generate', {'text': 'Often'})[1]['clip']['id']
        self.c.auto_pin_uses = 2
        self.c.get('tts.piper', '', 'Often')
        self.c.get('tts.piper', '', 'Often')
        self.assertEqual(self.call('api/clips')[1]['clips'][0]['auto_pinned'], True)
        self.call('api/clips/%d/pin' % cid, {'pinned': False})
        self.c.get('tts.piper', '', 'Often')
        self.c.get('tts.piper', '', 'Often')
        c = self.call('api/clips')[1]['clips'][0]
        self.assertEqual((c['pinned'], c['auto_pinned']), (False, False))

    def test_generate_errors(self):
        self.assertEqual(self.call('api/generate', {'text': ''})[0], 400)
        self.assertEqual(self.call('api/generate', {'text': 'x', 'voice': 'nobody'})[0], 400)
        self.assertEqual(self.call('api/generate', {'text': 'x' * 700})[0], 400)

    def test_play_follows_mode_and_uses_signed_link(self):
        cid = self.call('api/generate', {'text': 'Hello'})[1]['clip']['id']
        before = len(self.ha.calls)
        st, r = self.call('api/clips/%d/play' % cid, {'speaker': 'bedroom'})
        self.assertEqual(r['status'], 'spoken')
        play = [e for e in self.ha.events if e[1] == 'play_media']
        self.assertTrue(play[0][2].startswith(BASE + '/clip/%d.' % cid))
        self.assertGreater(len(self.ha.calls), before)
        self.assertEqual(self.ha.states[BED]['attributes']['volume_level'], 0.55)   # volume restored
        self.assertEqual(self.call('api/clips/%d/play' % cid, {'speaker': 'nope'})[1]['status'], 'blocked')

    def test_dry_mode_plays_nothing(self):
        s, ha, _, c, _ = build(tempfile.mkdtemp(), mode='dry')
        row = c.put('tts.piper', '', 'hi', b'ID3')
        r = ui_server._play(s, row, 'bedroom')
        self.assertEqual(r['status'], 'dry')
        self.assertEqual(ha.calls, [])

    def test_generate_and_play(self):
        st, d = self.call('api/generate-play', {'text': 'Hello again', 'speaker': 'bedroom'})
        self.assertEqual((st, d['play']['status']), (200, 'spoken'))

    def test_play_without_base_url_is_an_error_not_a_crash(self):
        self.s.cfg['base_url'] = ''
        cid = self.call('api/generate', {'text': 'Hello'})[1]['clip']['id']
        self.assertEqual(self.call('api/clips/%d/play' % cid, {'speaker': 'bedroom'})[1]['status'], 'error')


class UsageAutoPin(unittest.TestCase):
    """Use log, auto-pin after N uses in D days (fake clock), a user unpin sticks, dry runs and the page are not uses."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)

    def mk(self, **k):
        d = dict(max_bytes=10**6, days=3650, clock=Clock(), auto_pin_uses=3, auto_pin_days=10)
        d.update(k)
        c = cachelib.Cache(os.path.join(self.tmp.name, 'c'), os.path.join(self.tmp.name, 'ih.db'), **d)
        _OPEN.append(c)
        return c

    def use(self, c, n=1, text='hi'):
        for _ in range(n):
            c.get('tts.piper', 'v', text)

    def test_each_hit_is_a_use_with_a_timestamp(self):
        c = self.mk(auto_pin_uses=0)
        c.put('tts.piper', 'v', 'hi', b'ID3a')
        self.use(c, 2)
        c.clock.t += 5 * cachelib.DAY
        self.use(c)
        row = c.by_id(1)
        self.assertEqual((row['hits'], row['uses_30d']), (3, 3))
        self.assertEqual(c.uses_since(1, 2), 1)
        self.assertEqual(c.uses_since(1, 6), 3)

    def test_miss_is_not_a_use(self):
        c = self.mk()
        self.assertIsNone(c.get('tts.piper', 'v', 'nothing'))
        self.assertEqual(c.db.query('SELECT COUNT(*) FROM uses')[0][0], 0)

    def test_auto_pin_at_threshold_and_badge_data(self):
        c = self.mk()
        c.put('tts.piper', 'v', 'hi', b'ID3a')
        self.use(c, 2)
        self.assertFalse(c.by_id(1)['pinned'])
        self.use(c)
        row = c.by_id(1)
        self.assertEqual((row['pinned'], row['auto_pinned']), (True, True))
        self.assertEqual(ui_server.public(row)['auto_pinned'], True)
        self.assertEqual(c.stats()['auto_pinned'], 1)

    def test_uses_outside_the_window_do_not_count(self):
        c = self.mk()
        c.put('tts.piper', 'v', 'hi', b'ID3a')
        self.use(c, 2)
        c.clock.t += 11 * cachelib.DAY
        self.use(c, 2)
        self.assertFalse(c.by_id(1)['pinned'])
        self.use(c)
        self.assertTrue(c.by_id(1)['pinned'])

    def test_user_unpin_sticks(self):
        c = self.mk()
        c.put('tts.piper', 'v', 'hi', b'ID3a')
        self.use(c, 3)
        self.assertTrue(c.by_id(1)['auto_pinned'])
        c.set_pinned(1, False)
        row = c.by_id(1)
        self.assertEqual((row['pinned'], row['auto_pinned'], row['user_unpinned']), (False, False, True))
        self.use(c, 10)
        self.assertFalse(c.by_id(1)['pinned'])
        c.set_pinned(1, True)   # a person pinning again clears the flag and the 'auto' mark
        row = c.by_id(1)
        self.assertEqual((row['pinned'], row['auto_pinned'], row['user_unpinned']), (True, False, False))

    def test_manual_pin_is_not_marked_auto(self):
        c = self.mk()
        c.put('tts.piper', 'v', 'hi', b'ID3a', pinned=True)
        self.use(c, 5)
        self.assertEqual((c.by_id(1)['pinned'], c.by_id(1)['auto_pinned']), (True, False))

    def test_auto_pinned_survives_eviction_and_expiry(self):
        c = self.mk(max_bytes=300, days=5)
        c.put('tts.piper', 'v', 'hi', b'ID3' + b'a' * 97)
        self.use(c, 3)
        c.clock.t += 6 * cachelib.DAY
        c.put('tts.piper', 'v', 'b', b'ID3' + b'b' * 97)
        c.put('tts.piper', 'v', 'c', b'ID3' + b'c' * 97)
        c.put('tts.piper', 'v', 'd', b'ID3' + b'd' * 97)
        self.assertIsNotNone(c.find('tts.piper', 'v', 'hi'))

    def test_off_when_uses_is_zero(self):
        c = self.mk(auto_pin_uses=0)
        c.put('tts.piper', 'v', 'hi', b'ID3a')
        self.use(c, 20)
        self.assertFalse(c.by_id(1)['pinned'])

    def test_delete_drops_the_use_log(self):
        c = self.mk()
        c.put('tts.piper', 'v', 'hi', b'ID3a')
        self.use(c)
        c.delete(1)
        self.assertEqual(c.db.query('SELECT COUNT(*) FROM uses')[0][0], 0)

    def test_list_pinned_filter_and_counts(self):
        c = self.mk()
        c.put('tts.piper', 'v', 'a', b'ID3a', pinned=True)
        c.put('tts.piper', 'v', 'b', b'ID3b')
        self.use(c, 2, 'b')
        self.assertEqual([r['text'] for r in c.list(pinned=True)], ['a'])
        self.assertEqual([(r['text'], r['uses_30d']) for r in c.list(pinned=False)], [('b', 2)])
        self.assertEqual(len(c.list()), 2)

    def test_stats_percent(self):
        c = self.mk(max_bytes=1000)
        c.put('tts.piper', 'v', 'a', b'ID3' + b'a' * 247)
        st = c.stats()
        self.assertEqual((st['bytes'], st['max_bytes'], st['percent']), (250, 1000, 25.0))

    def test_migration_from_version_1_keeps_clips(self):
        import ihlib.db as ihdb
        path = os.path.join(self.tmp.name, 'old.db')
        d = ihdb.open_db(path, cachelib.MIGRATIONS[:1])
        d.execute('INSERT INTO clips(engine, voice, key, text, ext, size, created, last_used, hits, pinned) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  ('tts.piper', 'v', cachelib.text_key('old'), 'old', 'mp3', 4, 1.0, 1.0, 7, 1))
        d.close()
        c = cachelib.Cache(os.path.join(self.tmp.name, 'c2'), path)
        _OPEN.append(c)
        row = c.by_id(1)
        self.assertEqual((row['hits'], row['pinned'], row['auto_pinned'], row['user_unpinned']), (7, True, False, False))


class UsageThroughSpeaker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)

    def test_options_parse(self):
        c = sp.parse_options({})
        self.assertEqual((c['auto_pin_uses'], c['auto_pin_days']), (5, 30))
        c = sp.parse_options({'auto_pin_uses': 2, 'auto_pin_days': 7})
        self.assertEqual((c['auto_pin_uses'], c['auto_pin_days']), (2, 7))
        self.assertEqual(sp.parse_options({'auto_pin_uses': 'x'})['auto_pin_uses'], 5)

    def test_real_use_counts_and_auto_pins(self):
        s, ha, tts, c, clock = build(self.tmp.name, mode='live')
        c.auto_pin_uses, c.auto_pin_days = 3, 30
        for _ in range(4):   # the first is a miss (rendered and stored: not a use), then 3 hits
            self.assertEqual(s.speak(req(text='Dinner is ready'))['status'], 'spoken')
        row = c.find('tts.piper', 'voice-h', 'Dinner is ready')
        self.assertEqual((row['hits'], row['pinned'], row['auto_pinned']), (3, True, True))

    def test_dry_runs_do_not_count(self):
        s, ha, tts, c, clock = build(self.tmp.name, mode='live', discord_token='t', discord_users=['alex=123456789012345678'],
                                     discord_permissions=['hearth=alex'])
        c.put('tts.piper', 'voice-h', 'Hello there', b'ID3x')
        s._encode = lambda data: (b'ogg', 1.0, 'AAAA')
        for _ in range(8):
            self.assertEqual(s.speak(req(dry_run=True))['status'], 'dry')
            self.assertEqual(s.speak(req(to='discord', discord_user='alex', dry_run=True))['status'], 'dry')
        row = c.find('tts.piper', 'voice-h', 'Hello there')
        self.assertEqual((row['hits'], row['pinned']), (0, False))
        self.assertEqual(c.db.query('SELECT COUNT(*) FROM uses')[0][0], 0)

    def test_admin_generate_and_seeding_are_not_uses(self):
        s, ha, tts, c, clock = build(self.tmp.name)
        for _ in range(3):
            s.generate('Hello', '')
        s.seed('')
        s.seed('')
        self.assertEqual(c.db.query('SELECT COUNT(*) FROM uses')[0][0], 0)

    def test_filler_is_a_use(self):
        s, ha, tts, c, clock = build(self.tmp.name, mode='live')
        s.seed('voice-h')
        row = c.by_label('tts.piper', 'voice-h', 'one moment')
        s.speak(req(filler='one moment'))
        self.assertEqual(c.by_id(row['id'])['hits'], 1)


class Options(unittest.TestCase):
    def test_defaults_and_clamps(self):
        c = sp.parse_options({})
        self.assertEqual((c['cache_mb'], c['cache_days'], c['base_url'], c['seed_fillers']), (1024, 30, '', True))
        c = sp.parse_options({'cache_mb': 0, 'cache_days': 'x', 'base_url': 'http://h:8831/', 'seed_fillers': False})
        self.assertEqual((c['cache_mb'], c['cache_days'], c['base_url'], c['seed_fillers']), (1, 30, 'http://h:8831', False))


if __name__ == '__main__':
    unittest.main()
