"""Live worker status (0.4.0): what each Claude runner is doing right now (worker name, task title, stage, short detail).
Kept in memory with a short copy in /data/workers.json (so a restart does not lose it). The runner posts on each stage change and a final 'idle'.
The server stamps its own clock on every update. Not updated for 5 minutes = 'stalled'; not updated for 30 minutes = dropped. Idle removes the entry."""
import json, os, re, threading
from datetime import datetime, timedelta, timezone

from store import Store, StoreError, iso, parse, utcnow

STAGES = ('starting', 'assessing', 'planning', 'implementing', 'reporting', 'idle')
STALLED_S = 300
DROP_S = 1800
MAX_WORKERS = 8
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,19}$')
KEY_RE = re.compile(r'(bearer\s+\S+|\b[A-Za-z0-9+/_-]{24,}|(token|secret|password|key)[\w-]*\s*[=:]\s*\S+)', re.I)


def _text(v, n):
    s = re.sub(r'\s+', ' ', v if isinstance(v, str) else '').strip()
    s = KEY_RE.sub('[hidden]', s)
    return s if len(s) <= n else s[:n - 1] + '…'


class Workers:
    def __init__(self, data_dir, clock=utcnow):
        self.path = os.path.join(data_dir, 'workers.json')
        self.clock = clock
        self.lock = threading.Lock()
        self.items = {}
        try:
            with open(self.path) as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.items = {k: v for k, v in d.items() if isinstance(v, dict) and NAME_RE.match(str(k))}
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            Store.write_json(self.path, self.items)
        except OSError:
            pass   # the in-memory copy still works

    def _prune(self, now):
        for k in [k for k, v in self.items.items() if ((parse(v.get('updated_at')) or now) + timedelta(seconds=DROP_S)) <= now]:
            del self.items[k]

    def update(self, d):
        """Validate and store one status message. Raises StoreError('bad')."""
        w = d.get('worker')
        if not isinstance(w, str) or not NAME_RE.match(w):
            raise StoreError('bad', 'worker must be 1-20 lowercase letters, digits, - or _')
        stage = d.get('stage')
        if stage not in STAGES:
            raise StoreError('bad', 'stage must be one of: ' + ', '.join(STAGES))
        now = self.clock()
        with self.lock:
            self._prune(now)
            if stage == 'idle':
                self.items.pop(w, None)
            else:
                if w not in self.items and len(self.items) >= MAX_WORKERS:
                    raise StoreError('conflict', 'too many workers')
                started = d.get('started_at')
                started = iso(parse(started)) if isinstance(started, str) and parse(started) else (self.items.get(w, {}).get('started_at') or iso(now))
                self.items[w] = dict(worker=w, title=_text(d.get('title'), 120), ref=_text(d.get('ref'), 60), started_at=started,
                                     stage=stage, detail=_text(d.get('detail'), 80), updated_at=iso(now))
            self._save()
        return {'ok': True}

    def view(self):
        now = self.clock()
        with self.lock:
            self._prune(now)
            out = []
            for v in sorted(self.items.values(), key=lambda x: x['worker']):
                upd, st = parse(v['updated_at']) or now, parse(v['started_at']) or now
                age = max(0, int((now - upd).total_seconds()))
                out.append(dict(v, elapsed_s=max(0, int((now - st).total_seconds())), age_s=age, stalled=age >= STALLED_S))
        return {'workers': out, 'now': iso(now)}
