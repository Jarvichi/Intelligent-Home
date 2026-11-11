"""Gateway push (0.15.0): websocket frames, the protocol state machine and the reconnect loop, all against fakes.
Nothing here reaches Discord. Ids and tokens are made-up test values."""
import json, os, socket, struct, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import gateway as gw
import main as mainmod
from main import Assistant

TOKEN, PERSON, OTHER = 'test-token', '42', '77'
MASK = b'\x01\x02\x03\x04'
HB_MS = 40000


def server_text(obj):
    return gw.encode_frame(gw.WS_TEXT, json.dumps(obj).encode())


class FrameTests(unittest.TestCase):
    def roundtrip(self, n, mask):
        payload = bytes(i % 251 for i in range(n))
        self.assertEqual(gw.FrameParser().feed(gw.encode_frame(gw.WS_TEXT, payload, mask)), [(gw.WS_TEXT, payload)])

    def test_roundtrip_all_length_forms_masked_and_not(self):
        for n in (0, 5, 125, 126, 65535, 65536):
            self.roundtrip(n, None)
            self.roundtrip(n, MASK)

    def test_client_frame_is_masked_on_the_wire(self):
        f = gw.encode_frame(gw.WS_TEXT, b'hello', MASK)
        self.assertTrue(f[1] & gw.WS_MASKED)
        self.assertNotIn(b'hello', f)

    def test_bytes_arriving_one_at_a_time(self):
        p, out = gw.FrameParser(), []
        for b in gw.encode_frame(gw.WS_TEXT, b'x' * 300) + gw.encode_frame(gw.WS_PING, b'p'):
            out += p.feed(bytes([b]))
        self.assertEqual(out, [(gw.WS_TEXT, b'x' * 300), (gw.WS_PING, b'p')])

    def test_fragments_are_joined_and_a_ping_may_sit_between(self):
        first = bytes([gw.WS_TEXT, 2]) + b'ab'                 # no FIN bit
        last = bytes([gw.WS_FIN | gw.WS_CONT, 2]) + b'cd'
        out = gw.FrameParser().feed(first + gw.encode_frame(gw.WS_PING) + last)
        self.assertEqual(out, [(gw.WS_PING, b''), (gw.WS_TEXT, b'abcd')])

    def test_protocol_errors(self):
        with self.assertRaises(gw.WsError):
            gw.FrameParser().feed(bytes([gw.WS_FIN | gw.WS_CONT, 0]))          # continuation with no start
        with self.assertRaises(gw.WsError):
            gw.FrameParser().feed(bytes([gw.WS_FIN | 0x40 | gw.WS_TEXT, 0]))   # reserved bit (compression)
        with self.assertRaises(gw.WsError):
            gw.FrameParser(max_message=10).feed(gw.encode_frame(gw.WS_TEXT, b'x' * 11))
        with self.assertRaises(gw.WsError):
            gw.FrameParser().feed(bytes([gw.WS_PING, 0]))                      # control frame without FIN
        with self.assertRaises(gw.WsError):
            gw.FrameParser().feed(bytes([gw.WS_TEXT, 1]) + b'a' + gw.encode_frame(gw.WS_TEXT, b'b'))

    def test_accept_value_matches_the_rfc_example(self):
        self.assertEqual(gw.accept_value('dGhlIHNhbXBsZSBub25jZQ=='), 's3pPLMBiTxaQ9kYGzzhZRbK+xOo=')

    def test_split_url(self):
        self.assertEqual(gw.split_url(gw.GATEWAY_URL), (gw.GATEWAY_HOST, gw.GATEWAY_PORT, '/?v=10&encoding=json'))
        with self.assertRaises(gw.WsError):
            gw.split_url('ws://plain.example/')


class FakeSocket:
    """recv() hands out the queued items in order: bytes, or an exception instance to raise."""
    def __init__(self, items=()):
        self.items, self.sent, self.closed, self.timeouts = list(items), b'', False, []
    def settimeout(self, t):
        self.timeouts.append(t)
    def sendall(self, data):
        self.sent += data
    def recv(self, n):
        if not self.items:
            raise socket.timeout()
        x = self.items.pop(0)
        if isinstance(x, Exception):
            raise x
        return x
    def close(self):
        self.closed = True


def zero_rand(n):
    return b'\x00' * n


