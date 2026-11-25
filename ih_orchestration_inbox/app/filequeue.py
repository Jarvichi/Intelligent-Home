"""The old file queue (JSONL segments q-<first seq, 12 digits>.jsonl in <data>/queue, plus cursors.json), kept ONLY as the degraded-mode backend:
when /data/ih.db cannot be used the add-on keeps working on these plain files under /data/degraded (see inboxdb.py). Before 0.2.0 this was the only storage."""
import json, os, re, threading, time, collections

SEGMENT_MAX_BYTES = 256 * 1024
SEEN_MAX = 1000
CONSUMER_RE = re.compile(r'^[A-Za-z0-9_.-]{1,40}$')
SECONDS_PER_DAY = 86400
KIND_MESSAGE = 'message'                           # a record without `kind` is a Discord message from the bus


def kind_of(rec):
    return rec.get('kind') or KIND_MESSAGE


class Queue:
    """Append-only JSONL segments q-<first seq, 12 digits>.jsonl in <data>/queue, plus cursors.json. One lock; small and boring on purpose."""
    def __init__(self, data, retention_days=7, clock=time.time, segment_max=SEGMENT_MAX_BYTES):
        self.dir = os.path.join(data, 'queue')
        self.cursor_path = os.path.join(data, 'cursors.json')
        self.retention_s, self.clock, self.segment_max = retention_days * SECONDS_PER_DAY, clock, segment_max
        self.lock = threading.RLock()
        self.seq = 0
        self.cursors = {}
        self.seen = collections.OrderedDict()   # msg ids already stored (survives restart: rebuilt from the newest records)
        self.dropped = 0
        os.makedirs(self.dir, exist_ok=True)
        self._load()

    def segments(self):
        return sorted(f for f in os.listdir(self.dir) if re.fullmatch(r'q-\d{12}\.jsonl', f))

    def _records(self, fname):
        out = []
        try:
            with open(os.path.join(self.dir, fname)) as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        if isinstance(r, dict) and isinstance(r.get('seq'), int):
                            out.append(r)
                    except ValueError:
                        pass   # a torn last line after a crash: skip it
        except OSError:
            pass
        return out

    def _load(self):
        try:
            self.cursors = {k: int(v) for k, v in json.load(open(self.cursor_path)).items() if CONSUMER_RE.match(str(k))}
        except (OSError, ValueError, AttributeError, TypeError):
            self.cursors = {}
        for f in self.segments():
            for r in self._records(f):
                self.seq = max(self.seq, r['seq'])
                if r.get('msg_id'):
                    self.seen[r['msg_id']] = True
        while len(self.seen) > SEEN_MAX:
            self.seen.popitem(last=False)

    def cursor(self, consumer):
        with self.lock:
            return self.cursors.get(consumer, 0)

    def add(self, rec):
        """Store one record (a dict without seq). -> the stored record, or None when this msg_id is already in the queue."""
        with self.lock:
            mid = rec.get('msg_id')
            if mid and mid in self.seen:
                return None
            self.seq += 1
            rec = dict(rec, seq=self.seq)
            segs = self.segments()
            path = os.path.join(self.dir, segs[-1]) if segs else None
            if path is None or os.path.getsize(path) >= self.segment_max:
                path = os.path.join(self.dir, 'q-%012d.jsonl' % rec['seq'])
            with open(path, 'a') as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
            if mid:
                self.seen[mid] = True
                while len(self.seen) > SEEN_MAX:
                    self.seen.popitem(last=False)
            return rec

    def read(self, since=0, channel=None, limit=100, allowed_only=True, kind=None):
        now = self.clock()
        out = []
        with self.lock:
            segs = self.segments()
            for i, f in enumerate(segs):
                if i + 1 < len(segs) and int(segs[i + 1][2:14]) <= since + 1:
                    continue   # the whole segment is older than `since`
                for r in self._records(f):
                    if r['seq'] <= since or now - (r.get('received') or 0) > self.retention_s:
                        continue
                    if channel and r.get('channel_key') != channel:
                        continue
                    if kind and kind_of(r) != kind:
                        continue
                    if allowed_only and not r.get('allowed'):
                        continue
                    out.append(r)
                    if len(out) >= limit:
                        return out
        return out

    def ack(self, consumer, seq):
        with self.lock:
            seq = max(0, min(int(seq), self.seq))
            self.cursors[consumer] = max(self.cursors.get(consumer, 0), seq)   # never backwards
            tmp = self.cursor_path + '.tmp'
            with open(tmp, 'w') as fh:
                json.dump(self.cursors, fh)
            os.replace(tmp, self.cursor_path)
            return self.cursors[consumer]

    def retain(self):
        """Delete whole segments whose newest record is older than the retention. The newest segment is kept. -> number removed."""
        now, gone = self.clock(), 0
        with self.lock:
            for f in self.segments()[:-1]:
                recs = self._records(f)
                newest = max([r.get('received') or 0 for r in recs] or [0])
                if now - newest > self.retention_s:
                    try:
                        os.remove(os.path.join(self.dir, f)); gone += 1
                    except OSError:
                        pass
            self.dropped += gone
        return gone

    def stats(self):
        with self.lock:
            segs = self.segments()
            size = sum(os.path.getsize(os.path.join(self.dir, f)) for f in segs)
            return {'seq': self.seq, 'segments': len(segs), 'bytes': size, 'cursors': dict(self.cursors), 'segments_dropped': self.dropped}
