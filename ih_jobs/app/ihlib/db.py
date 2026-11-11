"""ihlib.db: one SQLite file per add-on (usually /data/ih.db). stdlib only (Python 3.12+).

NO SECRETS IN THE DATABASE, EVER. Tokens, passwords, keys and PINs stay in the add-on options
(options.json and friends, owned by the Supervisor). A database copy would spread them into
snapshots and backups. Human-readable .log files and rebuildable caches also stay files.

Thread safety: ONE CONNECTION PER THREAD. `DB.con` gives the calling thread its own connection
(WAL mode, so readers never block the writer). Writes go through `DB.tx()`, which runs
BEGIN IMMEDIATE ... COMMIT (ROLLBACK on any exception); a second writer waits up to
BUSY_TIMEOUT_MS. `tx()` may be nested in one thread: the inner block joins the outer one.
Connections of threads that ended stay open until `DB.close()`: use long-lived worker threads.

Usage:
    MIGRATIONS = [(1, 'CREATE TABLE runs(id INTEGER PRIMARY KEY, at TEXT)'),
                  (2, lambda con: con.execute('ALTER TABLE runs ADD COLUMN ok INTEGER'))]
    d = db.open('/data/ih.db', MIGRATIONS, snapshot_dir='/data/backup')
    kv = db.KV(d, 'trust')                       # same load/save/update calls as ihlib.state.Store
    db.import_legacy_json(d, '/data/trust/state.json', kv, '/data/trash')
    db.purge_trash('/data/trash')
    db.snapshot('/data/ih.db', '/data/backup')

Migrations: a list of (version, sql-or-callable), versions rising from 1. Each step runs in one
transaction together with its schema_version row: a crash or an error leaves the old version.
NEVER edit a released step; add a new one. A file with a NEWER version than the code knows
(add-on rolled back) is refused with SchemaTooNewError. A damaged file is moved aside
(<name>.corrupt-<time>) and CorruptError is raised; a new empty file is never made in its place
until someone restores a snapshot or passes accept_fresh=True.
"""
import io, json, os, re, shutil, sqlite3, threading, time
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import quote

DB_FILE_NAME = 'ih.db'                 # the one database file of an add-on, in its /data
BUSY_TIMEOUT_MS = 5000                 # how long a writer waits for another writer
MS_PER_SECOND = 1000
SECONDS_PER_DAY = 86400
TRASH_KEEP_DAYS = 30                   # old JSON files stay this long in the trash folder
SNAPSHOT_KEEP = 3                      # snapshots kept per database
FILE_MODE = 0o600                      # same as ihlib.state.Store
DIR_MODE = 0o700
SYNCHRONOUS = 'NORMAL'                 # safe with WAL; fewer disk syncs than FULL
INTEGRITY_OK = 'ok'
CORRUPT_MARK = '.corrupt-'
SIDE_SUFFIXES = ('-wal', '-shm')       # SQLite side files that belong to a database file
SNAPSHOT_SUFFIX = '.db'
TMP_PREFIX = '.tmp-'
STAMP_FORMAT = '%Y%m%dT%H%M%SZ'
DEFAULT_KV_NAMESPACE = ''
DEFAULT_KV_NAME = 'state'              # Store.load() default name
KV_NAME_RE = re.compile(r'[A-Za-z0-9_.-]{1,60}')   # same rule as ihlib.state.Store
IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,62}')
JSONL_SUFFIX = '.jsonl'
INTERNAL_TABLES = ('schema_version', 'kv', 'ih_legacy_import')

_SCHEMA_SQL = 'CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)'
_KV_SQL = ('CREATE TABLE IF NOT EXISTS kv(namespace TEXT NOT NULL, name TEXT NOT NULL, json TEXT NOT NULL, '
           'updated_at TEXT NOT NULL, PRIMARY KEY(namespace, name)) WITHOUT ROWID')
_LEGACY_SQL = ('CREATE TABLE IF NOT EXISTS ih_legacy_import(source TEXT PRIMARY KEY, target TEXT NOT NULL, '
               'rows INTEGER NOT NULL, imported_at TEXT NOT NULL) WITHOUT ROWID')


