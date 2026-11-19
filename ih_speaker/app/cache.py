"""ih_speaker clip cache: every generated TTS clip is kept as a file under <dir>/<engine>/<voice>/<sha1 of the normalised text>.<ext>,
with an index in the add-on's own SQLite file (ihlib.db). Size cap with least-recently-used eviction (pinned clips are never evicted),
expiry of unpinned clips that have not been used for `days` days. The same text under two voices or engines is two entries.
Everything that touches the clock is injectable. No secrets are stored."""
import hashlib
import os
import re
import threading
import time

from ihlib import db as ihdb

MIGRATIONS = [(1, '''CREATE TABLE clips(
    id INTEGER PRIMARY KEY, engine TEXT NOT NULL, voice TEXT NOT NULL, key TEXT NOT NULL, text TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '', ext TEXT NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL, last_used REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0, UNIQUE(engine, voice, key));
    CREATE INDEX clips_lru ON clips(pinned, last_used)''')]

COLS = 'id, engine, voice, key, text, label, ext, size, created, last_used, hits, pinned'
FIELDS = COLS.split(', ')
MAX_CLIP = 8 * 1024 * 1024
DAY = 86400


def normalise(text):
    """Same phrase, same entry: case and spacing do not matter."""
    return ' '.join(str(text or '').casefold().split())


def text_key(text):
    return hashlib.sha1(normalise(text).encode('utf-8')).hexdigest()


def _seg(s):
    """A safe folder name for an engine or voice id."""
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(s or '')).strip('.') or 'default'


def sniff_ext(data):
    """File extension from the first bytes (HA gives mp3, wav or ogg depending on the engine). Unknown = mp3."""
    if data[:4] == b'RIFF':
        return 'wav'
    if data[:4] == b'OggS':
        return 'ogg'
    if data[:4] == b'fLaC':
        return 'flac'
    return 'mp3'


MIME = {'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg', 'flac': 'audio/flac'}