def ok_head(extra=b''):
    nonce = __import__('base64').b64encode(zero_rand(gw.WS_NONCE_BYTES)).decode()
    return ('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: %s\r\n\r\n' % gw.accept_value(nonce)).encode() + extra


def open_conn(items):
    sock = FakeSocket(items)
    seen = {}
    def factory(host, port):
        seen['addr'] = (host, port)
        return sock
    return gw.WsConn(gw.GATEWAY_URL, factory, zero_rand), sock, seen


class WsConnTests(unittest.TestCase):
    def test_handshake_then_text_and_quiet_read(self):
        conn, sock, seen = open_conn([ok_head(), server_text({'op': 11}), socket.timeout()])
        self.assertEqual(seen['addr'], (gw.GATEWAY_HOST, gw.GATEWAY_PORT))
        self.assertTrue(sock.sent.startswith(b'GET /?v=10&encoding=json HTTP/1.1\r\nHost: gateway.discord.gg\r\n'))
        self.assertEqual([json.loads(x) for x in conn.recv(1.0)], [{'op': 11}])
        self.assertEqual(conn.recv(1.0), [])
        self.assertEqual(sock.timeouts[-1], 1.0)

    def test_frame_sent_together_with_the_handshake_is_not_lost(self):
        conn, sock, _ = open_conn([ok_head(server_text({'op': 10}))])
        self.assertEqual([json.loads(x) for x in conn.recv(1.0)], [{'op': 10}])

    def test_bad_handshakes_close_the_socket(self):
        for head in (b'HTTP/1.1 403 Forbidden\r\n\r\n', b'HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: wrong\r\n\r\n'):
            sock = FakeSocket([head])
            with self.assertRaises(gw.WsError):
                gw.WsConn(gw.GATEWAY_URL, lambda h, p: sock, zero_rand)
            self.assertTrue(sock.closed)
        sock = FakeSocket([b''])
        with self.assertRaises(gw.WsClosed):
            gw.WsConn(gw.GATEWAY_URL, lambda h, p: sock, zero_rand)

    def test_ping_is_answered_with_a_masked_pong(self):
        conn, sock, _ = open_conn([ok_head(), gw.encode_frame(gw.WS_PING, b'abc')])
        sock.sent = b''
        self.assertEqual(conn.recv(1.0), [])
        self.assertEqual(gw.FrameParser().feed(sock.sent), [(gw.WS_PONG, b'abc')])
        self.assertTrue(sock.sent[1] & gw.WS_MASKED)

    def test_send_text_is_masked_and_parses_back(self):
        conn, sock, _ = open_conn([ok_head()])
        sock.sent = b''
        conn.send_text('{"op":1}')
        self.assertEqual(gw.FrameParser().feed(sock.sent), [(gw.WS_TEXT, b'{"op":1}')])

    def test_server_close_gives_the_code_and_shuts_the_socket(self):
        conn, sock, _ = open_conn([ok_head(), gw.encode_frame(gw.WS_CLOSE, struct.pack('!H', 4004) + b'why')])
        with self.assertRaises(gw.WsClosed) as c:
            conn.recv(1.0)
        self.assertEqual(c.exception.code, 4004)
        self.assertTrue(sock.closed)

    def test_dropped_connection(self):
        conn, sock, _ = open_conn([ok_head(), b''])
        with self.assertRaises(gw.WsClosed) as c:
            conn.recv(1.0)
        self.assertIsNone(c.exception.code)

    def test_close_sends_the_code_once(self):
        conn, sock, _ = open_conn([ok_head()])
        sock.sent = b''
        conn.close(gw.CLOSE_KEEP_SESSION)
        conn.close(gw.CLOSE_KEEP_SESSION)
        self.assertEqual(gw.FrameParser().feed(sock.sent), [(gw.WS_CLOSE, struct.pack('!H', gw.CLOSE_KEEP_SESSION))])
        self.assertTrue(sock.closed)


HELLO = {'op': gw.OP_HELLO, 'd': {'heartbeat_interval': HB_MS}}
READY = {'op': 0, 's': 1, 't': 'READY', 'd': {'session_id': 'sess', 'resume_gateway_url': 'wss://gateway-test.discord.gg'}}


def dm_message(author, seq=2, guild=None):
    d = {'author': {'id': author}, 'content': 'private words', 'channel_id': '9'}
    if guild:
        d['guild_id'] = guild
    return {'op': 0, 's': seq, 't': 'MESSAGE_CREATE', 'd': d}


