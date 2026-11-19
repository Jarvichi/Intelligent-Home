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


class Options(unittest.TestCase):
    def test_defaults_and_clamps(self):
        c = sp.parse_options({})
        self.assertEqual((c['cache_mb'], c['cache_days'], c['base_url'], c['seed_fillers']), (1024, 30, '', True))
        c = sp.parse_options({'cache_mb': 0, 'cache_days': 'x', 'base_url': 'http://h:8831/', 'seed_fillers': False})
        self.assertEqual((c['cache_mb'], c['cache_days'], c['base_url'], c['seed_fillers']), (1, 30, 'http://h:8831', False))


if __name__ == '__main__':
    unittest.main()
