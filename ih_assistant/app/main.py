"""Intelligent Home - Assistant - Personas (ih_assistant): transport, schedule and profile for several personas (stdlib only).
The reply text is written elsewhere (the host); this add-on moves messages in and out, queues check-in events and keeps one
SEPARATE datastore per persona. Dry-run by default."""
import base64, binascii, datetime, hmac, json, logging, os, re, secrets, signal, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.error, urllib.parse, urllib.request
from urllib.parse import urlparse, parse_qs
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store as storemod
from store import PersonaStore, StoreError, valid_key
from discord_io import Discord, DiscordError
from ihlib import log as ihlog
from ihlib import ha as ihha
from ihlib import trace as ihtrace
import planner
import levels
import caldav as caldavmod
import dates as datesmod
import reminders as remmod
import gateway as gatewaymod
import random

VERSION = '0.15.1'
ATTACH_MAX_N = 10                 # files per message handed over (as the old dm_watch / host braininbox)
ATTACH_MAX_BYTES = 25 * 1024 * 1024
ATTACH_HOSTS = ('cdn.discordapp.com', 'media.discordapp.net')


def safe_name(v):
    """A Discord file name made safe for a local file: letters, digits, dot, dash, underscore; at most 80 characters."""
    n = re.sub(r'[^A-Za-z0-9._-]+', '_', str(v or 'file')).strip('._') or 'file'
    return n[-80:]


MAX_BODY = 2 * 1024 * 1024   # the identity call carries a base64 avatar (up to 1 MiB)
NAME_CHANGES_PER_HOUR = 2
NAME_WINDOW_S = 3600
AVATAR_MAX = 1024 * 1024
BAD_NAME = ('discord', '@', '#', ':', '```')
TEXT_MAX = 4000
TELL_MAX = 2000
TELL_PER_DAY = 20
EVENT_TYPES = ('game_session', 'game_reset', 'reminder_due')
EVENT_PER_DAY = 30
EVENT_FACTS_MAX, EVENT_FACT_LEN, EVENT_SOURCE_LEN, EVENT_TEXT_LEN = 20, 300, 40, 1000
CHECKIN_WINDOW_MIN = 30
AGREED_MAX = 20
PLAN_RETRY_S = 60   # a plan made while HA was down is rebuilt this often until HA answers
FACT_RE = re.compile(r'^[a-z0-9_.-]{1,64}/[a-z0-9_.-]{1,64}$')
FACT_TIMEOUT_S = 5
GIF_MAX_BYTES = 8 * 1024 * 1024
GIF_CACHE_MAX = 300   # pictures kept for reuse; the oldest go first
GIF_REUSE_CHANCE = 0.5
GIF_STOP = {'the', 'and', 'for', 'with', 'you', 'that', 'this', 'gif', 'your'}
GIF_WORDS_MAX = 60
ENTITY_RE = re.compile(r'^[a-z_]+\.[a-z0-9_]{1,80}$')
POLL_S = 3   # 0.9.8: was 8; the reply starts about this much sooner
POLL_FALLBACK_S = 60   # 0.15.0: Discord is asked this often while the push connection (gateway) is ready; a push asks at once
WAKE_BURST_S = 10      # 0.15.0: after a push, ask every POLL_S for this long (covers a second message and a late REST answer)
TYPING_MAX_S = 300
ASK_KEEP_S = 86400
ASK_MAX = 10
WATCH_KEEP_S = 21600   # 0.13.1: plain persona messages are watched for reactions for 6 h
WATCH_MAX = 8
REACT_EVERY_S = 20
ASK_EMOJI = {'yes': '\u2705', 'no': '\u274c', 'unsure': '\u2753'}
PLAN_DEFAULTS = {'checkin_plan': False, 'checkins_per_day': 2, 'wake_start': '09:00', 'wake_end': '21:30', 'min_gap_h': 3.0, 'calendars': [], 'calendar_read': [], 'dates': [], 'dates_leads': '7,3', 'dates_at': '18:00', 'dates_latest': '21:30', 'location_entities': [], 'calendar_write': [], 'busy_windows': [], 'free_windows': [],
                 'meal_plan_entity': '', 'meal_fact': '', 'presence_entity': '', 'quiet_if_messaged_h': 2.0,
                 'checkin_days': [], 'closed_dates': [], 'checkin_texts': [], 'checkin_send': False, 'usage_text': '', 'usage_text_notime': ''}
HHMM = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
MARK_RE = re.compile(r'^\d{1,25}$')
DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}(\.\.\d{4}-\d{2}-\d{2})?$')
USAGE_SENSORS = {'session': 'sensor.claude_usage_session_usage', 'week': 'sensor.claude_usage_week_usage',
                 'session_reset': 'sensor.claude_usage_session_reset_time', 'week_reset': 'sensor.claude_usage_weekly_reset_time'}   # defaults; options usage_sensor_* override
USAGE_OUT_AT = 99.0       # percent: out of usage when the session or the week sensor is at or over this
USAGE_MIN_GAP_S = 600     # one automatic reply per persona per 10 minutes
USAGE_FAR_H = 12          # reset further away than this shows a day name
USAGE_TEXT_MAX = 400
CHECKIN_TEXT_MAX = 600


def hhmm(v, default):
    return v if isinstance(v, str) and HHMM.match(v) else default


def to_min(s):
    return int(s[:2]) * 60 + int(s[3:])


def load_options(path):
    """-> (opts, problems). Bad persona entries are skipped and listed in problems (no values quoted)."""
    problems = []
    try:
        o = json.load(open(path))
    except Exception:
        o = {}
    mode = 'live' if o.get('mode') == 'live' else 'dry'
    personas, seen = [], set()
    for i, p in enumerate(o.get('personas') or []):
        if not isinstance(p, dict) or not valid_key(p.get('key')):
            problems.append('persona #%d: bad key' % (i + 1)); continue
        if p['key'] in seen:
            problems.append('persona %s: duplicate key' % p['key']); continue
        seen.add(p['key'])
        try:
            mpd = int(p.get('max_per_day') if p.get('max_per_day') is not None else 4)
        except (TypeError, ValueError):
            mpd = 4
        def num(v, lo, hi, d):
            try:
                return min(hi, max(lo, float(v))) if v is not None else d
            except (TypeError, ValueError):
                return d
        ents = lambda v: [e for e in (v or []) if isinstance(e, str) and ENTITY_RE.match(e)] if isinstance(v, list) else []
        wins = lambda v: [w.strip().lower() for w in (v or []) if isinstance(w, str) and planner.parse_window(w)] if isinstance(v, list) else []
        ent1 = lambda v: v if isinstance(v, str) and ENTITY_RE.match(v) else ''
        fact1 = lambda v: v if isinstance(v, str) and FACT_RE.match(v) else ''
        days = lambda v: sorted({d.strip().lower()[:3] for d in v if isinstance(d, str) and d.strip().lower()[:3] in DAYS}, key=DAYS.index) if isinstance(v, list) else []
        texts = []
        for t in (p.get('checkin_texts') or []) if isinstance(p.get('checkin_texts'), list) else []:
            if isinstance(t, str) and '|' in t:
                d, _, tx = t.partition('|'); d = d.strip().lower()[:3] if d.strip() != '*' else '*'
                if (d == '*' or d in DAYS) and tx.strip() and len(tx) <= CHECKIN_TEXT_MAX:
                    texts.append('%s|%s' % (d, tx.strip()))
        utext = lambda v: v.strip() if isinstance(v, str) and 0 < len(v.strip()) <= USAGE_TEXT_MAX else ''
        personas.append({
            'sender_label': p.get('sender_label') if isinstance(p.get('sender_label'), str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,47}', p['sender_label']) else 'unknown',
            'checkin_days': days(p.get('checkin_days')), 'closed_dates': [d.strip() for d in (p.get('closed_dates') or []) if isinstance(d, str) and DATE_RE.match(d.strip())] if isinstance(p.get('closed_dates'), list) else [],
            'checkin_texts': texts, 'checkin_send': p.get('checkin_send') is True,
            'usage_text': utext(p.get('usage_text')), 'usage_text_notime': utext(p.get('usage_text_notime')),
            'checkin_plan': p.get('checkin_plan') is True, 'checkins_per_day': planner.perday_option(p.get('checkins_per_day')),
            'busy_windows': wins(p.get('busy_windows')), 'free_windows': wins(p.get('free_windows')),
            'wake_start': hhmm(p.get('wake_start'), '09:00'), 'wake_end': hhmm(p.get('wake_end'), '21:30'),
            'min_gap_h': num(p.get('min_gap_h'), 0, 12, 3.0), 'calendars': ents(p.get('calendars')), 'calendar_read': ents(p.get('calendar_read')), 'dates': [str(x)[:120] for x in (p.get('dates') or []) if isinstance(x, str)][:300] if isinstance(p.get('dates'), list) else [], 'dates_leads': str(p.get('dates_leads') or '7,3')[:20], 'dates_at': hhmm(p.get('dates_at'), '18:00'), 'dates_latest': hhmm(p.get('dates_latest'), '21:30'), 'location_entities': ents(p.get('location_entities')), 'calendar_write': ents(p.get('calendar_write')),
            'meal_plan_entity': ent1(p.get('meal_plan_entity')), 'meal_fact': fact1(p.get('meal_fact')), 'presence_entity': ent1(p.get('presence_entity')),
            'quiet_if_messaged_h': num(p.get('quiet_if_messaged_h'), 0, 24, 2.0),
            'levels': levels.resolve(p['key'], p), 'fixed_rule': (p.get('fixed_rule').strip()[:300] if isinstance(p.get('fixed_rule'), str) else ''),
            'key': p['key'], 'user_id': str(p.get('user_id') or ''), 'bot_token': str(p.get('bot_token') or ''),
            'quiet_start': hhmm(p.get('quiet_start'), '22:00'), 'quiet_end': hhmm(p.get('quiet_end'), '08:00'),
            'max_per_day': max(0, mpd),
            'checkins': sorted({c for c in (p.get('checkins') or []) if isinstance(c, str) and HHMM.match(c)}),
            'can_tell': [k for k in (p.get('can_tell') or []) if valid_key(k) and k != p['key']],
            'live': p.get('live') is True})
    for uk, ak in (('usage_sensor_session', 'session'), ('usage_sensor_week', 'week'), ('usage_sensor_session_reset', 'session_reset'), ('usage_sensor_week_reset', 'week_reset')):
        if isinstance(o.get(uk), str) and ENTITY_RE.match(o[uk].strip()):
            USAGE_SENSORS[ak] = o[uk].strip()
    return {'mode': mode, 'facts_url': str(o.get('facts_url') or '').strip().rstrip('/'), 'facts_key': str(o.get('facts_key') or '').strip(),
            'api_token': str(o.get('api_token') or ''), 'timezone': str(o.get('timezone') or 'UTC'),
            'trace_url': str(o.get('trace_url') or o.get('facts_url') or 'http://172.30.32.1:8790').strip().rstrip('/'),
            'ingest_token': str(o.get('ingest_token') or '').strip(), 'giphy_api_key': str(o.get('giphy_api_key') or '').strip(), 'caldav_user': str(o.get('caldav_user') or '').strip(), 'caldav_password': str(o.get('caldav_password') or '').strip(), 'caldav_url': str(o.get('caldav_url') or 'https://caldav.icloud.com').strip(), 'gif_gap_minutes': o.get('gif_gap_minutes') if isinstance(o.get('gif_gap_minutes'), int) and 0 <= o.get('gif_gap_minutes') <= 1440 else 10,
            'discord_push': o.get('discord_push') is not False,
            'personas': personas}, problems


def in_quiet(now_min, start, end):
    s, e = to_min(start), to_min(end)
    if s == e:
        return False
    return (s <= now_min < e) if s < e else (now_min >= s or now_min < e)