def new_session():
    return gw.Session(TOKEN, PERSON, rng=lambda: 0.5)


class SessionTests(unittest.TestCase):
    def test_hello_identifies_with_dm_intents_only(self):
        s = new_session(); s.on_open()
        (kind, p), = s.on_payload(HELLO, 100.0)
        self.assertEqual((kind, p['op']), (gw.A_SEND, gw.OP_IDENTIFY))
        self.assertEqual(p['d']['intents'], (1 << 12) | (1 << 13))
        self.assertEqual(p['d']['token'], TOKEN)
        self.assertEqual(s.next_hb, 100.0 + 20.0)       # first beat after rng * interval

    def test_ready_then_hello_resumes_on_the_resume_address(self):
        s = new_session(); s.on_open()
        s.on_payload(HELLO, 0.0)
        self.assertEqual(s.url(), gw.GATEWAY_URL)
        self.assertEqual(s.on_payload(READY, 0.0), [(gw.A_READY,)])
        self.assertTrue(s.ready)
        self.assertEqual(s.url(), 'wss://gateway-test.discord.gg/?v=10&encoding=json')
        s.on_open()
        self.assertFalse(s.ready)
        (kind, p), = s.on_payload(HELLO, 50.0)
        self.assertEqual((p['op'], p['d']['session_id'], p['d']['seq']), (gw.OP_RESUME, 'sess', 1))
        self.assertEqual(s.on_payload({'op': 0, 's': 2, 't': 'RESUMED', 'd': {}}, 51.0), [(gw.A_READY,)])

    def test_resume_address_outside_discord_is_ignored(self):
        s = new_session()
        s.session_id, s.seq, s.resume_url = 'sess', 1, 'wss://elsewhere.example'
        self.assertEqual(s.url(), gw.GATEWAY_URL)

    def test_heartbeat_carries_seq_and_a_missing_ack_reconnects(self):
        s = new_session(); s.on_open()
        s.on_payload(HELLO, 0.0); s.on_payload(READY, 0.0)
        self.assertEqual(s.on_tick(19.9), [])
        self.assertEqual(s.on_tick(20.0), [(gw.A_SEND, {'op': gw.OP_HEARTBEAT, 'd': 1})])
        self.assertEqual(s.on_tick(30.0), [])
        s.on_payload({'op': gw.OP_HEARTBEAT_ACK}, 30.0)
        self.assertEqual(s.on_tick(60.0), [(gw.A_SEND, {'op': gw.OP_HEARTBEAT, 'd': 1})])
        self.assertEqual(s.on_tick(100.0), [(gw.A_RECONNECT, 0.0)])   # no ack since the last beat: zombie
        self.assertTrue(s.can_resume())

    def test_server_asks_for_a_heartbeat(self):
        s = new_session(); s.on_open(); s.on_payload(HELLO, 0.0)
        self.assertEqual(s.on_payload({'op': gw.OP_HEARTBEAT}, 1.0), [(gw.A_SEND, {'op': gw.OP_HEARTBEAT, 'd': None})])

    def test_reconnect_and_invalid_session(self):
        s = new_session(); s.on_open(); s.on_payload(HELLO, 0.0); s.on_payload(READY, 0.0)
        self.assertEqual(s.on_payload({'op': gw.OP_RECONNECT}, 1.0), [(gw.A_RECONNECT, 0.0)])
        self.assertEqual(s.on_payload({'op': gw.OP_INVALID_SESSION, 'd': True}, 1.0), [(gw.A_RECONNECT, gw.INVALID_SESSION_WAIT_S)])
        self.assertTrue(s.can_resume())
        s.on_payload({'op': gw.OP_INVALID_SESSION, 'd': False}, 1.0)
        self.assertFalse(s.can_resume())

    def test_bad_hello_reconnects(self):
        for d in ({}, {'heartbeat_interval': 'soon'}, {'heartbeat_interval': 0}):
            s = new_session(); s.on_open()
            self.assertEqual(s.on_payload({'op': gw.OP_HELLO, 'd': d}, 0.0), [(gw.A_RECONNECT, 0.0)])

    def test_only_the_person_in_a_private_chat_wakes(self):
        s = new_session()
        self.assertEqual(s.on_payload(dm_message(PERSON), 0.0), [(gw.A_WAKE, gw.WAKE_MESSAGE)])
        self.assertEqual(s.seq, 2)
        self.assertEqual(s.on_payload(dm_message(OTHER, 3), 0.0), [])           # the bot's own message
        self.assertEqual(s.on_payload(dm_message(PERSON, 4, guild='5'), 0.0), [])
        self.assertEqual(s.seq, 4)
        for t in gw.REACTION_EVENTS:
            self.assertEqual(s.on_payload({'op': 0, 's': 5, 't': t, 'd': {'user_id': PERSON}}, 0.0), [(gw.A_WAKE, gw.WAKE_REACTION)])
            self.assertEqual(s.on_payload({'op': 0, 's': 6, 't': t, 'd': {'user_id': OTHER}}, 0.0), [])
        self.assertEqual(s.on_payload({'op': 0, 's': 7, 't': 'TYPING_START', 'd': {'user_id': PERSON}}, 0.0), [])
        self.assertEqual(s.on_payload('not an object', 0.0), [])

    def test_session_keeps_no_message_content(self):
        s = new_session()
        s.on_payload(dm_message(PERSON), 0.0)
        self.assertNotIn('private words', repr(vars(s)))

    def test_close_codes(self):
        for code in gw.CLOSE_FATAL:
            s = new_session(); s.session_id, s.seq = 'sess', 1
            self.assertFalse(s.on_close(code))
        for code in gw.CLOSE_NEW_SESSION:
            s = new_session(); s.session_id, s.seq = 'sess', 1
            self.assertTrue(s.on_close(code))
            self.assertFalse(s.can_resume())
        for code in (None, 1006, gw.CLOSE_KEEP_SESSION, 4008):
            s = new_session(); s.session_id, s.seq = 'sess', 1
            self.assertTrue(s.on_close(code))
            self.assertTrue(s.can_resume())


