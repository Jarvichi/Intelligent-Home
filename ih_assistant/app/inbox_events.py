"""Posts Discord direct messages and emoji reactions to the orchestration inbox add-on (POST /events). stdlib only.
The same file lives in ih_discord and ih_assistant (a repo test keeps the two copies identical).
It replaces the host scripts dm_watch.py and react_watch.py: same lines of text, same "only this person" rule, bots ignored.
SHADOW (the default): nothing is sent, the would-be events are logged without their text. LIVE: sent; a failed send is retried
next round because the position only moves after the inbox stored (or already had) the event. The event ref is the Discord message
id (plus the emoji for reactions), so a repeat is stored once. Persisted position: <data>/inbox_events.json."""
import json, os, re, threading, time, urllib.error, urllib.parse, urllib.request

PATH_OK = re.compile(r'[^A-Za-z0-9_.:-]')
SEEN_KEEP = 200
EVENT_TEXT_MAX = 1900


def safe_ref(*parts):
    """A ref the inbox accepts (letters, digits and _ . : -): other characters (emoji) become hex."""
    ref = '-'.join(PATH_OK.sub(lambda m: 'u%x' % ord(m.group(0)), str(p)) for p in parts)
    return ref[:80]


def dm_line(m):
    """The line dm_watch.py printed: 'HH:MM UTC: text url url'."""
    ts = str(m.get('timestamp') or '')[11:16]
    return ' '.join([ts + ' UTC:', m.get('content') or '(attachment/sticker)'] + [a.get('url', '') for a in m.get('attachments', [])])[:EVENT_TEXT_MAX]


def reaction_line(emoji_name, mid, content):
    """The line react_watch.py printed: '<emoji> on message <id>: <first 120 chars>'."""
    return '%s on message %s: %s' % (emoji_name, mid, str(content or '')[:120].replace('\n', ' '))


class InboxEvents:
    def __init__(self, url, token, live, data_dir, log=print, opener=None, clock=time.time):
        self.url, self.token, self.live = str(url or '').rstrip('/'), str(token or ''), bool(live)
        self.path = os.path.join(data_dir, 'inbox_events.json')
        self.log, self.opener, self.clock = log, opener or urllib.request.urlopen, clock
        self.lock = threading.Lock()
        self.sent = self.would = self.failed = 0
        self.last_ok = self.last_error = None

    def ready(self):
        return bool(self.url and (self.token or not self.live))

    # ---- state ----
    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, d):
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    def _get(self, name):
        return self._load().get(name)

    def _set(self, name, value):
        with self.lock:
            d = self._load(); d[name] = value; self._save(d)

    # ---- posting ----
    def post(self, kind, source, ref, text):
        """-> True when the event is handled (sent, already stored, or shadow); False when it must be retried."""
        if not self.live:
            self.would += 1
            self.log('inbox_events: [SHADOW] would post %s from %s (%s)' % (kind, source, ref))
            return True
        body = json.dumps({'kind': kind, 'source': source, 'ref': ref, 'text': text[:EVENT_TEXT_MAX]}).encode()
        req = urllib.request.Request(self.url + '/events', data=body, method='POST',
                                     headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener(req, timeout=10) as r:
                ok = 200 <= r.status < 300
        except (urllib.error.URLError, OSError, ValueError) as e:
            ok, self.last_error = False, type(e).__name__
        if ok:
            self.sent += 1; self.last_ok = self.clock()
        else:
            self.failed += 1
            self.log('inbox_events: post of %s failed (%s), will retry' % (kind, self.last_error))
        return ok

    # ---- direct messages from one person ----
    def step_dm(self, name, get, channel, user_id, source):
        """New messages of user_id in a DM channel. get(path) -> parsed JSON (raises on error). -> number handled.
        First run: start at the newest message (history is not replayed)."""
        last = self._get('dm_' + name)
        if last is None:
            m = get('/channels/%s/messages?limit=1' % channel)
            self._set('dm_' + name, m[0]['id'] if m else '0')
            return 0
        msgs = sorted(get('/channels/%s/messages?after=%s&limit=20' % (channel, last)) or [], key=lambda m: int(m['id']))
        done = 0
        for m in msgs:
            a = m.get('author') or {}
            if str(a.get('id')) == str(user_id) and not a.get('bot'):
                if not self.post('dm', source, safe_ref('dm', m['id']), dm_line(m)):
                    break
                done += 1
            last = m['id']
            self._set('dm_' + name, last)
        return done

    # ---- reactions of the given people on messages the bot wrote ----
    def step_reactions(self, name, get, channel, user_ids, source):
        """New reactions by user_ids on the bot's own recent messages in a channel. -> number handled."""
        people = {str(u) for u in user_ids if u}
        found = []
        for m in get('/channels/%s/messages?limit=25' % channel) or []:
            if not (m.get('author') or {}).get('bot'):
                continue
            for r in m.get('reactions', []):
                if r.get('count', 1) - (1 if r.get('me') else 0) <= 0:
                    continue   # only the bot's own reaction: no extra request
                e = r['emoji']
                en = e['name'] + (':' + e['id'] if e.get('id') else '')
                users = get('/channels/%s/messages/%s/reactions/%s?limit=10' % (channel, m['id'], urllib.parse.quote(en))) or []
                if any(str(u.get('id')) in people and not u.get('bot') for u in users):
                    found.append(('%s:%s' % (m['id'], en), e['name'], m['id'], m.get('content')))
        key = 'react_' + name
        seen = self._get(key)
        if seen is None:   # first run: old reactions are not replayed
            self._set(key, sorted({f[0] for f in found})[-SEEN_KEEP:])
            return 0
        seen, done = set(seen), 0
        for k, em, mid, content in found:
            if k in seen:
                continue
            if not self.post('reaction', source, safe_ref(mid, em), reaction_line(em, mid, content)):
                continue
            seen.add(k); done += 1
        self._set(key, sorted(seen)[-SEEN_KEEP:])
        return done

    def status(self):
        return {'mode': 'live' if self.live else 'shadow', 'sent': self.sent, 'would': self.would, 'failed': self.failed,
                'last_ok_s': None if self.last_ok is None else int(self.clock() - self.last_ok)}