class Assistant:
    def __init__(self, opts, data_dir='/data', log=print, opener=None, sleep=time.sleep, clock=time.time, tz=None, ha=None, facts_opener=None, rng=None, gif_opener=None, caldav_factory=None):
        self.opts, self.log, self.clock, self.sleep = opts, log, clock, sleep
        self.data_dir = data_dir
        self._caldav = None
        self.caldav_factory = caldav_factory
        self.gif_opener = gif_opener   # urlopen-like for GIPHY (tests inject a fake)
        self.facts_opener = facts_opener   # urlopen-like, for the orchestrator facts (tests inject a fake)
        self.ha = ha   # Home Assistant client (calendar_events, state); None = no HA
        self.tz = tz
        self.rng = rng or random.random
        self.progress_publish_at = {}
        self.cfg, self.stores, self.discord = {}, {}, {}
        for p in opts['personas']:
            k = p['key']
            for dk, dv in PLAN_DEFAULTS.items():
                p.setdefault(dk, list(dv) if isinstance(dv, list) else dv)
            p.setdefault('levels', levels.resolve(k, p))
            self.cfg[k] = p
            self.stores[k] = PersonaStore(data_dir, k, clock)
            self.stores[k].ensure_reply_progress()
            if p['bot_token'] and p['user_id']:
                self.discord[k] = Discord(p['bot_token'], opener, sleep)
        self.init_push()

    # ---- push from the Discord Gateway (0.15.0) ----
    def init_push(self):
        self.gateways, self.wake_events = {}, {k: threading.Event() for k in self.cfg}
        self.poll_at, self.burst_until, self.react_now = {}, {}, set()

    def start_gateways(self, connect=None):
        """One Gateway listener per persona bot (reading is not gated by dry-run, exactly like polling). Off with discord_push: false."""
        if not self.opts.get('discord_push', True):
            self.log('gateway: off (discord_push is false); polling every %d s' % POLL_S)
            return 0
        for k in self.discord:
            c = self.cfg[k]
            kw = {'connect': connect} if connect else {}
            self.gateways[k] = gatewaymod.GatewayClient(k, c['bot_token'], c['user_id'], lambda kind, k=k: self.push_wake(k, kind), self.log, **kw)
            self.gateways[k].start()
        return len(self.gateways)

    def stop_gateways(self):
        for g in self.gateways.values():
            g.stop()
        for g in self.gateways.values():
            g.join()

    def push_ok(self, key):
        g = getattr(self, 'gateways', {}).get(key)
        return bool(g and g.healthy())

    def push_wake(self, key, kind):
        """Called from the gateway thread: memory flags only (no store, no Discord call), then wake the persona's worker."""
        self.burst_until[key] = self.clock() + WAKE_BURST_S
        if kind == gatewaymod.WAKE_REACTION:
            self.react_now.add(key)
        self.wake_events[key].set()

    def poll_due(self, key):
        """Ask Discord for new messages now? Always while the push connection is not ready (so a dead socket loses nothing);
        with push ready: right after a push, and every POLL_FALLBACK_S as a safety net."""
        now = self.clock()
        if not self.push_ok(key) or now < self.burst_until.get(key, 0):
            return True
        return now - self.poll_at.get(key, 0) >= POLL_FALLBACK_S

    def react_every(self, key):
        """Seconds between reaction checks: at once after a reaction push, slow while push is ready, else as before."""
        if key in getattr(self, 'react_now', ()):
            self.react_now.discard(key)
            return 0
        return POLL_FALLBACK_S if self.push_ok(key) else REACT_EVERY_S

    def wait_tick(self, key, timeout=POLL_S):
        """The worker's pause between ticks: ends early when a push arrives."""
        ev = self.wake_events[key]
        woken = ev.wait(timeout)
        ev.clear()
        return woken

    # ---- helpers ----
    def now(self):
        return datetime.datetime.fromtimestamp(self.clock(), self.tz) if self.tz else datetime.datetime.fromtimestamp(self.clock(), datetime.timezone.utc)

    def is_live(self, key):
        return self.opts['mode'] == 'live' and self.cfg[key]['live']

    def authed(self, header):
        tok = self.opts['api_token']
        if not tok or not header or not header.startswith('Bearer '):
            return False
        return hmac.compare_digest(header[7:].strip().encode(), tok.encode())

    def _day(self):
        return self.now().strftime('%Y-%m-%d')

    def _channel(self, key):
        """DM channel id of this persona's person (cached in the persona's own state)."""
        st = self.stores[key].state()
        if st.get('dm_channel'):
            return st['dm_channel']
        ch = self.discord[key].open_dm(self.cfg[key]['user_id'])
        self.stores[key].update_state(lambda s: s.__setitem__('dm_channel', ch))
        return ch

    def trace_batch(self, trace_id, spans):
        """Best-effort metadata-only trace upload. Message text and Discord IDs never leave the add-on."""
        if not self.opts.get('trace_url') or not self.opts.get('ingest_token') or not spans:
            return
        req = urllib.request.Request(self.opts['trace_url'] + '/trace',
            data=json.dumps({'trace_id': trace_id, 'spans': spans}).encode(), method='POST',
            headers={'Authorization': 'Bearer ' + self.opts['ingest_token'], 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                if not 200 <= r.status < 300:
                    self.log('trace upload failed (http_error)')
        except Exception as e:
            self.log('trace upload failed (%s)' % type(e).__name__)

    def sender_label(self, key):
        """Configured short label; never infer a person's identity from a persona key."""
        label = self.cfg.get(key, {}).get('sender_label', 'unknown')
        return label if isinstance(label, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,47}', label) else 'unknown'

    def trace_message(self, key, ev):
        """Create a trace at the Discord-to-persona inbox boundary and retain local correlation metadata."""
        tid, root, queued = uuid.uuid4().hex, secrets.token_hex(8), secrets.token_hex(8)
        started = self.clock()
        ev['trace'] = {'id': tid, 'root': root, 'queued': queued, 'started': started}
        text = str(ev.get('text') or '')   # only sizes leave this function, never the text
        attrs = ihtrace.clean_attrs({'persona': str(key), 'sender': self.sender_label(key), 'kind': 'message',
                                   'in_bytes': len(text.encode('utf-8')), 'chars': len(text),
                                   'attachments': int(ev.get('attachments') or 0)})
        self.trace_batch(tid, [
            {'span_id': root, 'parent_span_id': None, 'component': 'discord', 'operation': 'direct_message',
             'status': 'ok', 'reason': '', 'started_at': started, 'duration_ms': 0, 'attrs': attrs},
            {'span_id': queued, 'parent_span_id': root, 'component': 'ih_assistant', 'operation': 'inbox_queue',
             'status': 'ok', 'reason': '', 'started_at': started, 'duration_ms': 0}
        ])

    def _count_out(self, key, kind):
        day = self._day()
        def f(s):
            o = s.setdefault('out', {})
            if o.get('day') != day:
                o.clear(); o['day'] = day
            o[kind] = int(o.get(kind, 0)) + 1
        self.stores[key].update_state(f)

    def out_today(self, key):
        o = self.stores[key].state().get('out', {})
        return (int(o.get('reply', 0)) + int(o.get('checkin', 0))) if o.get('day') == self._day() else 0

    def checkins_today(self, key):
        """0.12.1: check-ins started today. Replies to the person's own messages do NOT count against the check-in allowance
        (a chatty day used to block every check-in)."""
        o = self.stores[key].state().get('out', {})
        return int(o.get('checkin', 0)) if o.get('day') == self._day() else 0

    def tick_dates(self, key):
        """0.13.0: hand the persona a reminder_due event for birthdays and other dates, once per day, between dates_at and dates_latest.
        The persona writes the message itself. -> True if an event was queued."""
        c, st, now = self.cfg[key], self.stores[key], self.now()
        if not c['dates']:
            return False
        nm = now.hour * 60 + now.minute
        if not (to_min(c['dates_at']) <= nm <= to_min(c['dates_latest'])) or in_quiet(nm, c['quiet_start'], c['quiet_end']):
            return False
        day = now.strftime('%Y-%m-%d')
        if st.state().get('dates_done') == day:
            return False
        st.update_state(lambda s: s.__setitem__('dates_done', day))   # mark first: never twice, even if the hand-over below fails
        r = datesmod.due(now.date(), datesmod.parse(c['dates']), datesmod.leads(c['dates_leads']))
        if not r:
            return False
        self.maybe_intro(key)
        st.append_event({'type': 'reminder_due', 'source': 'dates', 'facts': r[1], 'suggested_text': r[0][:900]})
        self.log('persona %s: dates reminder queued (%s)' % (key, r[1]['days_ahead']))
        return True

    # ---- reminders (0.14.0) ----
    def remind_add(self, key, body):
        """Save a reminder: {when: 'YYYY-MM-DD HH:MM' local, text, repeat?}. It is handed to the persona as a reminder_due event when due."""
        if key not in self.stores:
            return 404, {'ok': False, 'why': 'unknown persona'}
        when, text, rep = remmod.parse_local(body.get('when')), body.get('text'), body.get('repeat') or 'none'
        if when is None:
            return 400, {'ok': False, 'why': 'when: YYYY-MM-DD HH:MM (local time)'}
        if not isinstance(text, str) or not text.strip() or len(text) > remmod.MAX_TEXT:
            return 400, {'ok': False, 'why': 'text: 1..%d characters' % remmod.MAX_TEXT}
        if rep not in remmod.REPEATS:
            return 400, {'ok': False, 'why': 'repeat: ' + ', '.join(remmod.REPEATS)}
        now = self.now().replace(tzinfo=None)
        if (when - now).total_seconds() < remmod.MIN_LEAD_S:
            return 400, {'ok': False, 'why': 'that time has passed (or is under %d seconds away)' % remmod.MIN_LEAD_S}
        item = {'id': uuid.uuid4().hex[:8], 'due': when.strftime(remmod.ISO_FMT), 'text': text.strip(), 'repeat': rep,
                'anchor': when.day, 'created': now.strftime(remmod.ISO_FMT), 'status': 'pending'}
        def put(s):
            items = s.get('reminders', [])
            if sum(1 for i in items if i.get('status') == 'pending') >= remmod.MAX_PENDING:
                return False
            items.append(item)
            s['reminders'] = items
            return True
        if not self.stores[key].update_state(put):
            return 429, {'ok': False, 'why': 'at most %d pending reminders' % remmod.MAX_PENDING}
        self.log('persona %s: reminder %s saved (%s, %d chars)' % (key, item['id'], rep, len(item['text'])))
        return 200, {'ok': True, 'id': item['id'], 'due': item['due']}

    def remind_list(self, key):
        items = [i for i in self.stores[key].state().get('reminders', []) if i.get('status') == 'pending']
        return 200, {'ok': True, 'reminders': sorted(items, key=lambda i: i['due'])}

    def remind_cancel(self, key, body):
        rid = body.get('id')
        if not isinstance(rid, str) or not rid:
            return 400, {'ok': False, 'why': 'id required'}
        def drop(s):
            before = s.get('reminders', [])
            s['reminders'] = [i for i in before if i.get('id') != rid]
            return len(before) - len(s['reminders'])
        if not self.stores[key].update_state(drop):
            return 404, {'ok': False, 'why': 'no such reminder'}
        self.log('persona %s: reminder %s cancelled' % (key, rid))
        return 200, {'ok': True}

    def tick_reminders(self, key):
        """Hand the persona a reminder_due event (source 'reminders') for each reminder whose time has come. State is updated
        BEFORE the hand-over so a reminder never fires twice. Overdue up to a day: fires once, flagged late; older: dropped.
        Not counted against EVENT_PER_DAY. -> number of events queued."""
        st, now = self.stores[key], self.now().replace(tzinfo=None)
        clock = float(self.clock())
        out, dropped = [], []
        def mark(s):
            items = remmod.prune(s.get('reminders', []), clock)
            for it in remmod.due_items(items, now):
                late = remmod.overdue_s(it, now)
                if late > remmod.LATE_MAX_S:
                    dropped.append(it['id'])
                else:
                    out.append({'type': 'reminder_due', 'source': 'reminders', 'suggested_text': remmod.suggested(it), 'facts': dict(
                        {'kind': 'reminder', 'id': it['id'], 'due': it['due'], 'repeat': it['repeat'], 'set_on': it['created'][:10]},
                        **({'late': True} if late > remmod.LATE_FLAG_S else {}))})
                    it['fired_at'] = clock
                if it['repeat'] == 'none' or not remmod.advance_past(it, now):
                    it['status'] = 'done'
                    it.setdefault('fired_at', clock)
            s['reminders'] = items
        if not st.state().get('reminders'):
            return 0
        st.update_state(mark)
        for rid in dropped:
            self.log('persona %s: reminder %s too late, dropped' % (key, rid))
        for ev in out:
            self.maybe_intro(key)
            st.append_event(ev)
            self.log('persona %s: reminder %s queued' % (key, ev['facts']['id']))
        return len(out)

    # ---- scheduled work ----
    def tick_poll(self, key):
        """Fetch new private messages of the person into the persona's inbox. -> number added."""
        if key not in self.discord:
            return 0
        d, st, uid = self.discord[key], self.stores[key], self.cfg[key]['user_id']
        ch = self._channel(key)
        last = st.state().get('last_seen')
        if last is None:   # first run: start from the newest message, do not replay history
            m = d.messages_after(ch, None, 1)
            st.update_state(lambda s: s.__setitem__('last_seen', m[0]['id'] if m else '0'))
            return 0
        msgs = d.messages_after(ch, last, 50)
        if not msgs:
            return 0
        newest = max(msgs, key=lambda m: int(m['id']))['id']
        added = 0
        for m in sorted(msgs, key=lambda m: int(m['id'])):
            if str(m.get('author', {}).get('id')) != uid:
                continue
            self.maybe_intro(key)
            ev = {'type': 'message', 'msg_id': m['id'], 'text': str(m.get('content') or ''),
                             'attachments': len(m.get('attachments') or []),
                             'files': [{'n': i, 'name': safe_name(a.get('filename')), 'size': int(a.get('size') or 0), 'type': str(a.get('content_type') or '')}
                                       for i, a in enumerate((m.get('attachments') or [])[:ATTACH_MAX_N])]}
            self.trace_message(key, ev)
            st.append_event(ev)
            added += 1
        st.update_state(lambda s: s.__setitem__('last_seen', newest))
        if added:
            self.typing_now(key, newest)
            try:
                self.usage_autoreply(key, added)
            except Exception as e:
                self.log('persona %s: usage auto-reply error %s' % (key, type(e).__name__))
        return added

    def typing_now(self, key, msg_id):
        """0.9.8: show "typing" the moment a message is seen (before the persona's Claude has even started) and keep it
        until the persona answers. Never raises: a failed typing call must not lose the message."""
        try:
            if self.is_live(key) and key in self.discord:
                self.discord[key].typing(self._channel(key))
            self.typing(key, {'msg_id': str(msg_id)})
        except Exception as e:
            self.log('persona %s: instant typing failed (%s)' % (key, type(e).__name__))

    # ---- behaviour levels (0.7.0) ----
    def lv(self, key):
        return self.cfg[key]['levels']

    def checkin_cap(self, key):
        """Check-ins allowed per day = max_per_day scaled by checkin_level (0 = none)."""
        return levels.checkin_cap(self.cfg[key]['max_per_day'], self.lv(key)['checkin_level'])

    def behaviour(self, key):
        """The levels, the prompt block for the persona prompt, and a fresh gif decision (random draw each call)."""
        lv = self.lv(key)
        roll = self.rng()
        return {'ok': True, 'levels': dict(lv), 'prompt': levels.prompt_block(lv, key, self.cfg[key].get('fixed_rule')), 'gif_ok': levels.gif_allowed(lv['gif_level'], roll),
                'emoji_max': levels.emoji_cap(lv['emoji_level']), 'checkins_per_day_cap': self.checkin_cap(key)}

    def maybe_intro(self, key):
        """Not introduced yet: put ONE intro_due event first (no name; the host writes the text)."""
        st = self.stores[key]
        if st.profile()['introduced'] or st.state().get('intro_requested'):
            return False
        st.append_event({'type': 'intro_due'})
        st.update_state(lambda s: s.__setitem__('intro_requested', True))
        return True

    # ---- day rules, texts and the shared check-in queue (0.6.0) ----
    def day_allowed(self, key, now=None):
        """False on a weekday not in checkin_days (empty = every day) or on a closed date / range (for example college closed)."""
        c, now = self.cfg[key], now or self.now()
        if c['checkin_days'] and DAYS[now.weekday()] not in c['checkin_days']:
            return False
        d = now.date().isoformat()
        for r in c['closed_dates']:
            a, _, b = r.partition('..')
            if a <= d <= (b or a):
                return False
        return True

    def pick_text(self, key, now=None):
        """A check-in text for today's weekday (entries 'tue|text' or '*|text'; the weekday ones win). '' when none."""
        c, now = self.cfg[key], now or self.now()
        wd = DAYS[now.weekday()]
        mine = [t.partition('|')[2] for t in c['checkin_texts'] if t.startswith(wd + '|')]
        pool = mine or [t.partition('|')[2] for t in c['checkin_texts'] if t.startswith('*|')]
        if not pool:
            return ''
        return pool[int(self.clock()) // 60 % len(pool)]   # rotates by minute: stable inside a tick, no random needed

    def queue_checkin(self, key, planned, reason, slot=None):
        """Queue the checkin_due event. With checkin_texts the event carries a suggested text; with checkin_send AND live the add-on sends it itself (sent: true)."""
        st = self.stores[key]
        ev = {'type': 'checkin_due', 'planned': planned, 'reason': reason}
        if slot:
            ev['slot'] = slot
        text = self.pick_text(key)
        if text:
            ev['text'] = text
            ev['sent'] = False
            if self.cfg[key]['checkin_send']:
                if not self.is_live(key):
                    st.log_outbox({'kind': 'checkin', 'chars': len(text), 'mode': 'dry'})
                    self.log('persona %s: [DRY] would send check-in (%d chars)' % (key, len(text)))
                elif key in self.discord:
                    try:
                        ok = self.discord[key].send(self._channel(key), text, None)
                    except DiscordError as e:
                        ok = False
                        self.log('persona %s: check-in send failed (%s)' % (key, e))
                    if ok:
                        self.watch_sent(key)
                    st.log_outbox({'kind': 'checkin', 'chars': len(text), 'mode': 'live', 'ok': ok})
                    ev['sent'] = bool(ok)
        st.append_event(ev)

    # ---- usage auto-reply (rows 11 and 21) ----
    def usage_out(self):
        """(is_out, reset datetime or None). Bad or missing sensors (or no HA) = not out."""
        if self.ha is None:
            return False, None
        num = lambda e: self._num_state(e)
        try:
            sv, wv = num(USAGE_SENSORS['session']), num(USAGE_SENSORS['week'])
        except Exception:
            return False, None
        so, wo = sv is not None and sv >= USAGE_OUT_AT, wv is not None and wv >= USAGE_OUT_AT
        if not (so or wo):
            return False, None
        times = []
        for flag, ent in ((so, USAGE_SENSORS['session_reset']), (wo, USAGE_SENSORS['week_reset'])):
            if flag:
                try:
                    v = (self.ha.state(ent) or {}).get('state')
                    d = datetime.datetime.fromisoformat(str(v).replace('Z', '+00:00'))
                    times.append(d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc))
                except Exception:
                    pass
        return True, (max(times) if times else None)

    def _num_state(self, ent):
        try:
            return float((self.ha.state(ent) or {}).get('state'))
        except (TypeError, ValueError):
            return None

    def usage_reply_text(self, key, reset):
        c = self.cfg[key]
        if not c['usage_text']:
            return ''
        if reset is None:
            return c['usage_text_notime'] or c['usage_text'].replace(' until {t}', '').replace('{t}', 'it resets')
        loc = reset.astimezone(self.tz) if self.tz else reset
        far = (reset.timestamp() - self.clock()) > USAGE_FAR_H * 3600
        return c['usage_text'].replace('{t}', loc.strftime('%a %H:%M') if far else loc.strftime('%H:%M'))

    def usage_autoreply(self, key, n_new):
        """The person wrote n_new messages and Claude usage is out: ONE fallback reply per USAGE_MIN_GAP_S. -> True if sent (or dry-logged)."""
        c, st = self.cfg[key], self.stores[key]
        if n_new <= 0 or not c['usage_text'] or os.path.exists(os.path.join(os.environ.get('IHP_DATA', '/data'), 'STOP_AUTOREPLY')):
            return False
        out, reset = self.usage_out()
        if not out:
            return False
        if self.clock() - float(st.state().get('usage_reply_ts') or 0) < USAGE_MIN_GAP_S:
            return False
        text = self.usage_reply_text(key, reset)
        if not text:
            return False
        if not self.is_live(key) or key not in self.discord:
            st.log_outbox({'kind': 'usage_reply', 'chars': len(text), 'mode': 'dry'})
            self.log('persona %s: [DRY] would send usage auto-reply (%d chars)' % (key, len(text)))
            return True   # dry never touches the gap clock, like the host script
        try:
            ok = self.discord[key].send(self._channel(key), text, None)
        except DiscordError as e:
            self.log('persona %s: usage reply failed (%s)' % (key, e)); return False
        if ok:
            self.watch_sent(key)
        st.log_outbox({'kind': 'usage_reply', 'chars': len(text), 'mode': 'live', 'ok': ok})
        if ok:
            st.update_state(lambda s: s.__setitem__('usage_reply_ts', self.clock()))
            self.log('persona %s: usage auto-reply sent' % key)
        return bool(ok)

    def tick_checkins(self, key):
        """Queue checkin_due events whose time just passed (within CHECKIN_WINDOW_MIN), outside quiet hours, under max_per_day. -> list of times fired."""
        c, st, now = self.cfg[key], self.stores[key], self.now()
        day, nm = now.strftime('%Y-%m-%d'), now.hour * 60 + now.minute
        fired = []
        if not self.day_allowed(key, now):
            return fired   # not a check-in day, or a closed date
        if self.checkin_cap(key) <= 0:
            return fired   # checkin_level 0 = never start a conversation
        def done_for(s):
            cd = s.setdefault('checkins_done', {})
            if cd.get('day') != day:
                cd.clear(); cd['day'] = day; cd['times'] = []
            return cd['times']
        for t in c['checkins']:
            if t in done_for(st.state()):
                continue
            delta = nm - to_min(t)
            if delta < 0:
                continue
            if delta > CHECKIN_WINDOW_MIN:
                st.update_state(lambda s, t=t: done_for(s).append(t)); continue   # missed (add-on was off): drop, never fire late
            skip = in_quiet(nm, c['quiet_start'], c['quiet_end']) or self.checkins_today(key) >= self.checkin_cap(key)
            st.update_state(lambda s, t=t: done_for(s).append(t))
            if skip:
                continue
            self.maybe_intro(key)
            self.queue_checkin(key, t, 'fixed', slot=t)
            self._count_out(key, 'checkin')
            self._note_fired(key, t)
            fired.append(t)
        if c['checkin_plan']:
            fired += self.tick_plan(key)
        return fired

    # ---- planned check-ins (checkin_plan) ----
    def _note_fired(self, key, t):
        day = self._day()
        def f(s):
            fd = s.setdefault('fired_today', {})
            if fd.get('day') != day:
                fd.clear(); fd['day'] = day; fd['times'] = []
            fd['times'].append(t)
        self.stores[key].update_state(f)

    def _fired_times(self, key):
        fd = self.stores[key].state().get('fired_today', {})
        return fd.get('times', []) if fd.get('day') == self._day() else []

    def _event_span(self, ev, day, tz):
        """(start_min, end_min) of a timed calendar event on `day`, or None (all-day events never block)."""
        def one(v):
            if isinstance(v, dict):
                v = v.get('dateTime') or v.get('date')
            if not isinstance(v, str) or 'T' not in v:
                return None
            d = datetime.datetime.fromisoformat(v.replace('Z', '+00:00'))
            return d.astimezone(tz).replace(tzinfo=None) if d.tzinfo else d
        s, e = one(ev.get('start')), one(ev.get('end'))
        if s is None or e is None:
            return None
        a = 0 if s.date() < day else s.hour * 60 + s.minute
        b = 24 * 60 if e.date() > day else e.hour * 60 + e.minute
        return planner.event_window(a, b) if b > a else None

    def busy_for(self, key, day):
        """-> (busy spans [(a, b)], note). Calendar events + meal windows. HA errors: meals only, note names the error type."""
        c, out, note = self.cfg[key], [], ''
        tz = self.tz or datetime.timezone.utc
        eat = None
        try:
            if c['calendars'] or c['meal_plan_entity']:
                if self.ha is None:
                    raise RuntimeError('no HA client')
            start = datetime.datetime.combine(day, datetime.time(0, 0), tz)
            end = start + datetime.timedelta(days=1)
            for ent in c['calendars']:
                for ev in self.ha.calendar_events(ent, start, end) or []:
                    sp = self._event_span(ev, day, tz)
                    if sp:
                        out.append(sp)
        except Exception as e:
            note = 'calendar unreadable (%s)' % type(e).__name__
            self.log('persona %s: %s, planning without calendars' % (key, note))
        try:
            if c['meal_plan_entity'] and self.ha is not None:
                eat = self._eat_minute(self.ha.state(c['meal_plan_entity']), day)
        except Exception as e:
            self.log('persona %s: meal sensor unreadable (%s)' % (key, type(e).__name__))
            note = note or 'meal sensor unreadable (%s)' % type(e).__name__
        if c.get('meal_fact') and self.opts.get('facts_url') and self.opts.get('facts_key'):   # the orchestrator fact wins over the sensor when it has today's time
            try:
                got = self._read_fact(c['meal_fact'])
                if got is None:
                    self.log('persona %s: meal fact %s not set, default meal windows' % (key, c['meal_fact']))
                else:
                    e2 = self._fact_eat(got, day)
                    eat = e2 if e2 is not None else eat
            except Exception as e:
                self.log('persona %s: meal fact unreadable (%s), default meal windows' % (key, type(e).__name__))
                note = note or 'meal fact unreadable (%s)' % type(e).__name__
        # static layers: busy_windows + default meals, minus free_windows (free wins). The eat-time window and calendar events always block.
        wd = day.weekday()
        eatwin = (max(0, eat - 60), min(24 * 60, eat + 45)) if eat is not None else None
        static = planner.windows_for(c['busy_windows'], wd) + [m for m in planner.meal_windows(eat) if m != eatwin]
        static = planner.subtract(static, planner.windows_for(c['free_windows'], wd))
        return sorted(out + static + ([eatwin] if eatwin else [])), note

    def _read_fact(self, ref):
        """Value of orchestrator fact 'module/name' (GET facts_url/facts/<ref>, 5 s). None when it is not set (404 or expired). Other errors raise."""
        req = urllib.request.Request('%s/facts/%s' % (self.opts['facts_url'], ref), headers={'Authorization': 'Bearer ' + self.opts['facts_key']})
        try:
            with (self.facts_opener or urllib.request.urlopen)(req, timeout=FACT_TIMEOUT_S) as r:
                return json.loads(r.read())['value']
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def _fact_eat(self, value, day):
        """Eat minute of `day` from a fact value: 'HH:MM' (today only) or {'date': 'YYYY-MM-DD', 'eat': 'HH:MM'} (only when the date is `day`)."""
        if isinstance(value, dict):
            eat = value.get('eat')
            if value.get('date') != day.isoformat() or not isinstance(eat, str) or not HHMM.match(eat.strip()):
                return None
            return to_min(eat.strip())
        return self._eat_minute(value, day) if isinstance(value, str) and re.match(r'^\d{1,2}:\d{2}$', value.strip()) else None

    def _eat_minute(self, state, day):
        """Planned eating time of `day` from a sensor state: 'HH:MM' / 'HH:MM:SS' (only for today) or an ISO date-time on that day."""
        if not isinstance(state, str):
            return None
        m = re.match(r'^(\d{1,2}):(\d{2})(:\d{2})?$', state.strip())
        if m:
            return int(m.group(1)) * 60 + int(m.group(2)) if day == self.now().date() and int(m.group(1)) < 24 else None
        try:
            d = datetime.datetime.fromisoformat(state.strip().replace('Z', '+00:00'))
        except ValueError:
            return None
        if d.tzinfo and self.tz:
            d = d.astimezone(self.tz)
        return d.hour * 60 + d.minute if d.date() == day else None

    def _quiet_fn(self, key):
        c = self.cfg[key]
        return lambda t: in_quiet(t, c['quiet_start'], c['quiet_end'])

    def make_day_plan(self, key, day, rebuild=False):
        """Build and store the plan for `day` (once). Agreed times first, then random ones up to the day's target (a fixed number, or a
        number from a range such as 2-3 picked with a seed per date, so the plan is stable).
        rebuild=True (HA was down at plan time): replace only random slots still in the future; agreed and finished slots stay."""
        c, st = self.cfg[key], self.stores[key]
        dkey = day.isoformat()
        busy, note = self.busy_for(key, day)
        n = self.now()
        nm = n.hour * 60 + n.minute
        s0 = st.state()
        old = s0.get('plans', {}).get(dkey)
        if rebuild:
            if note:   # still down: try again later
                st.update_state(lambda s: s['plans'][dkey].__setitem__('tried', self.clock()))
                return False
            keep = [x for x in old['slots'] if x['kind'] == 'agreed' or x['status'] != 'pending' or to_min(x['time']) <= nm]
            target = old['target']
        else:
            keep, target = [], planner.pick_target(c['checkins_per_day'], '%s-%s-n' % (key, dkey))
        agreed = [a['time'] for a in s0.get('agreed', []) if a['date'] == dkey]
        floor = nm + 10 if day == n.date() else 0
        wake = (to_min(c['wake_start']), to_min(c['wake_end']))
        slots = planner.make_plan('%s-%s%s' % (key, dkey, '-r' if rebuild else ''), wake, busy, agreed, target, int(c['min_gap_h'] * 60), max(floor, wake[0]), self._quiet_fn(key), keep)
        plan = {'target': target, 'slots': slots, 'note': note, 'tried': self.clock()}
        def put(s):
            ps = s.setdefault('plans', {})
            for k in [k for k in ps if k < (day - datetime.timedelta(days=3)).isoformat()]:
                del ps[k]
            if rebuild:
                ps[dkey] = plan
            else:
                ps.setdefault(dkey, plan)
        st.update_state(put)
        self.log('persona %s: plan for the day %s: %d times%s' % (key, 'rebuilt' if rebuild else 'made', len(slots), ' (' + note + ')' if note else ''))
        return True

    def last_message_ts(self, key):
        """Unix time of the person's last message, from the persona's own inbox timestamps (None = none seen)."""
        last = None
        for ev in self.stores[key].read_inbox(0, 500):
            if ev.get('type') == 'message':
                try:
                    t = datetime.datetime.strptime(ev['ts'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc).timestamp()
                except (KeyError, ValueError, TypeError):
                    continue
                last = t if last is None else max(last, t)
        return last

    def minutes_since_message(self, key):
        t = self.last_message_ts(key)
        return None if t is None else (self.clock() - t) / 60

    def minutes_since_sent(self, key):
        """Minutes since the persona last sent anything (reply or question), from its own outbox log."""
        t = self.stores[key].last_outbox_ts(('reply', 'ask'))
        return None if t is None else (self.clock() - t) / 60

    def _skip_reason(self, key, slot, nm):
        c = self.cfg[key]
        if in_quiet(nm, c['quiet_start'], c['quiet_end']):
            return 'quiet hours'
        if self.checkins_today(key) >= self.checkin_cap(key):
            return 'max per day'
        age = self.minutes_since_message(key)
        if age is not None and age < c['quiet_if_messaged_h'] * 60:
            return 'messaged recently'
        if not (to_min(c['wake_start']) <= nm <= to_min(c['wake_end'])):
            return 'outside waking hours'
        if slot['kind'] == 'agreed':
            return None   # an agreed time is exempt from the checks below (gap, spoke recently, unanswered, presence, busy)
        sent = self.minutes_since_sent(key)
        if sent is not None and sent < c['quiet_if_messaged_h'] * 60:
            return 'persona spoke recently'
        got = self.last_message_ts(key)
        out_ts = self.stores[key].last_outbox_ts(('reply', 'ask'))
        if sent is not None and sent < 360 and (got is None or out_ts > got):
            return 'last message is unanswered'
        gap = c['min_gap_h'] * 60 * levels.checkin_gap_factor(c['levels']['checkin_level'])
        if any(abs(nm - to_min(t)) < gap for t in self._fired_times(key)):
            return 'too soon after the last check-in'
        if c['presence_entity'] and self.ha is not None:
            try:
                if self.ha.state(c['presence_entity']) in ('not_home', 'off', 'away'):
                    return 'not home'
            except Exception as e:
                self.log('persona %s: presence unreadable (%s)' % (key, type(e).__name__))
        if slot['kind'] == 'random':
            busy, _ = self.busy_for(key, self.now().date())
            if any(x <= nm < y for x, y in busy):
                return 'busy now'
        return None

    def tick_plan(self, key):
        """Plan the day if needed, then fire due slots. -> list of times fired."""
        c, st, now = self.cfg[key], self.stores[key], self.now()
        day = now.date()
        dkey, nm = day.isoformat(), now.hour * 60 + now.minute
        pl0 = st.state().get('plans', {}).get(dkey)
        if pl0 is None:
            self.make_day_plan(key, day)
        elif pl0.get('note') and self.clock() - pl0.get('tried', 0) >= PLAN_RETRY_S and self.ha is not None:
            self.make_day_plan(key, day, rebuild=True)
        fired = []
        for i, sl in enumerate(st.state().get('plans', {}).get(dkey, {}).get('slots', [])):
            if sl['status'] != 'pending':
                continue
            t = to_min(sl['time'])
            if nm < t:
                continue
            if nm > t + CHECKIN_WINDOW_MIN:
                status, why = 'missed', None
            else:
                why = self._skip_reason(key, sl, nm)
                status = 'skipped' if why else 'sent'
            def mark(s, i=i, status=status):
                s['plans'][dkey]['slots'][i]['status'] = status
            st.update_state(mark)
            if status == 'sent':
                self.maybe_intro(key)
                self.queue_checkin(key, sl['time'], sl['kind'])
                self._count_out(key, 'checkin')
                self._note_fired(key, sl['time'])
                fired.append(sl['time'])
            elif why:
                self.log('persona %s: planned check-in skipped (%s)' % (key, why))
        return fired

    def parse_date(self, v):
        today = self.now().date()
        if v in (None, 'today'):
            return today
        if v == 'tomorrow':
            return today + datetime.timedelta(days=1)
        try:
            if isinstance(v, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', v):
                return datetime.date.fromisoformat(v)
        except ValueError:
            pass
        return None

    def _agree_args(self, key, body):
        d, t = self.parse_date(body.get('date')), body.get('time')
        if d is None:
            return None, None, (400, {'ok': False, 'why': 'date: today, tomorrow or YYYY-MM-DD'})
        if not isinstance(t, str) or not HHMM.match(t):
            return None, None, (400, {'ok': False, 'why': 'time: HH:MM'})
        return d, t, None

    def agree(self, key, body):
        """Save a time the person and the persona agreed. It goes into that day's plan as an 'agreed' slot."""
        if not self.cfg[key]['checkin_plan']:
            return 409, {'ok': False, 'why': 'checkin_plan is off for this persona'}
        d, t, err = self._agree_args(key, body)
        if err:
            return err
        n = self.now()
        if d < n.date() or (d == n.date() and to_min(t) + CHECKIN_WINDOW_MIN < n.hour * 60 + n.minute):
            return 400, {'ok': False, 'why': 'that time has passed'}
        dkey = d.isoformat()
        def put(s):
            ag = [a for a in s.get('agreed', []) if a['date'] >= n.date().isoformat()]
            if not any(a['date'] == dkey and a['time'] == t for a in ag):
                if len(ag) >= AGREED_MAX:
                    return False
                ag.append({'date': dkey, 'time': t})
            s['agreed'] = ag
            pl = s.get('plans', {}).get(dkey)
            if pl and not any(x['time'] == t for x in pl['slots']):
                pl['slots'].append({'time': t, 'kind': 'agreed', 'status': 'pending'})
                pl['slots'].sort(key=lambda x: x['time'])
            return True
        if not self.stores[key].update_state(put):
            return 429, {'ok': False, 'why': 'at most %d agreed times' % AGREED_MAX}
        self.log('persona %s: agreed time saved' % key)
        return 200, {'ok': True, 'date': dkey, 'time': t}

    def unagree(self, key, body):
        d, t, err = self._agree_args(key, body)
        if err:
            return err
        dkey = d.isoformat()
        def rm(s):
            before = len(s.get('agreed', []))
            s['agreed'] = [a for a in s.get('agreed', []) if not (a['date'] == dkey and a['time'] == t)]
            pl = s.get('plans', {}).get(dkey)
            if pl:
                pl['slots'] = [x for x in pl['slots'] if not (x['time'] == t and x['kind'] == 'agreed' and x['status'] == 'pending')]
            return before - len(s['agreed'])
        n = self.stores[key].update_state(rm)
        return 200, {'ok': True, 'removed': n}

    def plan_view(self, key):
        """Times only: today's plan and the agreed times."""
        s, dkey = self.stores[key].state(), self._day()
        pl = s.get('plans', {}).get(dkey)
        return {'ok': True, 'enabled': self.cfg[key]['checkin_plan'], 'date': dkey,
                'slots': [{'time': x['time'], 'kind': x['kind'], 'status': x['status']} for x in (pl or {}).get('slots', [])] if pl else None,
                'agreed': [{'date': a['date'], 'time': a['time']} for a in sorted(s.get('agreed', []), key=lambda a: (a['date'], a['time']))]}

    def tick(self, key):
        n = f = 0
        try:
            if self.poll_due(key):
                self.poll_at[key] = self.clock()
                n = self.tick_poll(key)
        except DiscordError as e:
            self.log('persona %s: poll failed (%s)' % (key, e))
        for fn in (self.tick_reactions, self.tick_typing):
            try:
                fn(key)
            except DiscordError as e:
                self.log('persona %s: %s failed (%s)' % (key, fn.__name__, e))
        try:
            self.tick_dates(key)
        except Exception as e:
            self.log('persona %s: dates failed (%s)' % (key, type(e).__name__))
        try:
            self.tick_reminders(key)
        except Exception as e:
            self.log('persona %s: reminders failed (%s)' % (key, type(e).__name__))
        try:
            f = len(self.tick_checkins(key))
        except StoreError as e:
            self.log('persona %s: checkin failed (%s)' % (key, e))
        try:
            self.publish_reply_progress(key)
        except Exception as e:
            self.log('persona %s: progress publish failed (%s)' % (key, type(e).__name__))
        return n, f

    def publish_reply_progress(self, key, force=False):
        """Publish counts/timestamps only to the existing orchestrator facts store."""
        url, token = self.opts.get('facts_url'), self.opts.get('facts_key')
        if not (url and token):
            return False
        now = float(self.clock())
        if not force and now - self.progress_publish_at.get(key, 0) < 60:
            return False
        self.progress_publish_at[key] = now
        snap = self.stores[key].reply_progress_snapshot(now)
        if snap is None:
            return False
        snap['enabled'] = bool(self.is_live(key))
        req = urllib.request.Request('%s/facts/assistant/progress_%s' % (url.rstrip('/'), key),
                                     data=json.dumps({'value': snap, 'ttl_s': 300}).encode(),
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                                     method='PUT')
        try:
            with (self.facts_opener or urllib.request.urlopen)(req, timeout=FACT_TIMEOUT_S) as r:
                return 200 <= getattr(r, 'status', 200) < 300
        except Exception as e:
            self.log('persona %s: progress publish unavailable (%s)' % (key, type(e).__name__))
            return False

    def complete_message(self, key, body):
        """Record a completed bridge command outcome; this endpoint never sends or replays."""
        event_id = body.get('event_id')
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 1:
            return 400, {'ok': False, 'why': 'positive event_id required'}
        try:
            done = self.stores[key].complete_message(event_id=event_id)
        except Exception as e:
            self.log('persona %s: completion receipt failed (%s)' % (key, type(e).__name__))
            return 503, {'ok': False, 'why': 'completion receipt unavailable'}
        if not done:
            return 404, {'ok': False, 'why': 'tracked message not found'}
        self.publish_reply_progress(key, force=True)
        return 200, {'ok': True, 'completed': True}

    # ---- API actions (return (http code, body)) ----
    def reply(self, key, body):
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
            return 400, {'ok': False, 'why': 'text required (1..%d characters)' % TEXT_MAX}
        text = levels.strip_emoji(text, self.lv(key)['emoji_level']) or text   # hard cap on emoji per reply
        rt = body.get('reply_to')
        rt = str(rt) if rt is not None and MARK_RE.match(str(rt)) else None
        st = self.stores[key]
        if not self.is_live(key):
            st.log_outbox({'kind': 'reply', 'chars': len(text), 'mode': 'dry'})
            self._count_out(key, 'reply')
            st.update_state(lambda s: s.pop('keepers', None))
            self.log('persona %s: [DRY] would send %d chars' % (key, len(text)))
            return 200, {'ok': True, 'sent': False, 'dry': True, 'chars': len(text), 'gif': 'dry' if body.get('gif') else None}
        if key not in self.discord:
            return 409, {'ok': False, 'why': 'no bot configured'}
        try:
            ok = self.discord[key].send(self._channel(key), text, rt)
        except DiscordError as e:
            self.log('persona %s: send failed (%s)' % (key, e))
            return 502, {'ok': False, 'why': 'discord: %s' % e}
        if ok:
            self.watch_sent(key)
        st.log_outbox({'kind': 'reply', 'chars': len(text), 'mode': 'live', 'ok': ok})
        if ok:
            st.update_state(lambda s: s.pop('keepers', None))
            self._count_out(key, 'reply')
            completed = False
            if rt:
                try:
                    self.trace_reply(key, rt, len(text), len(text.encode('utf-8')))
                    completed = st.complete_message(msg_id=rt)
                    self.publish_reply_progress(key, force=True)
                except Exception as e:
                    self.log('persona %s: reply sent; completion receipt unavailable (%s)' % (key, type(e).__name__))
            if not st.profile()['introduced'] and st.state().get('intro_requested'):
                st.merge_profile({'introduced': True})
        self.log('persona %s: sent %d chars' % (key, len(text)))
        gif = self.send_gif(key, body.get('gif'), bool(body.get('gif_asked'))) if ok and body.get('gif') else None
        return 200, {'ok': ok, 'sent': ok, 'dry': False, 'chars': len(text), 'completion_recorded': bool(ok and rt and completed), 'gif': gif}

    # ---- GIFs (0.9.9 sends links; 0.9.10 attaches the picture and keeps a cache) ----
    def gif_tokens(self, words):
        return sorted({w for w in re.findall(r'[a-z0-9]+', str(words or '').lower()) if len(w) > 2 and w not in GIF_STOP})

    def gif_cache(self):
        try:
            with open(os.path.join(self.data_dir, 'gif_cache.json')) as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def gif_cache_save(self, items):
        items = items[-GIF_CACHE_MAX:]
        d = os.path.join(self.data_dir, 'gifs')
        keep = {i['file'] for i in items}
        try:
            for f in os.listdir(d):
                if f not in keep:
                    os.remove(os.path.join(d, f))
        except OSError:
            pass
        tmp = os.path.join(self.data_dir, 'gif_cache.json.tmp')
        with open(tmp, 'w') as f:
            json.dump(items, f)
        os.replace(tmp, os.path.join(self.data_dir, 'gif_cache.json'))

    def gif_fits(self, tokens, item):
        a, b = set(tokens), set(item.get('tokens') or [])
        return bool(a and b) and len(a & b) / float(len(a | b)) >= 0.5

    def send_gif(self, key, words, asked=False):
        """Send ONE GIF picture after a reply if the persona's gif level allows it (random draw) and none went out in the last
        gif_gap_minutes (default 10). 50% of the time a fitting GIF from the cache is reused (saves a GIPHY call, keeps it varied);
        otherwise GIPHY is searched (PG only), the picture downloaded (under 8 MB) and cached. Never raises. -> short status word."""
        try:
            words = re.sub(r'\s+', ' ', str(words or '')).strip()[:GIF_WORDS_MAX]
            tokens = self.gif_tokens(words)
            if not tokens:
                return 'no words'
            if not asked and not levels.gif_allowed(self.lv(key)['gif_level'], self.rng()):   # 0.9.11: the person asked for one -> no dice roll, no gap
                return 'skipped: level'
            st, now = self.stores[key], self.clock()
            gap = max(0, int(self.opts.get('gif_gap_minutes', 10))) * 60
            if not asked and now - float(st.state().get('last_gif') or 0) < gap:
                return 'skipped: too soon'
            cache = self.gif_cache()
            fitting = [i for i in cache if self.gif_fits(tokens, i) and os.path.exists(os.path.join(self.data_dir, 'gifs', i['file']))]
            item, how = None, 'new'
            if fitting and self.rng() < GIF_REUSE_CHANCE:
                item, how = fitting[int(self.rng() * len(fitting)) % len(fitting)], 'reused'
            elif self.opts.get('giphy_api_key'):
                try:
                    item = self.gif_fetch(words, tokens, cache)
                except Exception as e:
                    self.log('persona %s: GIPHY failed (%s)' % (key, type(e).__name__))
            if item is None and fitting:
                item, how = fitting[0], 'reused'   # GIPHY unavailable: a cached one beats nothing
            if item is None:
                return 'none found' if self.opts.get('giphy_api_key') else 'no key'
            if not self.discord[key].send_file(self._channel(key), os.path.join(self.data_dir, 'gifs', item['file'])):
                return 'send failed'
            st.update_state(lambda s: s.__setitem__('last_gif', now))
            self.log('persona %s: gif sent (%s)' % (key, how))
            return 'sent (%s)' % how
        except Exception as e:
            self.log('persona %s: gif failed (%s)' % (key, type(e).__name__))
            return 'failed'

    def gif_fetch(self, words, tokens, cache):
        """Search GIPHY, download one picture into the cache. -> the cache item, or None."""
        opener = self.gif_opener or urllib.request.urlopen
        q = urllib.parse.urlencode({'api_key': self.opts['giphy_api_key'], 'q': words, 'limit': 10, 'rating': 'pg', 'lang': 'en'})
        with opener('https://api.giphy.com/v1/gifs/search?' + q, timeout=10) as r:
            data = json.load(r).get('data') or []
        cands = []
        for g in data[:5]:
            im = (g.get('images') or {})
            url = ((im.get('downsized') or im.get('original') or {}).get('url') or '')
            if re.fullmatch(r'[A-Za-z0-9]{3,40}', str(g.get('id', ''))) and re.match(r'https://[a-z0-9.\-]*giphy\.com/', url):
                cands.append((g['id'], url))
        if not cands:
            return None
        gid, url = cands[int(self.rng() * len(cands)) % len(cands)]
        for i in cache:
            if i['id'] == gid and os.path.exists(os.path.join(self.data_dir, 'gifs', i['file'])):
                i['tokens'] = sorted(set(i.get('tokens') or []) | set(tokens)); self.gif_cache_save(cache)
                return i
        with opener(url, timeout=20) as r:
            blob = r.read(GIF_MAX_BYTES + 1)
        if not blob or len(blob) > GIF_MAX_BYTES or not blob.startswith(b'GIF8'):
            return None
        os.makedirs(os.path.join(self.data_dir, 'gifs'), exist_ok=True)
        item = {'id': gid, 'file': gid + '.gif', 'tokens': tokens}
        with open(os.path.join(self.data_dir, 'gifs', item['file']), 'wb') as f:
            f.write(blob)
        self.gif_cache_save(cache + [item])
        return item

    # ---- calendars (0.10.0): the persona reads and adds events, only in the calendars set for that person ----
    CAL_MAX_DAYS, CAL_MAX_EVENTS = 90, 80

    def cal_readable(self, key):
        c = self.cfg[key]
        return list(dict.fromkeys(c.get('calendars', []) + c.get('calendar_read', []) + c.get('calendar_write', [])))

    def calendar_view(self, key, days=14, start=None):
        """Events from this persona's calendars, soonest first. -> (code, body). Read only."""
        if self.ha is None:
            return 503, {'ok': False, 'why': 'no Home Assistant access'}
        cals = self.cal_readable(key)
        if not cals:
            return 404, {'ok': False, 'why': 'no calendars are set for this persona'}
        try:
            days = max(1, min(self.CAL_MAX_DAYS, int(days)))
            now = self.now()
            t0 = datetime.datetime.combine(datetime.date.fromisoformat(start), datetime.time(0, 0), tzinfo=now.tzinfo) if start else now.replace(hour=0, minute=0, second=0, microsecond=0)
        except (ValueError, TypeError):
            return 400, {'ok': False, 'why': 'days is a number and start is YYYY-MM-DD'}
        t1, out, bad = t0 + datetime.timedelta(days=days), [], []
        for ent in cals:
            try:
                for ev in self.ha.calendar_events(ent, t0, t1) or []:
                    s, e = ev.get('start') or {}, ev.get('end') or {}
                    allday = 'date' in s and 'dateTime' not in s
                    out.append({'calendar': ent, 'summary': str(ev.get('summary') or '')[:200], 'all_day': allday,
                                'start': s.get('dateTime') or s.get('date') or '', 'end': e.get('dateTime') or e.get('date') or '',
                                'location': str(ev.get('location') or '')[:200], 'uid': str(ev.get('uid') or '')[:120],
                                'recurring': bool(ev.get('rrule') or ev.get('recurrence_id'))})
            except Exception as ex:
                bad.append(ent); self.log('persona %s: calendar %s unreadable (%s)' % (key, ent, type(ex).__name__))
        out.sort(key=lambda x: x['start'])
        return 200, {'ok': True, 'from': t0.date().isoformat(), 'days': days, 'events': out[:self.CAL_MAX_EVENTS], 'truncated': len(out) > self.CAL_MAX_EVENTS,
                     'unreadable': bad, 'writable': self.cfg[key].get('calendar_write', [])}

    def calendar_add(self, key, body):
        """Add ONE event to a calendar in this persona's calendar_write list. Add only: no edit, no delete. -> (code, body)."""
        cal, summ = str(body.get('calendar') or ''), re.sub(r'\s+', ' ', str(body.get('summary') or '')).strip()
        if cal not in self.cfg[key].get('calendar_write', []):
            return 403, {'ok': False, 'why': 'this persona may not add to %s' % (cal or 'that calendar'), 'writable': self.cfg[key].get('calendar_write', [])}
        if not summ or len(summ) > 200:
            return 400, {'ok': False, 'why': 'summary required (1..200 characters)'}
        start, end = str(body.get('start') or '').strip(), str(body.get('end') or '').strip()
        try:
            if re.fullmatch(r'\d{4}-\d\d-\d\d', start):   # all-day event
                d0 = datetime.date.fromisoformat(start)
                d1 = datetime.date.fromisoformat(end) if re.fullmatch(r'\d{4}-\d\d-\d\d', end) else d0 + datetime.timedelta(days=1)
                if d1 <= d0:
                    d1 = d0 + datetime.timedelta(days=1)
                data = {'start_date': d0.isoformat(), 'end_date': d1.isoformat()}
            else:
                t0 = datetime.datetime.strptime(start, '%Y-%m-%d %H:%M')
                t1 = datetime.datetime.strptime(end, '%Y-%m-%d %H:%M') if end else t0 + datetime.timedelta(minutes=60)
                if t1 <= t0:
                    t1 = t0 + datetime.timedelta(minutes=60)
                data = {'start_date_time': t0.strftime('%Y-%m-%d %H:%M:%S'), 'end_date_time': t1.strftime('%Y-%m-%d %H:%M:%S')}
        except ValueError:
            return 400, {'ok': False, 'why': 'start is YYYY-MM-DD (all day) or YYYY-MM-DD HH:MM (local time)'}
        data.update({'entity_id': cal, 'summary': summ})
        for f in ('location', 'description'):
            if body.get(f):
                data[f] = str(body[f])[:500]
        st = self.stores[key]
        if not self.is_live(key) or self.ha is None:
            st.log_outbox({'kind': 'calendar_add', 'calendar': cal, 'chars': len(summ), 'mode': 'dry'})
            self.log('persona %s: [DRY] would add an event to %s' % (key, cal))
            return 200, {'ok': True, 'added': False, 'dry': True}
        try:
            self.ha.call_service('calendar', 'create_event', data)
        except Exception as ex:
            self.log('persona %s: calendar add failed (%s)' % (key, type(ex).__name__))
            return 502, {'ok': False, 'why': 'Home Assistant refused (%s)' % type(ex).__name__}
        st.log_outbox({'kind': 'calendar_add', 'calendar': cal, 'chars': len(summ), 'mode': 'live', 'ok': True})
        self.log('persona %s: event added to %s' % (key, cal))
        return 200, {'ok': True, 'added': True, 'dry': False, 'calendar': cal, 'start': data.get('start_date_time') or data.get('start_date')}

    # ---- calendar edit and delete (0.11.0, CalDAV) ----
    def cal_server(self):
        if self._caldav is None:
            o = self.opts
            if not (o.get('caldav_user') and o.get('caldav_password')):
                return None
            self._caldav = (self.caldav_factory or caldavmod.CalDAV)(o['caldav_user'], o['caldav_password'], o.get('caldav_url') or 'https://caldav.icloud.com')
        return self._caldav

    def _cal_target(self, key, body):
        """Shared checks for edit and delete. -> (error tuple | None, server, href, etag, ics, summary)."""
        cal, uid = str(body.get('calendar') or ''), str(body.get('uid') or '').strip()
        if cal not in self.cfg[key].get('calendar_write', []):
            return (403, {'ok': False, 'why': 'this persona may not change %s' % (cal or 'that calendar')}), None, None, None, None, None
        if not re.fullmatch(r'[A-Za-z0-9@._\-]{4,120}', uid):
            return (400, {'ok': False, 'why': 'uid required (from the calendar listing)'}), None, None, None, None, None
        srv = self.cal_server()
        if srv is None:
            return (503, {'ok': False, 'why': 'no calendar login is set (caldav_user / caldav_password)'}), None, None, None, None, None
        try:
            href, etag, ics = srv.find(cal, uid)
        except caldavmod.CalDAVError as e:
            return (404, {'ok': False, 'why': str(e)}), None, None, None, None, None
        summ = caldavmod.ics_unescape(caldavmod.ics_prop(ics, 'SUMMARY'))
        want = re.sub(r'\s+', ' ', str(body.get('summary_was') or '')).strip().lower()
        if not want or want != re.sub(r'\s+', ' ', summ).strip().lower():
            return (409, {'ok': False, 'why': 'name the event exactly as it is now in summary_was', 'current': summ[:200]}), None, None, None, None, None
        return None, srv, href, etag, ics, summ

    def _local_to_utc(self, text):
        t = datetime.datetime.strptime(text, '%Y-%m-%d %H:%M')
        if self.tz:
            t = t.replace(tzinfo=self.tz)
        else:
            t = t.replace(tzinfo=datetime.timezone.utc)
        return t.astimezone(datetime.timezone.utc)

    def calendar_edit(self, key, body):
        """Change ONE non-recurring event. body: calendar, uid, summary_was (its current title, must match), and any of summary, start, end, location, description.
        start / end are 'YYYY-MM-DD' (all day) or 'YYYY-MM-DD HH:MM' local. -> (code, body)."""
        err, srv, href, etag, ics, was = self._cal_target(key, body)
        if err:
            return err
        ch = {}
        try:
            for f in ('summary', 'location', 'description'):
                if f in body:
                    ch[f] = str(body[f])[:500]
            for f, k in (('start', 'dtstart'), ('end', 'dtend')):
                v = str(body.get(f) or '').strip()
                if not v:
                    continue
                ch[k] = ('date', datetime.date.fromisoformat(v)) if re.fullmatch(r'\d{4}-\d\d-\d\d', v) else ('utc', self._local_to_utc(v))
            if not ch:
                return 400, {'ok': False, 'why': 'nothing to change'}
            if 'dtstart' in ch and 'dtend' not in ch:
                return 400, {'ok': False, 'why': 'give end together with start'}
            new = caldavmod.edit_ics(ics, ch, self.now())
        except (ValueError, caldavmod.CalDAVError) as e:
            return 400, {'ok': False, 'why': str(e) if isinstance(e, caldavmod.CalDAVError) else 'start and end are YYYY-MM-DD or YYYY-MM-DD HH:MM'}
        st = self.stores[key]
        if not self.is_live(key):
            st.log_outbox({'kind': 'calendar_edit', 'calendar': body['calendar'], 'mode': 'dry'})
            return 200, {'ok': True, 'changed': False, 'dry': True}
        try:
            srv.put(href, new, etag)
        except caldavmod.CalDAVError as e:
            self.log('persona %s: calendar edit failed (%s)' % (key, e))
            return 502, {'ok': False, 'why': 'calendar server refused (%s)' % e}
        st.log_outbox({'kind': 'calendar_edit', 'calendar': body['calendar'], 'fields': sorted(ch), 'mode': 'live', 'ok': True})
        self.log('persona %s: event changed in %s (%s)' % (key, body['calendar'], ','.join(sorted(ch))))
        return 200, {'ok': True, 'changed': True, 'dry': False, 'fields': sorted(ch)}

    def calendar_delete(self, key, body):
        """Delete ONE non-recurring event. summary_was must match its current title. Not undoable. -> (code, body)."""
        err, srv, href, etag, ics, was = self._cal_target(key, body)
        if err:
            return err
        if caldavmod.is_recurring(ics):
            return 400, {'ok': False, 'why': 'recurring event: delete it in the calendar app'}
        st = self.stores[key]
        if not self.is_live(key):
            st.log_outbox({'kind': 'calendar_delete', 'calendar': body['calendar'], 'mode': 'dry'})
            return 200, {'ok': True, 'deleted': False, 'dry': True}
        try:
            srv.delete(href, etag)
        except caldavmod.CalDAVError as e:
            self.log('persona %s: calendar delete failed (%s)' % (key, e))
            return 502, {'ok': False, 'why': 'calendar server refused (%s)' % e}
        st.log_outbox({'kind': 'calendar_delete', 'calendar': body['calendar'], 'chars': len(was), 'mode': 'live', 'ok': True})
        self.log('persona %s: event deleted from %s' % (key, body['calendar']))
        return 200, {'ok': True, 'deleted': True, 'dry': False}

    # ---- where is everyone (0.12.0) ----
    @staticmethod
    def _haversine_km(a, b):
        import math
        la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
        h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
        return 6371.0 * 2 * math.asin(math.sqrt(h))

    def _loc_signals(self, key, home_ll):
        """Signals for ONE person, best first: GPS (person / device_tracker with a position) before plain home/away switches."""
        c, out = self.cfg[key], []
        ents = list(c.get('location_entities') or []) + ([c['presence_entity']] if c.get('presence_entity') else [])
        for ent in dict.fromkeys(ents):
            try:
                s = self.ha.get('states/' + ent) or {}
            except Exception:
                continue
            st, a = s.get('state'), s.get('attributes') or {}
            if st in (None, 'unknown', 'unavailable'):
                continue
            gps = ent.split('.')[0] in ('person', 'device_tracker') and a.get('latitude') is not None and a.get('longitude') is not None
            sig = {'entity': ent, 'kind': 'gps' if gps else 'presence', 'home': st in ('home', 'on'), 'state': st, 'since': s.get('last_changed')}
            if gps:
                if st not in ('home', 'not_home'):
                    sig['place'] = st
                if home_ll:
                    sig['distance_km'] = round(self._haversine_km(home_ll, (float(a['latitude']), float(a['longitude']))), 1)
                if a.get('gps_accuracy') is not None:
                    sig['accuracy_m'] = int(float(a['gps_accuracy']))
            out.append(sig)
        out.sort(key=lambda x: x['kind'] != 'gps')   # GPS first, then home/away presence as the fallback
        return out

    def where_view(self, key):
        """Where this person is (GPS if available, else home/away), who else is home, and what their calendar says about now. Read only.
        Others' details are limited to home/away. -> (code, body)"""
        if self.ha is None:
            return 503, {'ok': False, 'why': 'no Home Assistant access'}
        home_ll = None
        try:
            z = self.ha.get('states/zone.home') or {}
            home_ll = (float(z['attributes']['latitude']), float(z['attributes']['longitude']))
        except Exception:
            pass
        mine = self._loc_signals(key, home_ll)
        you = {'known': bool(mine)}
        if mine:
            b = mine[0]
            you.update({'home': b['home'], 'source': b['kind'], 'place': b.get('place'), 'distance_km': b.get('distance_km'), 'since': b.get('since'), 'signals': mine})
            if len({s['home'] for s in mine}) > 1:
                you['conflict'] = 'signals disagree: trust GPS if its accuracy is good, otherwise ask'
        others = []
        for k in self.cfg:
            if k == key:
                continue
            sig = self._loc_signals(k, home_ll)
            others.append({'who': self.stores[k].profile().get('person_name') or k, 'home': sig[0]['home'] if sig else None})
        now_ev = []
        try:
            n = self.now()
            for ent in self.cal_readable(key):
                for ev in self.ha.calendar_events(ent, n - datetime.timedelta(minutes=30), n + datetime.timedelta(hours=3)) or []:
                    s = ev.get('start') or {}
                    if 'dateTime' in s:
                        now_ev.append({'summary': str(ev.get('summary') or '')[:120], 'location': str(ev.get('location') or '')[:160], 'start': s.get('dateTime')})
        except Exception as ex:
            self.log('persona %s: where: calendar unreadable (%s)' % (key, type(ex).__name__))
        now_ev.sort(key=lambda e: e['start'])
        return 200, {'ok': True, 'you': you, 'household': others, 'calendar_now': now_ev[:8],
                     'note': 'Compare the calendar location with where they are; a mismatch is a reason to ask, not to assume. Do not tell anyone else where they are beyond home/away.'}

    def trace_reply(self, key, msg_id, reply_chars=None, reply_bytes=None):
        """Close the external Claude bridge and Discord send spans for a replied-to DM."""
        try:
            ev = next((e for e in reversed(self.stores[key].read_inbox(0, 500))
                       if e.get('type') == 'message' and str(e.get('msg_id')) == str(msg_id)), None)
            info = (ev or {}).get('trace') or {}
            tid, root, queued, started = info.get('id'), info.get('root'), info.get('queued'), float(info.get('started'))
            if not tid or not root or not queued:
                return
            now = self.clock()
            elapsed = max(0, min(3600000, int((now - started) * 1000)))
            bridge, sent = secrets.token_hex(8), secrets.token_hex(8)
            a = ihtrace.clean_attrs({'out_bytes': reply_bytes, 'chars': reply_chars}) if reply_chars is not None else {}
            reply_attrs = {'attrs': a} if a else {}
            self.trace_batch(tid, [
                {'span_id': bridge, 'parent_span_id': queued, 'component': 'claude_bridge', 'operation': 'external_processing',
                 'status': 'ok', 'reason': 'reply_received', 'started_at': started, 'duration_ms': elapsed},
                dict({'span_id': sent, 'parent_span_id': bridge, 'component': 'discord', 'operation': 'reply_sent',
                      'status': 'ok', 'reason': '', 'started_at': now, 'duration_ms': 0}, **reply_attrs)
            ])
        except Exception as e:
            self.log('trace correlation failed (%s)' % type(e).__name__)

    def typing(self, key, body=None):
        mid = (body or {}).get('msg_id')
        if mid is not None:
            if not MARK_RE.match(str(mid)):
                return 400, {'ok': False, 'why': 'msg_id must be a message id'}
            mid, now = str(mid), self.clock()
            def add(s):
                ks = [k for k in s.get('keepers', []) if k['msg_id'] != mid][-4:]
                ks.append({'msg_id': mid, 'start': now}); s['keepers'] = ks
            self.stores[key].update_state(add)
            if not self.is_live(key):
                self.log('persona %s: [DRY] would keep typing up to %d s' % (key, TYPING_MAX_S))
            return 200, {'ok': True, 'keeping': True, 'dry': not self.is_live(key)}
        if not self.is_live(key) or key not in self.discord:
            self.log('persona %s: [DRY] would show typing' % key)
            return 200, {'ok': True, 'sent': False, 'dry': True}
        try:
            self.discord[key].typing(self._channel(key))
        except DiscordError as e:
            return 502, {'ok': False, 'why': 'discord: %s' % e}
        return 200, {'ok': True, 'sent': True, 'dry': False}

    def ask(self, key, body):
        """Post a question as the persona with reaction buttons (yes / no / unsure). The user's reactions come back as 'reaction' events."""
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
            return 400, {'ok': False, 'why': 'text required (1..%d characters)' % TEXT_MAX}
        opts = body.get('options')
        if opts is None:
            opts = list(ASK_EMOJI)
        if not isinstance(opts, list) or not opts or any(o not in ASK_EMOJI for o in opts):
            return 400, {'ok': False, 'why': 'options: a list of yes, no, unsure'}
        st = self.stores[key]
        if not self.is_live(key):
            st.log_outbox({'kind': 'ask', 'chars': len(text), 'mode': 'dry', 'options': len(opts)})
            self._count_out(key, 'reply')
            self.log('persona %s: [DRY] would ask %d chars with %d reactions' % (key, len(text), len(opts)))
            return 200, {'ok': True, 'sent': False, 'dry': True, 'chars': len(text)}
        if key not in self.discord:
            return 409, {'ok': False, 'why': 'no bot configured'}
        d = self.discord[key]
        try:
            ch = self._channel(key)
            r = d.call('POST', '/channels/%s/messages' % ch, {'content': text[:1900]})
            mid = str(r['id'])
            for o in opts:
                d.react(ch, mid, ASK_EMOJI[o])
        except (DiscordError, KeyError, TypeError) as e:
            self.log('persona %s: ask failed (%s)' % (key, type(e).__name__))
            return 502, {'ok': False, 'why': 'discord error'}
        now = self.clock()
        def add(s):
            a = [x for x in s.get('asks', []) if now - x['ts'] < ASK_KEEP_S][-(ASK_MAX - 1):]
            a.append({'msg_id': mid, 'ts': now}); s['asks'] = a
            s.pop('keepers', None)
        st.update_state(add)
        st.log_outbox({'kind': 'ask', 'chars': len(text), 'mode': 'live', 'options': len(opts)})
        self._count_out(key, 'reply')
        self.log('persona %s: asked, %d chars' % (key, len(text)))
        return 200, {'ok': True, 'sent': True, 'dry': False, 'msg_id': mid}

    def watch_sent(self, key):
        """0.13.1: remember the message the persona's bot just sent so the person's emoji reactions on it become 'reaction' events."""
        mid = getattr(self.discord.get(key), 'last_id', None)
        if not mid:
            return
        now = self.clock()
        def add(s):
            w = [x for x in s.get('watch', []) if now - x['ts'] < WATCH_KEEP_S][-(WATCH_MAX - 1):]
            w.append({'msg_id': str(mid), 'ts': now}); s['watch'] = w
        self.stores[key].update_state(add)

    def tick_reactions(self, key):
        """Look at the reactions of the person on recent asks; new ones (and re-added ones) become 'reaction' events. -> number added."""
        if key not in self.discord:
            return 0
        st, d, uid, now = self.stores[key], self.discord[key], self.cfg[key]['user_id'], self.clock()
        s0 = st.state()
        asks = [a for a in s0.get('asks', []) if now - a['ts'] < ASK_KEEP_S]
        asks += [a for a in s0.get('watch', []) if now - a['ts'] < WATCH_KEEP_S and a['msg_id'] not in {x['msg_id'] for x in asks}]
        if not asks or now - s0.get('react_at', 0) < self.react_every(key):
            return 0
        st.update_state(lambda s: s.__setitem__('react_at', now))
        ch = self._channel(key)
        seen, cur, added = set(s0.get('react_seen', [])), set(), 0
        for a in asks:
            m = d.call('GET', '/channels/%s/messages/%s' % (ch, a['msg_id']))
            for r in (m or {}).get('reactions', []):
                if r.get('count', 1) - (1 if r.get('me') else 0) <= 0:
                    continue
                e = r['emoji']
                name = e['name'] + (':' + e['id'] if e.get('id') else '')
                users = d.call('GET', '/channels/%s/messages/%s/reactions/%s?limit=10' % (ch, a['msg_id'], urllib.parse.quote(name))) or []
                if any(str(u.get('id')) == uid for u in users):
                    k = '%s:%s' % (a['msg_id'], name)
                    cur.add(k)
                    if k not in seen:
                        st.append_event({'type': 'reaction', 'msg_id': a['msg_id'], 'emoji': e['name']}); added += 1
        st.update_state(lambda s: s.__setitem__('react_seen', sorted(cur)[-100:]))
        return added

    def tick_typing(self, key):
        """Typing keepers: show typing every tick until the persona has posted after msg_id, or TYPING_MAX_S passes (then a still_waiting event)."""
        st = self.stores[key]
        ks = st.state().get('keepers', [])
        if not ks:
            return 0
        now, live, keep, done = self.clock(), self.is_live(key) and key in self.discord, [], 0
        for k in ks:
            if live:
                ch = self._channel(key)
                if any(str(m.get('author', {}).get('id')) != self.cfg[key]['user_id'] for m in self.discord[key].messages_after(ch, k['msg_id'], 20)):
                    continue   # the persona (or its bot) has answered: stop
            if now - k['start'] >= TYPING_MAX_S:
                st.append_event({'type': 'still_waiting', 'msg_id': k['msg_id']}); done += 1
                continue
            if live:
                self.discord[key].typing(self._channel(key))
            keep.append(k)
        st.update_state(lambda s: s.__setitem__('keepers', [k for k in s.get('keepers', []) if k in keep]))
        return done

    def tell(self, frm, to, body):
        """Persona-to-persona message: the ONLY cross-persona write. It hands one event to the target's own store and reads nothing of it."""
        if frm not in self.stores or to not in self.stores:
            return 404, {'ok': False, 'why': 'unknown persona'}
        if frm == to:
            return 400, {'ok': False, 'why': 'cannot tell yourself'}
        if to not in self.cfg[frm]['can_tell']:
            return 403, {'ok': False, 'why': 'not allowed (can_tell)'}
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > TELL_MAX:
            return 400, {'ok': False, 'why': 'text required (1..%d characters)' % TELL_MAX}
        day = self._day()
        def take(s):
            t = s.setdefault('tells', {})
            if t.get('day') != day:
                t.clear(); t['day'] = day
            if int(t.get(to, 0)) >= TELL_PER_DAY:
                return False
            t[to] = int(t.get(to, 0)) + 1
            return True
        if not self.stores[frm].update_state(take):
            return 429, {'ok': False, 'why': 'limit of %d a day for this pair' % TELL_PER_DAY}
        ev = {'type': 'from_persona', 'from': frm, 'text': text}
        if isinstance(body.get('kind'), str) and re.match(r'^[a-z_]{1,20}$', body['kind']):
            ev['kind'] = body['kind']
        stored = self.stores[to].append_event(ev)
        self.stores[frm].log_outbox({'kind': 'tell', 'to': to, 'chars': len(text)})
        self.log('persona %s: tell to %s, %d chars' % (frm, to, len(text)))
        return 200, {'ok': True, 'id': stored['id']}

    def event(self, key, body):
        """An add-on (e.g. ih_games) hands this persona one event for its mind to act on. Lands ONLY in this persona's own inbox. Logs type and lengths only."""
        if key not in self.stores:
            return 404, {'ok': False, 'why': 'unknown persona'}
        typ, src, facts, sug = body.get('type'), body.get('source'), body.get('facts'), body.get('suggested_text')
        if typ not in EVENT_TYPES:
            return 400, {'ok': False, 'why': 'type must be one of ' + ', '.join(EVENT_TYPES)}
        if not isinstance(src, str) or not src.strip() or len(src) > EVENT_SOURCE_LEN:
            return 400, {'ok': False, 'why': 'source: text 1..%d characters' % EVENT_SOURCE_LEN}
        if not isinstance(facts, dict) or len(facts) > EVENT_FACTS_MAX:
            return 400, {'ok': False, 'why': 'facts: object with up to %d keys' % EVENT_FACTS_MAX}
        for k, v in facts.items():
            if not isinstance(k, str) or not k or len(k) > 60 or not (v is None or isinstance(v, (str, int, float, bool))):
                return 400, {'ok': False, 'why': 'facts: flat, values text/number/true-false'}
            if isinstance(v, str) and len(v) > EVENT_FACT_LEN:
                return 400, {'ok': False, 'why': 'facts: each text up to %d characters' % EVENT_FACT_LEN}
        if sug is not None and (not isinstance(sug, str) or len(sug) > EVENT_TEXT_LEN):
            return 400, {'ok': False, 'why': 'suggested_text: up to %d characters' % EVENT_TEXT_LEN}
        day = self._day()
        def take(s):
            t = s.setdefault('events_in', {})
            if t.get('day') != day:
                t.clear(); t['day'] = day
            if int(t.get(src, 0)) >= EVENT_PER_DAY:
                return False
            t[src] = int(t.get(src, 0)) + 1
            return True
        if not self.stores[key].update_state(take):
            return 429, {'ok': False, 'why': 'limit of %d a day for this source' % EVENT_PER_DAY}
        stored = self.stores[key].append_event({'type': typ, 'source': src, 'facts': facts, 'suggested_text': sug})
        self.log('persona %s: event %s from %s, %d facts, %d chars' % (key, typ, src[:EVENT_SOURCE_LEN], len(facts), len(sug or '')))
        return 200, {'ok': True, 'id': stored['id']}

    def identity(self, key, body):
        """Change the persona's own username / avatar / server nickname. A username change is limited to 2 an hour (Discord's rule), counted per persona."""
        un, av, nick = body.get('username'), body.get('avatar'), body.get('nickname')
        if un is None and av is None and nick is None:
            return 400, {'ok': False, 'why': 'give username, avatar or nickname'}
        if un is not None:
            un = un.strip() if isinstance(un, str) else None
            if not un or not 2 <= len(un) <= 32 or any(b in un.lower() for b in BAD_NAME):
                return 400, {'ok': False, 'why': "username: 2..32 characters, without 'discord', '@', '#', ':' or ```"}
        raw = None
        if av is not None:
            try:
                if not isinstance(av, str):
                    raise ValueError
                raw = base64.b64decode(av.split(',', 1)[1] if av.startswith('data:') else av, validate=True)
            except (ValueError, binascii.Error, IndexError):
                return 400, {'ok': False, 'why': 'avatar: base64 PNG or JPEG data'}
            if len(raw) > AVATAR_MAX:
                return 400, {'ok': False, 'why': 'avatar: up to 1 MiB'}
            if raw[:8] == b'\x89PNG\r\n\x1a\n':
                mime = 'image/png'
            elif raw[:3] == b'\xff\xd8\xff':
                mime = 'image/jpeg'
            else:
                return 400, {'ok': False, 'why': 'avatar: must be a PNG or JPEG'}
        if nick is not None:
            nick = nick.strip() if isinstance(nick, str) else None
            if nick is None or len(nick) > 32 or any(b in nick.lower() for b in BAD_NAME):
                return 400, {'ok': False, 'why': "nickname: up to 32 characters (empty clears it), without 'discord', '@', '#', ':' or ```"}
        st, now = self.stores[key], self.clock()
        recent = [t for t in st.state().get('name_changes', []) if now - t < NAME_WINDOW_S]
        if un is not None and len(recent) >= NAME_CHANGES_PER_HOUR:
            wait = int(NAME_WINDOW_S - (now - min(recent))) + 1
            return 429, {'ok': False, 'why': 'username changes are limited to %d an hour' % NAME_CHANGES_PER_HOUR, 'retry_after': wait}
        if not self.is_live(key):
            st.log_outbox({'kind': 'identity', 'mode': 'dry', 'username': len(un) if un else 0, 'avatar': len(raw) if raw else 0, 'nickname': len(nick) if nick is not None else -1})
            self.log('persona %s: [DRY] would set username (%d chars) / avatar (%d bytes) / nickname (%d chars)' % (key, len(un or ''), len(raw or b''), len(nick or '')))
            return 200, {'ok': True, 'dry': True, 'changed': []}
        if key not in self.discord:
            return 409, {'ok': False, 'why': 'no bot configured'}
        d, changed = self.discord[key], []
        try:
            if un is not None or raw is not None:
                patch = {}
                if un is not None:
                    patch['username'] = un
                if raw is not None:
                    patch['avatar'] = 'data:%s;base64,%s' % (mime, base64.b64encode(raw).decode())
                d.call('PATCH', '/users/@me', patch)
                if un is not None:
                    st.update_state(lambda s: s.__setitem__('name_changes', recent + [now]))
                    st.merge_profile({'name': un})
                    changed.append('username')
                if raw is not None:
                    changed.append('avatar')
            if nick is not None:
                n = 0
                for g in d.call('GET', '/users/@me/guilds') or []:
                    d.call('PATCH', '/guilds/%s/members/@me' % g['id'], {'nick': nick or None}); n += 1
                changed.append('nickname (%d servers)' % n)
        except (DiscordError, KeyError, TypeError) as e:
            self.log('persona %s: identity failed (%s)' % (key, type(e).__name__))
            return 502, {'ok': False, 'why': 'discord error', 'changed': changed}
        st.log_outbox({'kind': 'identity', 'mode': 'live', 'changed': changed})
        self.log('persona %s: identity changed (%s)' % (key, ', '.join(changed)))
        return 200, {'ok': True, 'dry': False, 'changed': changed}

    def attachment(self, key, msg_id, n):
        """Bytes of attachment n of the person's own private message msg_id, fetched fresh (Discord CDN links expire). -> (code, bytes|dict, content type)."""
        if key not in self.discord:
            return 409, {'ok': False, 'why': 'no bot configured'}, None
        if not (msg_id.isdigit() and n.isdigit()):
            return 400, {'ok': False, 'why': 'message id and file number are numbers'}, None
        d = self.discord[key]
        try:
            m = d.call('GET', '/channels/%s/messages/%s' % (self._channel(key), msg_id)) or {}
        except DiscordError as e:
            return 502, {'ok': False, 'why': 'discord error (%s)' % e}, None
        if str(m.get('author', {}).get('id')) != self.cfg[key]['user_id']:
            return 404, {'ok': False, 'why': 'not a message from the person'}, None
        att = (m.get('attachments') or [])
        if int(n) >= min(len(att), ATTACH_MAX_N):
            return 404, {'ok': False, 'why': 'no such file'}, None
        a = att[int(n)]
        url = str(a.get('url') or '')
        if urlparse(url).scheme != 'https' or urlparse(url).hostname not in ATTACH_HOSTS:
            return 502, {'ok': False, 'why': 'unexpected file host'}, None
        if int(a.get('size') or 0) > ATTACH_MAX_BYTES:
            return 413, {'ok': False, 'why': 'file too large'}, None
        try:
            with d.opener(urllib.request.Request(url, headers={'User-Agent': 'IntelligentHomeAssistant (local, 1.0)'}), timeout=30) as r:
                data = r.read(ATTACH_MAX_BYTES + 1)
        except (urllib.error.URLError, OSError) as e:
            return 502, {'ok': False, 'why': 'download failed (%s)' % type(e).__name__}, None
        if len(data) > ATTACH_MAX_BYTES:
            return 413, {'ok': False, 'why': 'file too large'}, None
        self.log('persona %s: attachment %s/%s fetched (%d bytes)' % (key, msg_id, n, len(data)))
        return 200, data, str(a.get('content_type') or 'application/octet-stream')

    def status(self):
        out = {}
        for k, c in self.cfg.items():
            s = self.stores[k]
            out[k] = {'live': self.is_live(k), 'bot': k in self.discord, 'inbox': s.inbox_count(), 'outbox': s.outbox_count(),
                      'out_today': self.out_today(k), 'checkins': len(c['checkins']), 'introduced': s.profile()['introduced'],
                      'can_tell': len(c['can_tell']), 'plan': c['checkin_plan'], 'levels': dict(c['levels']), 'push': self.gateways[k].state if k in self.gateways else gatewaymod.STATE_OFF}
        return {'ok': True, 'version': VERSION, 'mode': self.opts['mode'], 'token_set': bool(self.opts['api_token']), 'personas': out}


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ct='application/json'):
            b = (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode()
            self.send_response(code); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(b)

        def _route(self, method):
            u = urlparse(self.path)
            parts = [p for p in u.path.split('/') if p]
            if method == 'GET' and parts == ['healthz']:
                return self._send(200, 'ok', 'text/plain')
            if not app.authed(self.headers.get('Authorization')):
                return self._send(401, {'ok': False, 'why': 'bearer token required'})
            if os.path.exists(os.path.join(os.environ.get('IHP_DATA', '/data'), 'STOP')):
                return self._send(503, {'ok': False, 'why': 'STOP file present'})
            if method == 'GET' and parts == ['status']:
                return self._send(200, app.status())
            if len(parts) < 3 or parts[0] != 'persona' or parts[1] not in app.stores:
                return self._send(404, {'ok': False})
            key, act = parts[1], parts[2]
            if method == 'GET' and act == 'inbox' and len(parts) == 3:
                q = parse_qs(u.query)
                try:
                    after = int(q.get('after', ['0'])[0]); limit = int(q.get('limit', ['100'])[0])
                except ValueError:
                    return self._send(400, {'ok': False, 'why': 'after and limit are numbers'})
                return self._send(200, {'ok': True, 'events': app.stores[key].read_inbox(after, limit)})
            if method == 'GET' and act == 'plan' and len(parts) == 3:
                return self._send(200, app.plan_view(key))
            if method == 'GET' and act == 'reminders' and len(parts) == 3:
                return self._send(*app.remind_list(key))
            if method == 'GET' and act == 'attachment' and len(parts) == 5:
                code, body, ct = app.attachment(key, parts[3], parts[4])
                if code != 200:
                    return self._send(code, body)
                self.send_response(200); self.send_header('Content-Type', ct); self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)
                return
            if method == 'GET' and act == 'calendar' and len(parts) == 3:
                q = parse_qs(u.query)
                return self._send(*app.calendar_view(key, (q.get('days') or ['14'])[0], (q.get('start') or [None])[0]))
            if method == 'GET' and act == 'where' and len(parts) == 3:
                return self._send(*app.where_view(key))
            if method == 'GET' and act == 'behaviour' and len(parts) == 3:
                return self._send(200, app.behaviour(key))
            if method == 'GET' and act == 'profile' and len(parts) == 3:
                return self._send(200, {'ok': True, 'profile': app.stores[key].profile()})
            if method != 'POST':
                return self._send(404, {'ok': False})
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413, {'ok': False, 'why': 'body too large'})
            try:
                body = json.loads(self.rfile.read(n) or b'{}')
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {'ok': False, 'why': 'JSON object required'})
            if act == 'reply' and len(parts) == 3:
                return self._send(*app.reply(key, body))
            if act == 'complete' and len(parts) == 3:
                return self._send(*app.complete_message(key, body))
            if act == 'calendar' and len(parts) == 4 and parts[3] == 'add':
                return self._send(*app.calendar_add(key, body))
            if act == 'calendar' and len(parts) == 4 and parts[3] == 'edit':
                return self._send(*app.calendar_edit(key, body))
            if act == 'calendar' and len(parts) == 4 and parts[3] == 'delete':
                return self._send(*app.calendar_delete(key, body))
            if act == 'typing' and len(parts) == 3:
                return self._send(*app.typing(key, body))
            if act == 'ask' and len(parts) == 3:
                return self._send(*app.ask(key, body))
            if act == 'identity' and len(parts) == 3:
                return self._send(*app.identity(key, body))
            if act == 'event' and len(parts) == 3:
                return self._send(*app.event(key, body))
            if act == 'remind' and len(parts) == 3:
                return self._send(*app.remind_add(key, body))
            if act == 'remind' and len(parts) == 4 and parts[3] == 'cancel':
                return self._send(*app.remind_cancel(key, body))
            if act == 'agree' and len(parts) == 3:
                return self._send(*app.agree(key, body))
            if act == 'agree' and len(parts) == 4 and parts[3] == 'remove':
                return self._send(*app.unagree(key, body))
            if act == 'tell' and len(parts) == 4:
                return self._send(*app.tell(key, parts[3], body))
            if act == 'profile' and len(parts) == 3:
                try:
                    prof, ign = app.stores[key].merge_profile(body)
                except StoreError as e:
                    return self._send(400, {'ok': False, 'why': str(e)})
                return self._send(200, {'ok': True, 'profile': prof, 'ignored': ign})
            self._send(404, {'ok': False})

        def do_GET(self):
            self._route('GET')

        def do_POST(self):
            self._route('POST')

        def log_message(self, *a):
            pass
    return H


def worker(app, key):
    while True:
        try:
            app.tick(key)
        except Exception as e:
            app.log('persona %s: tick error %s' % (key, type(e).__name__))
        app.wait_tick(key)


def main():
    opts, problems = load_options(os.environ.get('IHP_OPTIONS', '/data/options.json'))
    secrets = [opts['api_token'], opts['facts_key'], opts.get('giphy_api_key', ''), opts.get('caldav_password', '')] + [p['bot_token'] for p in opts['personas']] + [p['user_id'] for p in opts['personas']]
    lg = logging.getLogger('ih_assistant'); lg.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.__stdout__); sh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')); lg.addHandler(sh)
    log = ihlog.Logger(lg.info, [s for s in secrets if len(s) >= 4])
    tz = None
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(opts['timezone'])
    except Exception:
        log('timezone %s not available: using UTC' % opts['timezone'])
    for p in problems:
        log('option problem: ' + p)
    app = Assistant(opts, os.environ.get('IHP_DATA', '/data'), log, tz=tz, ha=ihha.HA())
    log('ih_assistant %s starting: mode %s, %d personas (%s), token %s' % (
        VERSION, opts['mode'], len(opts['personas']), ', '.join('%s=%s' % (k, 'live' if app.is_live(k) else 'dry') for k in app.cfg),
        'set' if opts['api_token'] else 'NOT set (all requests refused)'))
    for k in app.cfg:
        threading.Thread(target=worker, args=(app, k), daemon=True).start()
    app.start_gateways()
    def on_term(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, on_term)   # Supervisor stop: leave serve_forever so the gateways close cleanly
    try:
        ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('IHP_PORT', '8830'))), make_handler(app)).serve_forever()
    finally:
        app.stop_gateways()


if __name__ == '__main__':
    main()