class BackoffTests(unittest.TestCase):
    def test_doubles_to_the_cap_and_resets(self):
        b = gw.Backoff(base=1.0, cap=8.0, rng=lambda: 1.0)
        self.assertEqual([b.next() for _ in range(6)], [1.0, 2.0, 4.0, 8.0, 8.0, 8.0])
        b.reset()
        self.assertEqual(b.next(), 1.0)

    def test_jitter_only_shortens(self):
        b = gw.Backoff(base=4.0, cap=8.0, rng=lambda: 0.0)
        self.assertEqual(b.next(), 4.0 * gw.BACKOFF_JITTER_MIN)


class FakeConn:
    """script: lists of payload dicts (one list per recv call), or an exception to raise from recv."""
    def __init__(self, script):
        self.script, self.sent, self.closed_with = list(script), [], None
    def recv(self, timeout):
        if not self.script:
            raise gw.WsClosed(None)
        x = self.script.pop(0)
        if isinstance(x, Exception):
            raise x
        return [json.dumps(p).encode() if isinstance(p, dict) else p for p in x]
    def send_text(self, text):
        self.sent.append(json.loads(text))
    def close(self, code=gw.CLOSE_NORMAL):
        self.closed_with = code


class ClientHarness:
    def __init__(self, scripts, stop_after_waits=None):
        self.conns, self.urls, self.wakes, self.logs, self.waits, self.now = [], [], [], [], [], [0.0]
        self.scripts, self.stop_after = list(scripts), stop_after_waits
        self.client = gw.GatewayClient('pp', TOKEN, PERSON, self.wakes.append, self.logs.append, connect=self.connect,
                                       clock=lambda: self.now[0], rng=lambda: 1.0, wait=self.wait)
    def connect(self, url):
        self.urls.append(url)
        if not self.scripts:
            self.client.stop()
            raise OSError('no more scripts')
        x = self.scripts.pop(0)
        if isinstance(x, Exception):
            raise x
        self.conns.append(FakeConn(x))
        return self.conns[-1]
    def wait(self, s):
        self.waits.append(s)