class DBError(Exception):
    """Base class for every error this module raises on purpose."""


class SchemaTooNewError(DBError):
    """The file was written by newer code than this. Install the newer add-on version or restore a snapshot."""


class MigrationError(DBError):
    """A migration step failed and was rolled back; the file is still at the version before that step."""


class CorruptError(DBError):
    """The file is damaged. `quarantined` is where it was moved to (or found). Nothing was recreated."""
    def __init__(self, message, path, quarantined):
        super().__init__(message)
        self.path, self.quarantined = path, quarantined


class LegacyImportError(DBError):
    """The old JSON file could not be imported. It was left where it was; nothing was stored."""


class SnapshotError(DBError):
    """The snapshot could not be made or failed its integrity check. No snapshot file was left behind."""


def _iso(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec='seconds')


def _stamp(now):
    return datetime.fromtimestamp(now, timezone.utc).strftime(STAMP_FORMAT)


def _statements(sql):
    """Split a text with several SQL statements. Needed because executescript() commits first, which would break the transaction."""
    buf = ''
    for piece in sql.split(';'):
        buf += piece + ';'
        if sqlite3.complete_statement(buf):
            if buf.strip(' \t\r\n;'):
                yield buf.strip()
            buf = ''
    rest = buf.strip(' \t\r\n;')
    if rest:
        yield rest


def _check_migrations(migrations):
    out, last = [], 0
    for item in migrations or ():
        version, step = item
        if not isinstance(version, int) or isinstance(version, bool) or version <= last:
            raise ValueError('migration versions must be whole numbers rising from 1 (got %r after %r)' % (version, last))
        if not (isinstance(step, str) or callable(step)):
            raise ValueError('migration %d must be SQL text or a callable(con)' % version)
        out.append((version, step))
        last = version
    return out


def quarantined(path):
    """-> sorted list of damaged copies of `path` that were moved aside earlier."""
    folder, base = os.path.dirname(os.path.abspath(path)), os.path.basename(path)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(os.path.join(folder, n) for n in names
                  if n.startswith(base + CORRUPT_MARK) and not n.endswith(SIDE_SUFFIXES))


def _quarantine(path, now):
    """Move the damaged file (and its -wal/-shm) aside. -> new path of the main file."""
    base, n = path + CORRUPT_MARK + _stamp(now), 0
    dest = base
    while os.path.lexists(dest):
        n += 1
        dest = '%s-%d' % (base, n)
    os.replace(path, dest)
    for suffix in SIDE_SUFFIXES:
        if os.path.lexists(path + suffix):
            os.replace(path + suffix, dest + suffix)
    return dest