class Cache:
    def __init__(self, directory, database, max_bytes=1024 * 1024 * 1024, days=30, clock=time.time):
        """`database` is an ihlib.db.DB (usually /data/ih.db) or a path."""
        self.dir, self.max_bytes, self.days, self.clock = directory, int(max_bytes), int(days), clock
        self.db = database if hasattr(database, 'query') else ihdb.open_db(database, MIGRATIONS)
        if hasattr(database, 'query') and database.schema_version() < MIGRATIONS[-1][0]:
            database.migrate(MIGRATIONS)
        self.lock = threading.RLock()
        os.makedirs(self.dir, exist_ok=True)

    # ---- paths and rows
    def path(self, row):
        return os.path.join(self.dir, _seg(row['engine']), _seg(row['voice']), '%s.%s' % (row['key'], row['ext']))

    @staticmethod
    def _row(r):
        return dict(zip(FIELDS, r), pinned=bool(r[11])) if r else None

    def find(self, engine, voice, text):
        return self._row((self.db.query('SELECT %s FROM clips WHERE engine=? AND voice=? AND key=?' % COLS,
                                        (engine, voice or '', text_key(text))) or [None])[0])

    def by_id(self, cid):
        return self._row((self.db.query('SELECT %s FROM clips WHERE id=?' % COLS, (int(cid),)) or [None])[0])

    def by_label(self, engine, voice, label):
        lab = normalise(label)
        for r in self.db.query('SELECT %s FROM clips WHERE engine=? AND voice=? ORDER BY pinned DESC, hits DESC' % COLS, (engine, voice or '')):
            row = self._row(r)
            if lab and normalise(row['label']) == lab:
                return row
        return None

    def list(self, engine='', voice=None, q=''):
        """Rows newest-used first. voice None = all voices ('' is the engine default voice)."""
        sql, args = 'SELECT %s FROM clips WHERE 1=1' % COLS, []
        if engine:
            sql, args = sql + ' AND engine=?', args + [engine]
        if voice is not None:
            sql, args = sql + ' AND voice=?', args + [voice]
        rows = [self._row(r) for r in self.db.query(sql + ' ORDER BY last_used DESC', args)]
        needle = normalise(q)
        return [r for r in rows if not needle or needle in normalise(r['text']) or needle in normalise(r['label'])]

    def total(self):
        r = self.db.query('SELECT COALESCE(SUM(size),0), COUNT(*) FROM clips')[0]
        return int(r[0]), int(r[1])

    def read(self, row):
        with open(self.path(row), 'rb') as f:
            return f.read()

    # ---- use
    def get(self, engine, voice, text):
        """A hit: the row, with last_used and hits updated. A miss, or a row whose file is gone: None (the stale row is dropped)."""
        row = self.find(engine, voice, text)
        if not row:
            return None
        if not os.path.exists(self.path(row)):
            self.db.execute('DELETE FROM clips WHERE id=?', (row['id'],))
            return None
        self.db.execute('UPDATE clips SET last_used=?, hits=hits+1 WHERE id=?', (self.clock(), row['id']))
        return self.by_id(row['id'])

    def put(self, engine, voice, text, data, label='', pinned=None):
        """Store a clip (replaces the audio of the same engine, voice and text), then expire and evict. -> row."""
        if not data or len(data) > MAX_CLIP:
            raise ValueError('audio is empty or too large')
        voice, text, now = voice or '', str(text).strip(), self.clock()
        with self.lock:
            old, key, ext = self.find(engine, voice, text), text_key(text), sniff_ext(data)
            row = {'engine': engine, 'voice': voice, 'key': key, 'ext': ext}
            p = self.path(row)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, p)
            if old:
                if old['ext'] != ext:
                    self._unlink(old)
                self.db.execute('UPDATE clips SET ext=?, size=?, last_used=?, text=?, label=CASE WHEN ?<>\'\' THEN ? ELSE label END, '
                                'pinned=CASE WHEN ? IS NULL THEN pinned ELSE ? END WHERE id=?',
                                (ext, len(data), now, text, label, label, pinned, None if pinned is None else int(bool(pinned)), old['id']))
                cid = old['id']
            else:
                cid = self.db.execute('INSERT INTO clips(engine, voice, key, text, label, ext, size, created, last_used, hits, pinned) '
                                      'VALUES(?,?,?,?,?,?,?,?,?,0,?)', (engine, voice, key, text, label, ext, len(data), now, now, int(bool(pinned)))).lastrowid
            self.expire()
            self.evict()
            return self.by_id(cid)

    def set_pinned(self, cid, pinned):
        return bool(self.db.execute('UPDATE clips SET pinned=? WHERE id=?', (int(bool(pinned)), int(cid))).rowcount)

    def delete(self, cid):
        with self.lock:
            row = self.by_id(cid)
            if not row:
                return False
            self._unlink(row)
            self.db.execute('DELETE FROM clips WHERE id=?', (row['id'],))
            return True

    def _unlink(self, row):
        try:
            os.remove(self.path(row))
        except OSError:
            pass

    # ---- limits
    def plan_expire(self):
        cutoff = self.clock() - self.days * DAY
        return [self._row(r) for r in self.db.query('SELECT %s FROM clips WHERE pinned=0 AND last_used<? ORDER BY last_used' % COLS, (cutoff,))]

    def plan_evict(self, extra=0):
        """Unpinned clips (least recently used first) that must go so the total (plus `extra` bytes about to be stored) fits the cap."""
        total, _ = self.total()
        over, out = total + extra - self.max_bytes, []
        if over <= 0:
            return out
        for r in self.db.query('SELECT %s FROM clips WHERE pinned=0 ORDER BY last_used, id' % COLS):
            row = self._row(r)
            out.append(row)
            over -= row['size']
            if over <= 0:
                break
        return out

    def expire(self):
        gone = self.plan_expire()
        for r in gone:
            self.delete(r['id'])
        return gone

    def evict(self):
        gone = self.plan_evict()
        for r in gone:
            self.delete(r['id'])
        return gone

    def stats(self):
        size, n = self.total()
        pinned = self.db.query('SELECT COUNT(*) FROM clips WHERE pinned=1')[0][0]
        return {'clips': n, 'pinned': pinned, 'bytes': size, 'max_bytes': self.max_bytes, 'days': self.days}


def brief(row):
    """A row without the text, for logs and dry-run answers."""
    return {k: row[k] for k in ('id', 'engine', 'voice', 'size', 'last_used', 'hits', 'pinned')}
