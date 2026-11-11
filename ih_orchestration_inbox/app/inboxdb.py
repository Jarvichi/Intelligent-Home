"""Storage for the orchestration inbox: one SQLite file (/data/ih.db) through the shared ihlib.db helper.

What is stored where (full table in DOCS.md):
  table queue_messages  <- old /data/queue/q-<seq>.jsonl segments (one row per stored Discord message)
  table queue_cursors   <- old /data/cursors.json (one row per consumer)
Everything else stays a file: options.json (config and secrets), STOP / STOP_LIVE (kill switches), the add-on log.

Rules this module keeps (same as the AI trust pilot, ih_jobs_trust 0.2.0):
  - Old files are COPIED into the database and verified inside the import transaction. Only after the commit does ihlib move the
    file to /data/trash (purged after 30 days). A file that fails the check stays where it is and is reported in status.
  - If the database is damaged, too new, or cannot be opened, the add-on keeps running in DEGRADED mode on the old file queue,
    rooted in /data/degraded/ (never the old paths; the database is never recreated). After the database is restored, the next
    start imports /data/degraded/ (messages get NEW seq numbers, cursors keep the higher value).
  - All database work runs on ONE long-lived worker thread (ihlib keeps one connection per thread; HTTP threads are short-lived).
  - Status shows counts only: never message text, author or channel."""
import json, os, re, threading, time, logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from ihlib import db as ihdb
import filequeue

DB_NAME = ihdb.DB_FILE_NAME
TRASH_DIR = 'trash'                  # old files wait here, deleted by ihdb.purge_trash after ihdb.TRASH_KEEP_DAYS
BACKUP_DIR = 'backup'                # nightly snapshots, newest ihdb.SNAPSHOT_KEEP kept
DEGRADED_DIR = 'degraded'            # plain files written while the database is unusable
QUEUE_DIR = 'queue'                  # old layout: <data>/queue/q-<12 digits>.jsonl
CURSORS_FILE = 'cursors.json'        # old layout: <data>/cursors.json
SEGMENT_RE = re.compile(r'q-\d{12}\.jsonl')
SNAPSHOT_HOUR = 4                    # local hour from which the nightly snapshot may be taken (before the HA backup at about 05:00)
SNAPSHOT_MIN_AGE_S = 20 * 3600       # a snapshot younger than this counts as "done for tonight"
MAINTENANCE_CHECK_S = 600            # how often the maintenance thread looks at the clock
CALL_TIMEOUT_S = 30                  # longest wait for the database worker thread
HEALTH_TIMEOUT_S = 5                 # status must answer fast even when the worker is busy
DEFAULT_TIMEZONE = 'Europe/London'   # for the snapshot hour (UTC when the container has no tz data)
ERROR_TEXT_MAX = 300
MAX_IMPORT_ERRORS = 10

MIGRATIONS = [
    (1, '''CREATE TABLE queue_messages(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              msg_id TEXT NOT NULL,
              channel_key TEXT NOT NULL,
              author_id TEXT NOT NULL,
              allowed INTEGER NOT NULL DEFAULT 0,
              ts_json TEXT NOT NULL DEFAULT 'null',
              received INTEGER NOT NULL DEFAULT 0,
              text TEXT NOT NULL DEFAULT '',
              reply_to_json TEXT NOT NULL DEFAULT 'null',
              att_count INTEGER NOT NULL DEFAULT 0,
              att_kinds_json TEXT NOT NULL DEFAULT '[]',
              kind TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT '',
              ref TEXT NOT NULL DEFAULT '');
        CREATE UNIQUE INDEX queue_messages_msg_id ON queue_messages(msg_id) WHERE msg_id <> '';
        CREATE INDEX queue_messages_channel ON queue_messages(channel_key, seq);
        CREATE INDEX queue_messages_received ON queue_messages(received);
        CREATE TABLE queue_cursors(consumer TEXT PRIMARY KEY, seq INTEGER NOT NULL DEFAULT 0)'''),
]
EXPECTED_SCHEMA = MIGRATIONS[-1][0]
_COLUMNS = 'seq, msg_id, channel_key, author_id, allowed, ts_json, received, text, reply_to_json, att_count, att_kinds_json, kind, source, ref'

_LOG = logging.getLogger('ih_orchestration_inbox')