class DB:
    """One SQLite file. Make it with open()/open_db(). See the module text for the thread rules."""

    def __init__(self, path, migrations=(), busy_timeout_ms=BUSY_TIMEOUT_MS, clock=time.time,
                 snapshot_dir=None, accept_fresh=False):
        self.path = os.path.abspath(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.clock = clock
        self.migrations = _check_migrations(migrations)
        self._local = threading.local()
        self._all, self._all_lock, self._closed = [], threading.Lock(), False
        self._first_open(accept_fresh)
        try:
            self.migrate(snapshot_dir=snapshot_dir)
        except BaseException:
            self.close()
            raise

    # ---- connections
    def _connect(self):
        con = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / MS_PER_SECOND,
                              isolation_level=None, check_same_thread=False)
        try:
            con.execute('PRAGMA busy_timeout=%d' % int(self.busy_timeout_ms))
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('PRAGMA synchronous=%s' % SYNCHRONOUS)
            con.execute('PRAGMA foreign_keys=ON')
        except BaseException:
            con.close()
            raise
        return con

    def _first_open(self, accept_fresh):
        os.makedirs(os.path.dirname(self.path), mode=DIR_MODE, exist_ok=True)
        existed = os.path.exists(self.path)
        if not existed and not accept_fresh:
            old = quarantined(self.path)
            if old:
                raise CorruptError('%s is missing and a damaged copy was moved aside earlier (%s). Restore a snapshot to %s, '
                                   'or open with accept_fresh=True to start empty.' % (self.path, old[-1], self.path), self.path, old[-1])
        con = None
        try:
            con = self._connect()
            result = con.execute('PRAGMA quick_check').fetchone()[0]
            if result != INTEGRITY_OK:
                raise sqlite3.DatabaseError('quick_check: %s' % result)
        except sqlite3.DatabaseError as e:
            if con is not None:
                con.close()
            if isinstance(e, sqlite3.OperationalError) or not existed:
                # locked, read-only folder, disk full...: not proof of damage, so the file is not touched
                raise DBError('cannot open %s: %s' % (self.path, e)) from e
            dest = _quarantine(self.path, self.clock())
            raise CorruptError('%s is damaged (%s). It was moved to %s. Nothing was recreated: restore a snapshot.'
                               % (self.path, e, dest), self.path, dest) from e
        if not existed:
            os.chmod(self.path, FILE_MODE)
        self._local.con = con
        self._all.append(con)

    @property
    def con(self):
        """This thread's own connection (autocommit; use tx() for writes that belong together)."""
        con = getattr(self._local, 'con', None)
        if con is None:
            if self._closed:
                raise DBError('database is closed')
            con = self._connect()
            self._local.con = con
            with self._all_lock:
                self._all.append(con)
        return con

    def close(self):
        """Close every thread's connection. Call once, when no thread is using the database any more."""
        with self._all_lock:
            cons, self._all, self._closed = self._all, [], True
        for con in cons:
            try:
                con.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- transactions and queries
    @contextmanager
    def tx(self):
        """Write transaction for this thread: BEGIN IMMEDIATE, COMMIT at the end, ROLLBACK on any exception. Yields the connection."""
        con = self.con
        depth = getattr(self._local, 'depth', 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield con
            finally:
                self._local.depth = depth
            return
        con.execute('BEGIN IMMEDIATE')
        self._local.depth = 1
        try:
            yield con
        except BaseException:
            self._local.depth = 0
            con.execute('ROLLBACK')
            raise
        self._local.depth = 0
        con.execute('COMMIT')

    def execute(self, sql, params=()):
        """One write statement in its own transaction (or inside the open tx()). -> cursor."""
        with self.tx() as con:
            return con.execute(sql, params)

    def query(self, sql, params=()):
        """-> list of rows (tuples)."""
        return self.con.execute(sql, params).fetchall()

    # ---- migrations
    def schema_version(self):
        """-> highest applied migration version (0 for a new file)."""
        self.con.execute(_SCHEMA_SQL)
        return self.con.execute('SELECT COALESCE(MAX(version), 0) FROM schema_version').fetchone()[0]

    def migrate(self, migrations=None, snapshot_dir=None):
        """Apply the steps this file does not have yet, in order, one transaction each. -> list of versions applied now.
        snapshot_dir: a snapshot is taken there before the first step when the file already holds a schema."""
        if migrations is not None:
            self.migrations = _check_migrations(migrations)
        known = self.migrations[-1][0] if self.migrations else 0
        current = self.schema_version()
        if current > known:
            raise SchemaTooNewError('%s has schema version %d but this code only knows %d. Install the newer add-on version '
                                    'or restore a snapshot. Nothing was changed.' % (self.path, current, known))
        pending = [m for m in self.migrations if m[0] > current]
        if pending and current > 0 and snapshot_dir:
            snapshot(self.path, snapshot_dir, clock=self.clock)
        applied = []
        for version, step in pending:
            try:
                with self.tx() as con:
                    # read again inside the write lock: another thread or process may have done this step
                    if con.execute('SELECT COALESCE(MAX(version), 0) FROM schema_version').fetchone()[0] >= version:
                        continue
                    if callable(step):
                        step(con)
                    else:
                        for statement in _statements(step):
                            con.execute(statement)
                    con.execute('INSERT INTO schema_version(version, applied_at) VALUES(?, ?)', (version, _iso(self.clock())))
            except DBError:
                raise
            except Exception as e:
                raise MigrationError('migration %d of %s failed and was rolled back (still at version %d): %s'
                                     % (version, self.path, self.schema_version(), e)) from e
            applied.append(version)
        return applied

    def table_counts(self):
        """-> {table name: row count} for every table."""
        return _table_counts(self.con)


def _table_counts(con):
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {n: con.execute('SELECT COUNT(*) FROM "%s"' % n.replace('"', '""')).fetchone()[0] for n in names}


def open_db(path, migrations=(), busy_timeout_ms=BUSY_TIMEOUT_MS, clock=time.time, snapshot_dir=None, accept_fresh=False):
    """Open (or create) the database, check it, apply pending migrations. -> DB.
    Raises SchemaTooNewError, MigrationError, CorruptError (file moved aside, never recreated) or DBError."""
    return DB(path, migrations, busy_timeout_ms=busy_timeout_ms, clock=clock, snapshot_dir=snapshot_dir, accept_fresh=accept_fresh)


class KV:
    """Small JSON state in the `kv` table. Same calls and answers as ihlib.state.Store (load/save/update),
    plus get/set/delete/items/mutate. One namespace per old Store folder (for example the plugin name).
    Every write is one transaction; update() and mutate() read and write under the write lock, so
    two threads can no longer lose each other's change (the old file Store could). No secrets here."""

    def __init__(self, db, namespace=DEFAULT_KV_NAMESPACE):
        self.db, self.namespace = db, namespace
        db.con.execute(_KV_SQL)

    @staticmethod
    def _check(name):
        if not isinstance(name, str) or not KV_NAME_RE.fullmatch(name) or name.startswith('.'):
            raise ValueError('bad state name')
        return name

    def _read(self, con, name, default):
        row = con.execute('SELECT json FROM kv WHERE namespace=? AND name=?', (self.namespace, name)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def _write(self, con, name, obj):
        text = json.dumps(obj)       # raises TypeError before anything is written: the old value stays
        con.execute('INSERT INTO kv(namespace, name, json, updated_at) VALUES(?, ?, ?, ?) '
                    'ON CONFLICT(namespace, name) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at',
                    (self.namespace, name, text, _iso(self.db.clock())))

    # Store-compatible
    def load(self, name=DEFAULT_KV_NAME, default=None):
        """-> stored value; {} (or `default`) when there is none. Like Store.load."""
        return self._read(self.db.con, self._check(name), {} if default is None else default)

    def save(self, name, obj):
        """Store the value. -> path of the database file (Store.save returned the JSON file path)."""
        self._check(name)
        with self.db.tx() as con:
            self._write(con, name, obj)
        return self.db.path

    def update(self, name, **changes):
        """Read the dict, apply the changes, store it, all in one transaction. -> the new dict."""
        return self.mutate(name, lambda d: (d.update(changes), d)[1])

    # extra
    def mutate(self, name, fn, default=None):
        """Atomic read-change-write: stores fn(old value) and returns it. `default` (else {}) is the old value when none is stored."""
        self._check(name)
        with self.db.tx() as con:
            new = fn(self._read(con, name, {} if default is None else default))
            self._write(con, name, new)
        return new

    def get(self, name, default=None):
        """-> stored value or `default` (None when missing, unlike load)."""
        return self._read(self.db.con, self._check(name), default)

    def set(self, name, obj):
        self.save(name, obj)

    def delete(self, name):
        """-> True when a value was removed."""
        self._check(name)
        with self.db.tx() as con:
            return con.execute('DELETE FROM kv WHERE namespace=? AND name=?', (self.namespace, name)).rowcount > 0

    def items(self):
        """-> list of (name, value) in this namespace, sorted by name."""
        rows = self.db.con.execute('SELECT name, json FROM kv WHERE namespace=? ORDER BY name', (self.namespace,)).fetchall()
        return [(n, json.loads(j)) for n, j in rows]


# ---- legacy JSON import and the trash folder
def _read_legacy(path, skip_bad_lines):
    with io.open(path, encoding='utf-8') as f:
        if not path.endswith(JSONL_SUFFIX):
            return json.load(f)
        rows = []
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                if not skip_bad_lines:
                    raise ValueError('line %d is not JSON' % number)
        return rows


def _insert_rows(con, table, data):
    if not IDENT_RE.fullmatch(table):
        raise ValueError('bad table name')
    columns = [r[1] for r in con.execute('PRAGMA table_info("%s")' % table)]
    if not columns:
        raise ValueError('table %s does not exist (create it in a migration first)' % table)
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise ValueError('a table import needs a list of objects')
    for row in data:
        unknown = [k for k in row if k not in columns]
        if unknown:
            raise ValueError('no column in %s for key(s) %s' % (table, ', '.join(sorted(map(str, unknown)))))
        keys = list(row)
        if not keys:
            con.execute('INSERT INTO "%s" DEFAULT VALUES' % table)
            continue
        values = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in (row[k] for k in keys)]
        con.execute('INSERT INTO "%s"(%s) VALUES(%s)' % (table, ', '.join('"%s"' % k for k in keys), ', '.join('?' * len(keys))), values)
    return len(data)


def move_to_trash(path, trash_dir, clock=time.time):
    """Move a file into the trash folder and stamp it with the time NOW (purge_trash counts from that time). -> new path."""
    os.makedirs(trash_dir, mode=DIR_MODE, exist_ok=True)
    now = clock()
    dest = os.path.join(trash_dir, os.path.basename(path))
    if os.path.lexists(dest):
        dest = '%s.%s' % (dest, _stamp(now))
    shutil.move(path, dest)
    os.utime(dest, (now, now))
    return dest


def import_legacy_json(db, path, target, trash_dir, name=None, skip_bad_lines=False):
    """Import one old .json or .jsonl file ONCE, then move it into `trash_dir`. Safe to call on every start.
    target: a KV (the whole file becomes one value, named `name` or the file name without .json);
            a table name (the file is a list of objects, or .jsonl with one object per line; keys must be columns;
            dict/list values are stored as JSON text); or a callable(con, data) -> row count for anything else.
    The import and its marker row are one transaction. A crash after it and before the move is finished by the next call.
    A broken file raises LegacyImportError and is left in place.
    -> {'status': 'imported' | 'already' | 'missing', 'rows': n, 'trash': path or None}"""
    source = os.path.abspath(path)
    db.con.execute(_LEGACY_SQL)
    done = db.con.execute('SELECT rows FROM ih_legacy_import WHERE source=?', (source,)).fetchone()
    if done is None and not os.path.isfile(source):
        return {'status': 'missing', 'rows': 0, 'trash': None}
    status, rows = 'already', done[0] if done else 0
    if done is None:
        try:
            data = _read_legacy(source, skip_bad_lines)
        except (OSError, ValueError) as e:
            raise LegacyImportError('cannot read %s: %s. The file was left in place.' % (source, e)) from e
        try:
            with db.tx() as con:
                if con.execute('SELECT 1 FROM ih_legacy_import WHERE source=?', (source,)).fetchone() is None:
                    if isinstance(target, KV):
                        key = name or os.path.splitext(os.path.basename(source))[0]
                        target._check(key)
                        target._write(con, key, data)
                        rows, label = 1, 'kv:%s/%s' % (target.namespace, key)
                    elif callable(target):
                        rows, label = int(target(con, data) or 0), 'callable'
                    else:
                        rows, label = _insert_rows(con, target, data), 'table:%s' % target
                    con.execute('INSERT INTO ih_legacy_import(source, target, rows, imported_at) VALUES(?, ?, ?, ?)',
                                (source, label, rows, _iso(db.clock())))
                    status = 'imported'
        except DBError:
            raise
        except Exception as e:
            raise LegacyImportError('cannot import %s: %s. Nothing was stored; the file was left in place.' % (source, e)) from e
    trash = move_to_trash(source, trash_dir, db.clock) if os.path.isfile(source) else None
    return {'status': status, 'rows': rows, 'trash': trash}


def purge_trash(trash_dir, days=TRASH_KEEP_DAYS, clock=time.time):
    """Delete files that have been in the trash folder for more than `days`. Only plain files directly in the folder. -> sorted names removed."""
    limit, removed = clock() - days * SECONDS_PER_DAY, []
    try:
        entries = list(os.scandir(trash_dir))
    except OSError:
        return removed
    for entry in entries:
        if entry.is_file(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_mtime < limit:
            os.unlink(entry.path)
            removed.append(entry.name)
    return sorted(removed)


# ---- snapshots
def _snapshot_names(dest_dir, stem):
    try:
        names = os.listdir(dest_dir)
    except OSError:
        return []
    return sorted(n for n in names if n.startswith(stem + '-') and n.endswith(SNAPSHOT_SUFFIX))


def snapshot(db_path, dest_dir, keep=SNAPSHOT_KEEP, clock=time.time, busy_timeout_ms=BUSY_TIMEOUT_MS):
    """Copy the live database with the SQLite backup API (safe while the add-on runs), run integrity_check on the copy,
    then keep only the newest `keep` snapshots. The copy is one plain file (no -wal). -> path of the new snapshot.
    Raises SnapshotError when the source is missing or the copy is not sound (the bad copy is deleted)."""
    if keep < 1:
        raise ValueError('keep must be at least 1')
    db_path = os.path.abspath(db_path)
    if not os.path.isfile(db_path):
        raise SnapshotError('no database at %s' % db_path)
    os.makedirs(dest_dir, mode=DIR_MODE, exist_ok=True)
    stem = os.path.splitext(os.path.basename(db_path))[0]
    final = os.path.join(dest_dir, '%s-%s%s' % (stem, _stamp(clock()), SNAPSHOT_SUFFIX))
    tmp = os.path.join(dest_dir, '%s%s-%d-%d' % (TMP_PREFIX, stem, os.getpid(), threading.get_ident()))
    src = dst = None
    try:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        src = sqlite3.connect('file:%s?mode=rw' % quote(db_path), uri=True, timeout=busy_timeout_ms / MS_PER_SECOND)
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        dst.execute('PRAGMA journal_mode=DELETE')
        result = dst.execute('PRAGMA integrity_check').fetchone()[0]
        dst.close()
        dst = None
        if result != INTEGRITY_OK:
            raise SnapshotError('snapshot of %s failed integrity_check: %s' % (db_path, result))
        os.chmod(tmp, FILE_MODE)
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, final)
    except sqlite3.Error as e:
        raise SnapshotError('cannot snapshot %s: %s' % (db_path, e)) from e
    finally:
        for con in (src, dst):
            if con is not None:
                con.close()
        for leftover in (tmp, tmp + '-journal'):
            if os.path.lexists(leftover):
                os.unlink(leftover)
    for old in _snapshot_names(dest_dir, stem)[:-keep]:
        os.unlink(os.path.join(dest_dir, old))
    return final


def restore_check(snapshot_path):
    """Open a snapshot READ-ONLY (it is never changed), run integrity_check and count the rows of every table.
    Never raises for a bad file. -> {'ok': bool, 'integrity': text, 'schema_version': n, 'tables': {name: rows}, 'error': text or None}"""
    out = {'ok': False, 'integrity': None, 'schema_version': None, 'tables': {}, 'error': None}
    if not os.path.isfile(snapshot_path):
        out['error'] = 'no such file'
        return out
    con = None
    try:
        con = sqlite3.connect('file:%s?mode=ro&immutable=1' % quote(os.path.abspath(snapshot_path)), uri=True)
        out['integrity'] = con.execute('PRAGMA integrity_check').fetchone()[0]
        out['tables'] = _table_counts(con)
        if 'schema_version' in out['tables']:
            out['schema_version'] = con.execute('SELECT COALESCE(MAX(version), 0) FROM schema_version').fetchone()[0]
        out['ok'] = out['integrity'] == INTEGRITY_OK
    except sqlite3.Error as e:
        out['error'] = str(e)
    finally:
        if con is not None:
            con.close()
    return out


open = open_db   # db.open(path, ...) as in the plan; the module uses io.open for files
