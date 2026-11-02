"""PersonaStore: the ONLY way to touch a persona's files. One store = one folder /data/personas/<key>/.
No method takes a path. File names are fixed. The key is validated, the folder must resolve (realpath) inside the personas folder,
symlinks are refused, files are mode 0600 and written atomically. Nothing here can see another persona's folder."""
import json, os, re, secrets, threading, time

KEY_RE = re.compile(r'^[a-z][a-z0-9_]{1,20}$')
INBOX_KEEP = 500
OUTBOX_KEEP = 1000
DEFAULT_PROFILE = {'name': None, 'person_name': None, 'tone': None, 'notes': [], 'seeded_from': None, 'introduced': False}
PROFILE_LIMITS = {'name': 60, 'person_name': 60, 'tone': 300, 'seeded_from': 60}
NOTES_MAX, NOTE_LEN = 20, 300
FILES = {'profile': 'profile.json', 'state': 'state.json', 'inbox': 'inbox.jsonl', 'outbox': 'outbox.jsonl'}


class StoreError(ValueError):
    pass


def valid_key(key):
    return isinstance(key, str) and bool(KEY_RE.match(key))


class PersonaStore:
    def __init__(self, data_dir, key, clock=time.time):
        if not valid_key(key):
            raise StoreError('bad persona key')
        base = os.path.realpath(os.path.join(data_dir, 'personas'))
        os.makedirs(base, mode=0o700, exist_ok=True)
        folder = os.path.join(base, key)
        if os.path.islink(folder):
            raise StoreError('persona folder is a link')
        os.makedirs(folder, mode=0o700, exist_ok=True)
        real = os.path.realpath(folder)
        if os.path.dirname(real) != base or os.path.basename(real) != key:
            raise StoreError('persona folder escapes the personas folder')
        self.key, self._dir, self.clock = key, real, clock
        self._lock = threading.RLock()

    # ---- fixed-name file helpers (private) ----
    def _p(self, which):
        p = os.path.join(self._dir, FILES[which])
        if os.path.islink(p):
            raise StoreError('refusing a linked file')
        return p

    def _read_json(self, which, default):
        try:
            with open(self._p(which), encoding='utf-8') as f:
                v = json.load(f)
            return v if isinstance(v, dict) else dict(default)
        except FileNotFoundError:
            return json.loads(json.dumps(default))
        except ValueError:
            return json.loads(json.dumps(default))

    def _write_atomic(self, which, text):
        p = self._p(which)
        tmp = p + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)

    def _lines(self, which):
        try:
            with open(self._p(which), encoding='utf-8') as f:
                return [ln for ln in f.read().split('\n') if ln.strip()]
        except FileNotFoundError:
            return []

    # ---- profile ----
    def profile(self):
        with self._lock:
            d = dict(DEFAULT_PROFILE, notes=[])
            d.update({k: v for k, v in self._read_json('profile', d).items() if k in DEFAULT_PROFILE})
            return d

    def merge_profile(self, seed):
        """-> (profile, ignored_fields). Raises StoreError for a value over its limit or of the wrong type."""
        if not isinstance(seed, dict):
            raise StoreError('profile must be an object')
        upd, ignored = {}, []
        for k, v in seed.items():
            if k not in DEFAULT_PROFILE:
                ignored.append(str(k)[:30]); continue
            if k in PROFILE_LIMITS:
                if v is not None and (not isinstance(v, str) or len(v) > PROFILE_LIMITS[k]):
                    raise StoreError('%s: text up to %d characters' % (k, PROFILE_LIMITS[k]))
            elif k == 'notes':
                if not isinstance(v, list) or len(v) > NOTES_MAX or any(not isinstance(n, str) or len(n) > NOTE_LEN for n in v):
                    raise StoreError('notes: up to %d texts of %d characters' % (NOTES_MAX, NOTE_LEN))
            elif k == 'introduced' and not isinstance(v, bool):
                raise StoreError('introduced: true or false')
            upd[k] = v
        with self._lock:
            prof = self.profile()
            prof.update(upd)
            self._write_atomic('profile', json.dumps(prof, ensure_ascii=False, indent=1))
            return prof, ignored

    # ---- state ----
    def state(self):
        with self._lock:
            return self._read_json('state', {})

    def save_state(self, st):
        with self._lock:
            self._write_atomic('state', json.dumps(st, ensure_ascii=False))

    def update_state(self, fn):
        """Run fn(state_dict) under the lock, then save. -> fn's return value."""
        with self._lock:
            st = self.state()
            r = fn(st)
            self.save_state(st)
            return r

    # ---- inbox ----
    def append_event(self, event):
        """Append one event to THIS persona's inbox. Assigns id (a counter) and ts. -> the stored event.
        This is also the only door other personas use for 'tell': the caller hands over an event, nothing is read."""
        with self._lock:
            st = self.state()
            seq = int(st.get('next_id', 1))
            ev = dict(event)
            ev['id'] = seq
            ev.setdefault('ts', time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(self.clock())) + 'Z')
            # Track only direct messages in a dedicated, per-persona outcome stream.
            # Non-message events (check-ins, game events, persona-to-persona notes)
            # have different owners and must not create false consumer stalls.
            tracked = ev.get('type') == 'message'
            if tracked:
                progress = st.setdefault('reply_progress', {
                    'epoch': secrets.token_hex(16), 'produced_seq': 0, 'acked_seq': 0,
                    'completed': [], 'pending': []
                })
                progress['produced_seq'] = int(progress.get('produced_seq', 0)) + 1
                ev['progress_seq'] = progress['produced_seq']
                progress.setdefault('pending', []).append({
                    'seq': ev['progress_seq'], 'event_id': seq, 'at': float(self.clock())
                })
            lines = self._lines('inbox')
            lines.append(json.dumps(ev, ensure_ascii=False))
            lines = lines[-INBOX_KEEP:]
            if tracked:
                # Persist the watermark first. If the inbox append then fails, the
                # snapshot detects a missing event and reports unknown (never healthy).
                st['next_id'] = seq + 1
                self.save_state(st)
                self._write_atomic('inbox', '\n'.join(lines) + '\n')
            else:
                self._write_atomic('inbox', '\n'.join(lines) + '\n')
                st['next_id'] = seq + 1
                self.save_state(st)
            return ev

    def complete_message(self, msg_id=None, event_id=None):
        """Acknowledge a direct message only after its handler/reply succeeded.
        IDs are local to this persona. Returns False for untracked or unknown events.
        """
        with self._lock:
            events = self._lines('inbox')
            matched = None
            for line in events:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get('type') != 'message' or not isinstance(ev.get('progress_seq'), int):
                    continue
                if event_id is not None and ev.get('id') == event_id:
                    matched = ev
                    break
                if msg_id is not None and str(ev.get('msg_id')) == str(msg_id):
                    matched = ev
                    break
            if not matched:
                return False
            st = self.state()
            progress = st.get('reply_progress') or {}
            seq = int(matched['progress_seq'])
            if seq <= int(progress.get('acked_seq', 0)):
                return True
            pending = progress.get('pending') or []
            if not any(isinstance(x, dict) and x.get('seq') == seq for x in pending):
                return False
            done = set(int(x) for x in progress.get('completed', []) if isinstance(x, int) and not isinstance(x, bool))
            done.add(seq)
            acked = int(progress.get('acked_seq', 0))
            while acked + 1 in done:
                acked += 1
                done.remove(acked)
            progress['acked_seq'] = acked
            progress['completed'] = sorted(done)
            progress['pending'] = [x for x in pending if isinstance(x, dict) and int(x.get('seq', 0)) > acked]
            st['reply_progress'] = progress
            self.save_state(st)
            return True

    def ensure_reply_progress(self):
        """Create a fresh, empty direct-message outcome epoch if none exists."""
        with self._lock:
            st = self.state()
            if not isinstance(st.get('reply_progress'), dict):
                st['reply_progress'] = {'epoch': secrets.token_hex(16), 'produced_seq': 0,
                                        'acked_seq': 0, 'completed': [], 'pending': []}
                self.save_state(st)
            return st['reply_progress']

    def reply_progress_snapshot(self, now=None):
        """Content-free direct-message queue watermarks; inconsistent state is explicit."""
        now = float(self.clock() if now is None else now)
        with self._lock:
            st = self.state()
            p = st.get('reply_progress')
            if not isinstance(p, dict):
                return None
            produced, acked = p.get('produced_seq'), p.get('acked_seq')
            pending = p.get('pending')
            if (not isinstance(produced, int) or isinstance(produced, bool) or
                    not isinstance(acked, int) or isinstance(acked, bool) or
                    not isinstance(pending, list) or produced < acked):
                return {'consistent': False}
            pending_rows = [x for x in pending if isinstance(x, dict)]
            seqs = sorted(x.get('seq') for x in pending_rows)
            event_ids = {x.get('event_id') for x in pending_rows}
            inbox_ids = set()
            for line in self._lines('inbox'):
                try:
                    ev = json.loads(line)
                    if ev.get('type') == 'message' and isinstance(ev.get('progress_seq'), int):
                        inbox_ids.add(ev['id'])
                except (ValueError, AttributeError):
                    continue
            gap = produced - acked
            valid_rows = all(isinstance(x.get('seq'), int) and not isinstance(x.get('seq'), bool) and
                             isinstance(x.get('event_id'), int) and not isinstance(x.get('event_id'), bool) and
                             isinstance(x.get('at'), (int, float)) and not isinstance(x.get('at'), bool) for x in pending_rows)
            valid = (gap <= INBOX_KEEP and len(pending_rows) == len(pending) == gap and valid_rows and
                     seqs == list(range(acked + 1, produced + 1)) and
                     event_ids <= inbox_ids and isinstance(p.get('epoch'), str))
            oldest = min((float(x['at']) for x in pending_rows if isinstance(x.get('at'), (int, float))), default=None)
            return {'epoch': p['epoch'], 'produced_seq': produced, 'acked_seq': acked,
                    'oldest_pending_at': oldest, 'observed_at': now, 'heartbeat_at': now,
                    'consistent': bool(valid)}

    def read_inbox(self, after=0, limit=100):
        with self._lock:
            out = []
            for ln in self._lines('inbox'):
                try:
                    ev = json.loads(ln)
                except ValueError:
                    continue
                if ev.get('id', 0) > after:
                    out.append(ev)
            return out[:max(1, min(int(limit), INBOX_KEEP))]

    def inbox_count(self):
        with self._lock:
            return len(self._lines('inbox'))

    # ---- outbox log (lengths only, never text) ----
    def log_outbox(self, entry):
        with self._lock:
            e = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(self.clock())) + 'Z'}
            e.update({k: v for k, v in entry.items() if k != 'text'})
            lines = self._lines('outbox')
            lines.append(json.dumps(e, ensure_ascii=False))
            self._write_atomic('outbox', '\n'.join(lines[-OUTBOX_KEEP:]) + '\n')
            return e

    def outbox_count(self):
        with self._lock:
            return len(self._lines('outbox'))

    def last_outbox_ts(self, kinds):
        """Unix time of the newest outbox entry whose kind is in `kinds` (None if none). Reads only timestamps and kinds."""
        import calendar
        best = None
        with self._lock:
            for ln in self._lines('outbox'):
                try:
                    e = json.loads(ln)
                    if e.get('kind') in kinds:
                        t = calendar.timegm(time.strptime(e['ts'], '%Y-%m-%dT%H:%M:%SZ'))
                        best = t if best is None else max(best, t)
                except (ValueError, KeyError, TypeError):
                    continue
        return best