class ClientTests(unittest.TestCase):
    def test_identify_wake_drop_then_resume(self):
        h = ClientHarness([[[HELLO], [READY], [dm_message(PERSON)], gw.WsClosed(None)],
                           [[HELLO], [{'op': 0, 's': 3, 't': 'RESUMED', 'd': {}}], gw.WsClosed(gw.CLOSE_FATAL[0])]])
        h.client.run()
        self.assertEqual([c.sent[0]['op'] for c in h.conns], [gw.OP_IDENTIFY, gw.OP_RESUME])
        self.assertEqual(h.urls, [gw.GATEWAY_URL, 'wss://gateway-test.discord.gg/?v=10&encoding=json'])
        # ready -> catch-up wake (message + reaction), then the real message, then the catch-up after the resume
        self.assertEqual(h.wakes, [gw.WAKE_MESSAGE, gw.WAKE_REACTION, gw.WAKE_MESSAGE, gw.WAKE_MESSAGE, gw.WAKE_REACTION])
        self.assertEqual(h.waits, [gw.BACKOFF_BASE_S])
        self.assertEqual(h.client.state, gw.STATE_FAILED)        # 4004-class close: stop for good
        self.assertFalse(h.client.healthy())
        self.assertEqual(h.client.connects, 2)

    def test_healthy_only_between_ready_and_close(self):
        seen = []
        h = ClientHarness([[[HELLO], [READY], [dm_message(PERSON)], gw.WsClosed(gw.CLOSE_FATAL[0])]])
        h.client.on_wake = lambda kind: seen.append(h.client.healthy())
        self.assertFalse(h.client.healthy())
        h.client.run()
        self.assertEqual(seen, [True, True, True])
        self.assertFalse(h.client.healthy())

    def test_connect_failures_back_off_and_a_good_connection_resets(self):
        h = ClientHarness([OSError('down'), OSError('down'), OSError('down'), [[HELLO], [READY], gw.WsClosed(None)], OSError('down')])
        h.client.run()
        self.assertEqual(h.waits, [1.0, 2.0, 4.0, 1.0, 2.0])
        self.assertEqual(h.client.state, gw.STATE_OFF)

    def test_zombie_connection_is_closed_resumably_and_resumed(self):
        h = ClientHarness([[[HELLO], [READY], [], [], []], [[HELLO], gw.WsClosed(gw.CLOSE_FATAL[0])]])
        ticks = iter([0.0, 0.0, 0.0, 0.0, 50.0, 50.0, 100.0, 100.0])
        h.client.clock = lambda: next(ticks, 100.0)
        h.client.run()
        self.assertEqual([p['op'] for p in h.conns[0].sent], [gw.OP_IDENTIFY, gw.OP_HEARTBEAT])
        self.assertEqual(h.conns[0].closed_with, gw.CLOSE_KEEP_SESSION)
        self.assertEqual(h.conns[1].sent[0]['op'], gw.OP_RESUME)

    def test_invalid_session_waits_then_identifies_again(self):
        h = ClientHarness([[[HELLO], [READY], [{'op': gw.OP_INVALID_SESSION, 'd': False}]], [[HELLO], gw.WsClosed(gw.CLOSE_FATAL[0])]])
        h.client.run()
        self.assertEqual(h.waits, [gw.INVALID_SESSION_WAIT_S])
        self.assertEqual(h.conns[1].sent[0]['op'], gw.OP_IDENTIFY)

    def test_stop_closes_cleanly(self):
        h = ClientHarness([[[HELLO], [READY], []]])
        orig = h.client.on_wake
        h.client.on_wake = lambda kind: (orig(kind), h.client.stop())
        h.client.run()
        self.assertEqual(h.conns[0].closed_with, gw.CLOSE_NORMAL)
        self.assertEqual(h.client.state, gw.STATE_OFF)
        self.assertEqual(h.waits, [])

    def test_bad_json_drops_the_connection_and_retries(self):
        h = ClientHarness([[[b'{not json']]])
        h.client.run()
        self.assertEqual(h.waits, [gw.BACKOFF_BASE_S])
        self.assertTrue(any('ValueError' in l or 'JSONDecodeError' in l for l in h.logs))

    def test_logs_never_hold_the_token_or_content(self):
        h = ClientHarness([[[HELLO], [READY], [dm_message(PERSON)], gw.WsClosed(4000)], OSError(TOKEN)])
        h.client.run()
        text = ' '.join(h.logs)
        self.assertTrue(h.logs)
        for secret in (TOKEN, 'private words', PERSON):
            self.assertNotIn(secret, text)