def _short(e):
    return ('%s: %s' % (type(e).__name__, e))[:ERROR_TEXT_MAX]


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _values(rec):
    """Column values (without seq) of one record dict."""
    att = rec.get('attachments') if isinstance(rec.get('attachments'), dict) else {}
    kinds = att.get('kinds') if isinstance(att.get('kinds'), list) else []
    return (str(rec.get('msg_id') or ''), str(rec.get('channel_key') or ''), str(rec.get('author_id') or ''), 1 if rec.get('allowed') else 0,
            json.dumps(rec.get('ts')), _int(rec.get('received')), str(rec.get('text') or ''), json.dumps(rec.get('reply_to')),
            _int(att.get('count')), json.dumps(kinds), str(rec.get('kind') or ''), str(rec.get('source') or ''), str(rec.get('ref') or ''))


def _rec(row):
    """A row -> the record dict the API has always returned (same keys, same order)."""
    seq, msg_id, channel, author, allowed, ts, received, text, reply, count, kinds, kind, source, ref = row
    if kind:                                                  # an event from a local service (POST /events)
        return {'kind': kind, 'source': source, 'ref': ref, 'msg_id': msg_id, 'channel_key': channel, 'author_id': author, 'allowed': bool(allowed),
                'ts': json.loads(ts), 'received': received, 'text': text, 'seq': seq}
    return {'msg_id': msg_id, 'channel_key': channel, 'author_id': author, 'allowed': bool(allowed), 'ts': json.loads(ts), 'received': received,
            'text': text, 'reply_to': json.loads(reply), 'attachments': {'count': count, 'kinds': json.loads(kinds)}, 'seq': seq}


def _segment_files(folder):
    try:
        return sorted(f for f in os.listdir(folder) if SEGMENT_RE.fullmatch(f))
    except OSError:
        return []


def _valid(rec):
    return isinstance(rec, dict) and isinstance(rec.get('seq'), int) and not isinstance(rec.get('seq'), bool)


