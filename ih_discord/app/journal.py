"""Journal and cursor store of the Discord transport (generic: no deployment details).
Journal = append-only JSON lines (/data/journal.jsonl). A message is written here BEFORE its channel cursor moves, so a restart resumes from
the cursor with nothing lost and nothing fetched twice. A second line {"done": id} marks "routed", so recover() can re-route a message that
was journaled but not routed when the process died. Rotation keeps the last 24 h and a line cap. Cursors = /data/cursor.json (atomic write)."""
import json, os, threading, time

KEEP_S = 24 * 3600
MAX_LINES = 5000
CHECK_EVERY = 200


class Cursors:
    """{channel key: {"id": channel id, "last": newest message id handled}}. A changed channel id counts as a new channel (first run)."""
    def __init__(self, path):
        self.path, self.d, self.lock = path, {}, threading.Lock()
        self.corrupt = False
        try:
            with open(path) as f:
                x = json.load(f)
            if isinstance(x, dict):
                self.d = {str(k): v for k, v in x.items() if isinstance(v, dict) and 'last' in v}
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.corrupt = True   # treated as "no cursors": the next poll starts at the newest message

    def get(self, key, chan_id):
        v = self.d.get(key)
        if v and str(v.get('id')) == str(chan_id):
            return str(v['last'])
        return None

    def set(self, key, chan_id, last):
        with self.lock:
            self.d[key] = {'id': str(chan_id), 'last': str(last)}
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.d, f)
            os.replace(tmp, self.path)

    def reset(self):
        with self.lock:
            self.d = {}
            try: os.remove(self.path)
            except OSError: pass


class Journal:
    def __init__(self, path, clock=time.time, keep_s=KEEP_S, max_lines=MAX_LINES, fsync=True):
        self.path, self.clock, self.keep_s, self.max_lines, self.fsync = path, clock, keep_s, max_lines, fsync
        self.lock, self.n = threading.Lock(), 0
        try:   # a crash can leave a torn last line without a newline: end it, so the next append is not glued to it
            with open(path, 'rb+') as f:
                f.seek(0, 2)
                if f.tell() and (f.seek(-1, 2) or f.read(1)) != b'\n':
                    f.write(b'\n')
        except OSError:
            pass
        self.rotate()

    def append(self, entry):
        """Write one line (flushed to disk). Returns when it is safe to move the cursor. Raises OSError if the write failed."""
        e = dict(entry); e.setdefault('t', int(self.clock()))
        with self.lock:
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + '\n')
                f.flush()
                if self.fsync:
                    os.fsync(f.fileno())
            self.n += 1
            if self.n >= CHECK_EVERY:
                self.n = 0
                self._rotate_locked()

    def mark_done(self, mid):
        self.append({'done': str(mid)})

    def read(self):
        out = []
        try:
            with open(self.path, encoding='utf-8') as f:
                for ln in f:
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        continue   # a torn last line after a crash
        except OSError:
            pass
        return out

    def rotate(self):
        with self.lock:
            self._rotate_locked()

    def _rotate_locked(self):
        rows = []
        try:
            with open(self.path, encoding='utf-8') as f:
                for ln in f:
                    try: rows.append(json.loads(ln))
                    except ValueError: continue
        except OSError:
            return
        now = self.clock()
        keep = [r for r in rows if now - r.get('t', now) <= self.keep_s][-self.max_lines:]
        if len(keep) == len(rows):
            return
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            for r in keep:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')
        os.replace(tmp, self.path)

    def pending(self, max_age_s, now=None):
        """Message entries not marked done and not skipped, younger than max_age_s, oldest first (for recovery after a crash)."""
        now = self.clock() if now is None else now
        rows = self.read()
        done = {r['done'] for r in rows if 'done' in r}
        return [r for r in rows if 'id' in r and not r.get('skipped') and r['id'] not in done and now - r.get('msg_ts', r.get('t', now)) <= max_age_s]

    def since(self, channel_key, after_id, max_age_s, now=None):
        """Routable message entries of one channel key with id > after_id and young enough (for redelivery to a returning module)."""
        now = self.clock() if now is None else now
        try:
            a = int(after_id or 0)
        except ValueError:
            a = 0
        return [r for r in self.read() if 'id' in r and not r.get('skipped') and r.get('channel_key') == channel_key
                and int(r['id']) > a and now - r.get('msg_ts', r.get('t', now)) <= max_age_s]

    def count(self):
        return len(self.read())