class FakeGateway:
    def __init__(self, ok):
        self.ok, self.state = ok, gw.STATE_READY if ok else gw.STATE_CONNECTING
    def healthy(self):
        return self.ok


class AssistantPushTests(unittest.TestCase):
    def make(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.now = [100000.0]; self.polls = []
        opts = {'mode': 'dry', 'api_token': '', 'personas': [{'key': 'pp', 'user_id': PERSON, 'bot_token': TOKEN, 'live': False,
                'max_per_day': 0, 'checkins': [], 'can_tell': [], 'checkin_days': [], 'closed_dates': [], 'checkin_texts': [], 'checkin_send': False}]}
        a = Assistant(opts, self.tmp.name, log=lambda *x: None, clock=lambda: self.now[0])
        a.tick_poll = lambda k: self.polls.append(self.now[0]) or 0
        for name in ('tick_reactions', 'tick_typing', 'tick_dates', 'tick_reminders', 'publish_reply_progress'):
            setattr(a, name, lambda k: 0)
        a.tick_checkins = lambda k: []
        return a

    def test_no_gateway_polls_every_tick_as_before(self):
        a = self.make()
        for _ in range(3):
            a.tick('pp'); self.now[0] += mainmod.POLL_S
        self.assertEqual(len(self.polls), 3)

    def test_unhealthy_gateway_polls_every_tick(self):
        a = self.make(); a.gateways['pp'] = FakeGateway(False)
        for _ in range(3):
            a.tick('pp'); self.now[0] += mainmod.POLL_S
        self.assertEqual(len(self.polls), 3)

    def test_healthy_gateway_polls_on_wake_and_at_the_fallback_interval(self):
        a = self.make(); a.gateways['pp'] = FakeGateway(True)
        a.tick('pp')                                   # first tick: nothing polled yet, so due
        self.assertEqual(len(self.polls), 1)
        self.now[0] += mainmod.POLL_S; a.tick('pp')
        self.assertEqual(len(self.polls), 1)           # quiet: no REST call
        a.push_wake('pp', gw.WAKE_MESSAGE)
        self.assertTrue(a.wait_tick('pp', 0))          # the worker's pause ends at once
        self.assertFalse(a.wait_tick('pp', 0))
        a.tick('pp')
        self.assertEqual(len(self.polls), 2)
        self.now[0] += mainmod.POLL_S; a.tick('pp')    # still inside the burst after the wake
        self.assertEqual(len(self.polls), 3)
        self.now[0] += mainmod.WAKE_BURST_S; a.tick('pp')
        self.assertEqual(len(self.polls), 3)
        self.now[0] += mainmod.POLL_FALLBACK_S; a.tick('pp')
        self.assertEqual(len(self.polls), 4)           # safety net
        a.gateways['pp'].ok = False                    # socket died: straight back to every tick
        self.now[0] += mainmod.POLL_S; a.tick('pp')
        self.assertEqual(len(self.polls), 5)

    def test_reaction_interval(self):
        a = self.make()
        self.assertEqual(a.react_every('pp'), mainmod.REACT_EVERY_S)
        a.gateways['pp'] = FakeGateway(True)
        self.assertEqual(a.react_every('pp'), mainmod.POLL_FALLBACK_S)
        a.push_wake('pp', gw.WAKE_REACTION)
        self.assertEqual(a.react_every('pp'), 0)
        self.assertEqual(a.react_every('pp'), mainmod.POLL_FALLBACK_S)

    def test_start_gateways_respects_the_option_and_status_shows_state(self):
        a = self.make()
        a.opts['discord_push'] = False
        self.assertEqual(a.start_gateways(), 0)
        self.assertEqual(a.status()['personas']['pp']['push'], gw.STATE_OFF)
        a.opts['discord_push'] = True
        def refuse(url):
            raise OSError('no network in tests')
        self.assertEqual(a.start_gateways(connect=refuse), 1)
        a.stop_gateways()
        self.assertFalse(a.gateways['pp'].thread.is_alive())
        self.assertFalse(a.push_ok('pp'))

    def test_option_default_is_on(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json') as f:
            f.write('{}'); f.flush()
            self.assertTrue(mainmod.load_options(f.name)[0]['discord_push'])
            f.seek(0); f.write('{"discord_push": false}'); f.flush()
            self.assertFalse(mainmod.load_options(f.name)[0]['discord_push'])


if __name__ == '__main__':
    unittest.main()