class InboxStore:
    """The queue and the consumer cursors. Same calls as the old file Queue: add, read, ack, retain, stats, seq, cursor."""

    def __init__(self, data_dir, retention_days=7, clock=time.time, log=None):
        self.data_dir, self.clock = data_dir, clock
        self.retention_s = retention_days * ihdb.SECONDS_PER_DAY
        self.log = log or _LOG.info
        self.path = os.path.join(data_dir, DB_NAME)
        self.trash_dir = os.path.join(data_dir, TRASH_DIR)
        self.backup_dir = os.path.join(data_dir, BACKUP_DIR)
        self.degraded_dir = os.path.join(data_dir, DEGRADED_DIR)
        self.arrived = threading.Condition()   # told on every add: long polls wait on it
        self.db = None
        self.degraded = None             # text when the database cannot be used at all
        self.runtime_error = None        # last failed database call while the file was open
        self.import_errors = []
        self.imported = {}               # {'messages': n, 'cursors': n} files moved to the trash this process
        self.dropped = 0                 # rows removed by the retention rule in this process
        self.last_check = None           # restore_check result of the newest snapshot (counts only)
        self.last_purge_day = None
        self.last_maintenance_error = None
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix='inboxdb')
        self._health = None
        self._file = None
        self._file_lock = threading.Lock()
        self._stop = threading.Event()

    # ---- run on the one database thread
    def _call(self, fn, timeout=CALL_TIMEOUT_S):
        return self._ex.submit(fn).result(timeout)

    def open(self):
        """Open, migrate and import the legacy files. Never raises: failure -> degraded mode."""
        try:
            self._call(self._open)
        except Exception as e:                                       # includes a timeout
            self.degraded = _short(e)
            self.db = None
            self.log('inbox: DATABASE DEGRADED, running on plain files in %s: %s' % (self.degraded_dir, self.degraded))
        return self

    def _open(self):
        self.db = ihdb.open(self.path, MIGRATIONS, snapshot_dir=self.backup_dir, clock=self.clock)
        self._import_legacy()
        self.maintain()
        if self.imported:
            self._snapshot_and_check()           # a safety copy right after the files moved to the trash

    def close(self):
        self._stop.set()
        try:
            self._call(lambda: self.db and self.db.close(), HEALTH_TIMEOUT_S)
        except Exception:
            pass
        self._ex.shutdown(wait=False)

    def _file_queue(self):
        """The old file queue, rooted in /data/degraded. Created on first use only."""
        with self._file_lock:
            if self._file is None:
                self._file = filequeue.Queue(self.degraded_dir, self.retention_s // ihdb.SECONDS_PER_DAY, self.clock)
            return self._file

    def _run(self, db_fn, file_fn):
        if self.db is not None:
            try:
                return self._call(db_fn)
            except Exception as e:
                self.runtime_error = _short(e)
                self.log('inbox: DATABASE CALL FAILED, using plain files in %s instead: %s' % (self.degraded_dir, self.runtime_error))
        return file_fn(self._file_queue())

    # ---- legacy import (copy, verify, then trash)
    def _import_legacy(self):
        for folder, fresh in ((self.data_dir, False), (self.degraded_dir, True)):
            qdir = os.path.join(folder, QUEUE_DIR)
            sources = [(os.path.join(qdir, f), 'messages', self._put_messages(fresh)) for f in _segment_files(qdir)]
            sources.append((os.path.join(folder, CURSORS_FILE), 'cursors', self._put_cursors))
            for path, kind, target in sources:
                if not os.path.isfile(path):
                    continue
                try:
                    r = ihdb.import_legacy_json(self.db, path, target, self.trash_dir, skip_bad_lines=True)   # a torn last line is skipped, as before
                except ihdb.LegacyImportError as e:
                    self._import_failed(path, e)
                    continue
                if r['status'] == 'imported':
                    self.imported[kind] = self.imported.get(kind, 0) + 1
                    self.log('inbox: imported %s (%d row(s)) into the database, old file moved to trash' % (os.path.basename(path), r['rows']))
            try:
                os.rmdir(qdir)                    # only succeeds when empty
            except OSError:
                pass

    def _import_failed(self, path, e):
        msg = '%s: %s' % (os.path.relpath(path, self.data_dir), _short(e))
        self.log('inbox: legacy import FAILED, file left in place: %s' % msg)
        if msg not in self.import_errors and len(self.import_errors) < MAX_IMPORT_ERRORS:
            self.import_errors.append(msg)

    def _put_messages(self, fresh):
        """Import target for one segment. fresh=False keeps the old seq numbers; fresh=True (degraded files) takes new ones."""
        def put(con, data):
            if not isinstance(data, list):
                raise ValueError('segment is not a list of records')
            recs = [r for r in data if _valid(r)]
            before = con.execute('SELECT COUNT(*) FROM queue_messages').fetchone()[0]
            for r in recs:
                v = _values(r)
                if fresh:
                    if v[0] and con.execute('SELECT 1 FROM queue_messages WHERE msg_id=?', (v[0],)).fetchone():
                        continue
                    con.execute('INSERT INTO queue_messages(msg_id, channel_key, author_id, allowed, ts_json, received, text, reply_to_json, att_count, att_kinds_json, kind, source, ref) '
                                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', v)
                else:
                    con.execute('INSERT OR IGNORE INTO queue_messages(%s) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)' % _COLUMNS, (r['seq'],) + v)
            for r in recs:                                            # verify before the commit (and so before the move)
                mid = str(r.get('msg_id') or '')
                if mid:
                    row = con.execute('SELECT %s FROM queue_messages WHERE msg_id=?' % _COLUMNS, (mid,)).fetchone()
                else:
                    row = con.execute('SELECT %s FROM queue_messages WHERE seq=?' % _COLUMNS, (r['seq'],)).fetchone() if not fresh else True
                if row is None:
                    raise ValueError('verify failed: message with seq %d is not in the table' % r['seq'])
                if row is not True and not fresh and row[0] == r['seq']:
                    stored = _rec(row)
                    if any(stored.get(k) != val for k, val in r.items()):
                        raise ValueError('verify failed: row with seq %d differs' % r['seq'])
            after = con.execute('SELECT COUNT(*) FROM queue_messages').fetchone()[0]
            if after < before:
                raise ValueError('verify failed: row counts %d -> %d' % (before, after))
            return len(recs)
        return put

    @staticmethod
    def _put_cursors(con, data):
        if not isinstance(data, dict):
            raise ValueError('cursors file is not an object')
        n = 0
        for consumer, seq in data.items():
            if not filequeue.CONSUMER_RE.match(str(consumer)):
                continue
            seq = max(0, _int(seq))
            con.execute('INSERT INTO queue_cursors(consumer, seq) VALUES(?, ?) ON CONFLICT(consumer) DO UPDATE SET seq=MAX(seq, excluded.seq)', (str(consumer), seq))
            if con.execute('SELECT seq FROM queue_cursors WHERE consumer=?', (str(consumer),)).fetchone()[0] < seq:
                raise ValueError('verify failed: cursor differs')
            n += 1
        return n

    # ---- the queue (same calls as the old file Queue)
    def _max_seq(self, con):
        row = con.execute("SELECT seq FROM sqlite_sequence WHERE name='queue_messages'").fetchone()
        return row[0] if row else 0

    @property
    def seq(self):
        return self._run(lambda: self._max_seq(self.db.con), lambda q: q.seq)

    def cursor(self, consumer):
        def db_fn():
            row = self.db.con.execute('SELECT seq FROM queue_cursors WHERE consumer=?', (consumer,)).fetchone()
            return row[0] if row else 0
        return self._run(db_fn, lambda q: q.cursor(consumer))

    def add(self, rec):
        """Store one record (a dict without seq). -> the stored record, or None when this msg_id is already in the queue."""
        def db_fn():
            with self.db.tx() as con:
                v = _values(rec)
                if v[0] and con.execute('SELECT 1 FROM queue_messages WHERE msg_id=?', (v[0],)).fetchone():
                    return None                                   # checked first: an ignored INSERT would still use up a seq number
                cur = con.execute('INSERT INTO queue_messages(msg_id, channel_key, author_id, allowed, ts_json, received, text, reply_to_json, '
                                  'att_count, att_kinds_json, kind, source, ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', v)
                return dict(rec, seq=cur.lastrowid)
        stored = self._run(db_fn, lambda q: q.add(rec))
        if stored is not None:
            with self.arrived:
                self.arrived.notify_all()
        return stored

    def wait_beyond(self, seq, timeout):
        """Block until a record newer than `seq` is stored, or `timeout` seconds pass. -> True when one arrived."""
        with self.arrived:
            return self.arrived.wait_for(lambda: self.seq > seq, timeout)

    def read(self, since=0, channel=None, limit=100, allowed_only=True, kind=None):
        def db_fn():
            sql, args = 'SELECT %s FROM queue_messages WHERE seq > ? AND received >= ?' % _COLUMNS, [since, self.clock() - self.retention_s]
            if channel:
                sql, args = sql + ' AND channel_key = ?', args + [channel]
            if kind:
                sql, args = sql + ' AND kind = ?', args + ['' if kind == filequeue.KIND_MESSAGE else kind]
            if allowed_only:
                sql += ' AND allowed = 1'
            return [_rec(r) for r in self.db.con.execute(sql + ' ORDER BY seq LIMIT ?', args + [limit]).fetchall()]
        return self._run(db_fn, lambda q: q.read(since, channel, limit, allowed_only, kind))

    def ack(self, consumer, seq):
        def db_fn():
            with self.db.tx() as con:
                top = max(0, min(int(seq), self._max_seq(con)))
                con.execute('INSERT INTO queue_cursors(consumer, seq) VALUES(?, ?) ON CONFLICT(consumer) DO UPDATE SET seq=MAX(seq, excluded.seq)', (consumer, top))   # never backwards
                return con.execute('SELECT seq FROM queue_cursors WHERE consumer=?', (consumer,)).fetchone()[0]
        return self._run(db_fn, lambda q: q.ack(consumer, seq))

    def retain(self):
        """Delete messages older than the retention. -> number removed."""
        def db_fn():
            with self.db.tx() as con:
                return con.execute('DELETE FROM queue_messages WHERE received < ?', (self.clock() - self.retention_s,)).rowcount
        def file_fn(q):
            return q.retain()
        gone = self._run(db_fn, file_fn)
        self.dropped += gone
        return gone

    def stats(self):
        """Same keys as the old file queue. segments is 0 in database mode, bytes is the database file, segments_dropped counts rows removed by retention."""
        def db_fn():
            con = self.db.con
            return {'seq': self._max_seq(con), 'segments': 0, 'bytes': os.path.getsize(self.path) if os.path.exists(self.path) else 0,
                    'cursors': {c: s for c, s in con.execute('SELECT consumer, seq FROM queue_cursors ORDER BY consumer')},
                    'segments_dropped': self.dropped, 'rows': con.execute('SELECT COUNT(*) FROM queue_messages').fetchone()[0]}
        return self._run(db_fn, lambda q: q.stats())

    # ---- nightly work: trash purge, snapshot, restore check
    def _newest_snapshot(self):
        names = ihdb._snapshot_names(self.backup_dir, os.path.splitext(DB_NAME)[0])
        return os.path.join(self.backup_dir, names[-1]) if names else None

    def _snapshot_age_s(self):
        p = self._newest_snapshot()
        if not p:
            return None
        stamp = re.search(r'-(\d{8}T\d{6}Z)\.db$', p)
        try:
            t = datetime.strptime(stamp.group(1), ihdb.STAMP_FORMAT).replace(tzinfo=timezone.utc).timestamp()
        except (AttributeError, ValueError):
            t = os.path.getmtime(p)
        return self.clock() - t

    def maintain(self, local_hour=None):
        """Purge the trash once a day; take a snapshot once a night (from SNAPSHOT_HOUR) and check it can be restored.
        local_hour None = no hour gate (used at start only for the purge). Never raises."""
        if self.db is None:
            return
        try:
            day = datetime.fromtimestamp(self.clock(), timezone.utc).date()
            if self.last_purge_day != day:
                gone = ihdb.purge_trash(self.trash_dir, clock=self.clock)
                self.last_purge_day = day
                if gone:
                    self.log('inbox: trash purge removed %d old file(s)' % len(gone))
            age = self._snapshot_age_s()
            if local_hour is not None and local_hour >= SNAPSHOT_HOUR and (age is None or age > SNAPSHOT_MIN_AGE_S):
                self._snapshot_and_check()
            self.last_maintenance_error = None
        except Exception as e:
            self.last_maintenance_error = _short(e)
            self.log('inbox: nightly maintenance FAILED: %s' % self.last_maintenance_error)

    def _snapshot_and_check(self):
        snap = ihdb.snapshot(self.path, self.backup_dir, clock=self.clock)
        chk = ihdb.restore_check(snap)
        live = self.db.table_counts()
        self.last_check = {'at': ihdb._iso(self.clock()), 'ok': chk['ok'], 'schema_version': chk['schema_version'], 'rows': chk['tables'],
                           'rows_match_live': chk['tables'] == live, 'error': chk['error']}
        self.log('inbox: snapshot taken, restore check %s, schema %s, rows %s%s' % (
            'OK' if chk['ok'] else 'FAILED', chk['schema_version'], json.dumps(chk['tables'], sort_keys=True), '' if self.last_check['rows_match_live'] else ' (differs from live)'))

    def maintain_now(self, local_hour):
        """Run maintain() on the database thread."""
        try:
            self._call(lambda: self.maintain(local_hour))
        except Exception as e:
            self.last_maintenance_error = _short(e)

    def start_maintenance(self, tz_name=DEFAULT_TIMEZONE):
        """Daemon thread: wakes every MAINTENANCE_CHECK_S, runs maintain() with the local hour."""
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc

        def loop():
            while not self._stop.wait(MAINTENANCE_CHECK_S):
                self.maintain_now(datetime.fromtimestamp(self.clock(), tz).hour)
        t = threading.Thread(target=loop, daemon=True, name='inboxdb-maint')
        t.start()
        return t

    # ---- status (counts only)
    def _health_now(self):
        h = {'engine': 'sqlite', 'file': DB_NAME, 'degraded': False, 'schema_version': None, 'expected_schema_version': EXPECTED_SCHEMA, 'rows': {}}
        if self.db is not None:
            h['schema_version'] = self.db.schema_version()
            h['rows'] = self.db.table_counts()
        return h

    def health(self):
        h = None
        if self.db is not None:
            try:
                h = self._call(self._health_now, HEALTH_TIMEOUT_S)
                self._health = h
            except Exception as e:
                h = dict(self._health or {'engine': 'sqlite', 'file': DB_NAME, 'expected_schema_version': EXPECTED_SCHEMA})
                h['status_error'] = _short(e)
        if h is None:
            h = {'engine': 'sqlite', 'file': DB_NAME, 'schema_version': None, 'expected_schema_version': EXPECTED_SCHEMA, 'rows': {}}
        h['degraded'] = self.degraded is not None
        h['error'] = self.degraded or self.runtime_error
        if self.degraded:
            h['WARNING'] = 'DATABASE NOT IN USE: messages are kept in plain files in /data/%s until the database is restored' % DEGRADED_DIR
        h['last_snapshot'] = None
        age = self._snapshot_age_s()
        if age is not None:
            h['last_snapshot'] = ihdb._iso(self.clock() - age)
        try:
            h['snapshots'] = len(ihdb._snapshot_names(self.backup_dir, os.path.splitext(DB_NAME)[0]))
        except OSError:
            h['snapshots'] = 0
        h['last_restore_check'] = self.last_check
        files = _scan(self.trash_dir)
        h['trash'] = {'files': len(files), 'bytes': sum(s for _, s in files)}
        h['legacy_import_errors'] = list(self.import_errors)
        h['maintenance_error'] = self.last_maintenance_error
        h['corrupt_copies'] = len(ihdb.quarantined(self.path))
        return h


def _scan(folder):
    try:
        return [(e.name, e.stat(follow_symlinks=False).st_size) for e in os.scandir(folder) if e.is_file(follow_symlinks=False)]
    except OSError:
        return []
