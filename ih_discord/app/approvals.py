"""Approvals channel (port of the earlier host script, CULL row 17). Posts an approval or a manual-step embed in the private approvals channel,
adds the tick / cross / question reactions, and reads the answer back. The state lives in /data/approvals_open.json.
Ops (all POST /approvals/<op>, bearer admin_token, JSON body): ask, step, check, list, delete.
  ask/step {title, detail, id?, overnight?} -> {n}; check {n?} -> {results: [{n, title, kind, answer, label}]}; list {} ; delete {n}.
`wait` is the CLI's job: it calls check every 20 s (the add-on never blocks a request).
An answered overnight approval is reported once with `run_overnight: true`: the host shim runs overnight/approve.py N [no], then calls `hooked`.
DRY by default: ask/step/delete send nothing to Discord unless identity_live is on and the kill switch file is absent. Reading reactions is always allowed.
Tokens are never returned or logged. stdlib only."""
import json, os, threading, time, urllib.error, urllib.parse, urllib.request

API = 'https://discord.com/api/v10'
UA = 'DiscordBot (ih-discord-approvals, 1)'
YES, NO, QUE = ('✅', '👍'), ('❌', '👎'), ('❓', '❔')


def parse_answer(yes, no, que=False):
    """yes / no = whether a human tapped the tick / cross. Both or neither = open."""
    if que: return 'question'
    return 'yes' if yes and not no else 'no' if no and not yes else 'open'


def label(kind, a):
    return {'yes': 'done', 'no': 'skipped'}.get(a, a) if kind == 'step' else a


class Approvals:
    def __init__(self, token, channel, store, live_check, opener=None, sleep=time.sleep):
        """channel: the approvals channel id (a number). live_check() -> bool."""
        self.token, self.channel, self.store = token, str(channel or ''), store
        self.live_check, self.opener, self.sleep = live_check, opener or urllib.request.urlopen, sleep
        self.lock = threading.Lock()

    def ready(self): return bool(self.token and self.channel.isdigit())

    def _call(self, method, path, body=None):
        r = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                   headers={'Authorization': 'Bot ' + self.token, 'Content-Type': 'application/json', 'User-Agent': UA})
        with self.opener(r, timeout=15) as x:
            raw = x.read()
            return json.loads(raw) if raw else None

    def _load(self):
        try: return json.load(open(self.store))
        except (OSError, ValueError): return {}

    def _save(self, s):
        tmp = self.store + '.tmp'
        json.dump(s, open(tmp, 'w'), indent=1); os.replace(tmp, self.store)

    def run(self, op, body):
        """-> (http status, dict). Never includes a token."""
        body = body if isinstance(body, dict) else {}
        fn = {'ask': lambda b: self.ask(b, 'approval'), 'step': lambda b: self.ask(b, 'step'), 'check': self.check, 'list': self.check,
              'delete': self.delete, 'hooked': self.hooked}.get(op)
        if not fn: return 404, {'ok': False, 'why': 'unknown op'}
        if not self.ready(): return 503, {'ok': False, 'why': 'approvals channel (extra_channels key approvals) or house token not set'}
        with self.lock:
            return fn(body)

    @staticmethod
    def _num(v):
        v = str(v if v is not None else '').strip().lstrip('#')
        return v if v.isdigit() else ''

    def ask(self, b, kind):
        title, detail = str(b.get('title') or '').strip()[:200], str(b.get('detail') or '')[:3500]
        if not title: return 422, {'ok': False, 'why': 'title needed'}
        s = self._load()
        n = self._num(b.get('id')) or str(max([int(k) for k in s] + [0]) + 1)
        if kind == 'step':
            emb = {'title': 'Your step #%s: %s' % (n, title), 'description': detail, 'color': 0x3498DB,
                   'footer': {'text': 'Tap ✅ when it is done. Tap ❌ if you cannot do it. Tap ❓ if you need more detail.'}}
        else:
            emb = {'title': 'Approval #%s: %s' % (n, title), 'description': detail, 'color': 0xF5A623,
                   'footer': {'text': 'Tap ✅ yes. Tap ❌ no. Tap ❓ if you need more detail. A reply in chat also works.'}}
        if not self.live_check():
            return 200, {'ok': True, 'dry': True, 'n': n, 'would': 'post %s #%s %r' % (kind, n, title)}
        msg = self._call('POST', '/channels/%s/messages' % self.channel, {'embeds': [emb]})
        for e in ('✅', '❌', '❓'):
            self._call('PUT', '/channels/%s/messages/%s/reactions/%s/@me' % (self.channel, msg['id'], urllib.parse.quote(e))); self.sleep(0.4)
        s[n] = {'msg': msg['id'], 'title': title, 'overnight': bool(b.get('overnight')), 'answer': None, 'kind': kind}
        self._save(s)
        return 200, {'ok': True, 'dry': False, 'n': n}

    def _users(self, mid, emoji):
        for _ in range(4):   # Discord answers 429 when many requests are checked in a row: wait and retry, never read 429 as "no tick"
            try:
                self.sleep(0.3)
                return [u for u in self._call('GET', '/channels/%s/messages/%s/reactions/%s?limit=10' % (self.channel, mid, urllib.parse.quote(emoji))) or [] if not u.get('bot')]
            except urllib.error.HTTPError as e:
                if e.code != 429: return []
                self.sleep(2)
            except Exception: return []
        return []

    def _status(self, n, s):
        r = s[n]
        if r['answer']: return r['answer']
        yes = any(self._users(r['msg'], e) for e in YES); no = any(self._users(r['msg'], e) for e in NO)
        a = parse_answer(yes, no, any(self._users(r['msg'], e) for e in QUE))
        if a != 'open':
            r['answer'] = a
            if a != 'question' and r.get('overnight') and r.get('kind', 'approval') != 'step': r['hook_pending'] = True
            self._save(s)
        return a

    def check(self, b):
        s = self._load(); want = self._num(b.get('n'))
        ns = [want] if want else sorted(s, key=int)
        out = []
        for n in ns:
            if n not in s: continue
            a = self._status(n, s); r = s[n]; k = r.get('kind', 'approval')
            out.append({'n': n, 'title': r['title'], 'kind': k, 'answer': a, 'label': label(k, a), 'overnight': bool(r.get('overnight')),
                        'run_overnight': bool(r.get('hook_pending')), 'approved': a == 'yes'})
        return 200, {'ok': True, 'results': out, 'open': [x['n'] for x in out if x['answer'] == 'open']}

    def hooked(self, b):
        n = self._num(b.get('n')); s = self._load()
        if n in s and s[n].pop('hook_pending', None): self._save(s)
        return 200, {'ok': True}

    def delete(self, b):
        n = self._num(b.get('n')); s = self._load()
        if n not in s: return 404, {'ok': False, 'why': 'no such request'}
        if not self.live_check(): return 200, {'ok': True, 'dry': True, 'would': 'delete #%s' % n}
        try: self._call('DELETE', '/channels/%s/messages/%s' % (self.channel, s[n]['msg']))
        except urllib.error.HTTPError as e:
            if e.code != 404: return 502, {'ok': False, 'status': e.code}   # 404 = already gone: still forget it
        del s[n]; self._save(s)
        return 200, {'ok': True, 'dry': False, 'deleted': n}
