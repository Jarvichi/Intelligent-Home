"""ih_jobs' own SQLite database: /data/ih.db (one file per add-on, see ihlib/db.py). Stdlib only.

It holds what a PERSON told this add-on, so far the bus deck overrides: "bus OP-12345 is a single-decker". Nothing secret goes in here
(tokens stay in the add-on options). Logs and rebuildable caches stay files.

A database that cannot open never stops a job: reads fall back to the old plain file (/data/bus_watch/deck_overrides.json) and writes are refused
with the reason, so nobody believes a lost answer was saved. The old file is moved to /data/trash/ (deleted after 30 days) only once its rows are
safely in the database, and only in live mode (dry mode writes nothing to disk)."""
import logging, os, re, threading, time
from datetime import datetime, timezone

from ihlib import db as ihdb

DB_NAME = ihdb.DB_FILE_NAME
TRASH_DIR = 'trash'                    # old JSON files wait here, deleted by ihdb.purge_trash after ihdb.TRASH_KEEP_DAYS
BACKUP_DIR = 'backup'                  # snapshots, newest ihdb.SNAPSHOT_KEEP kept
LEGACY_PLUGIN = 'bus_watch'            # old file: <data>/bus_watch/deck_overrides.json
LEGACY_FILE = 'deck_overrides.json'
DECKS = ('single', 'double')           # what can be stored; 'unknown' clears an answer
CODE_RE = re.compile(r'(?:[A-Za-z]+-)?(\d{1,8})')   # 'OP-12345' or '12345'
BY_MAX = 40
SNAPSHOT_MIN_AGE_S = 20 * 3600         # a snapshot younger than this counts as "done for tonight"
ERROR_TEXT_MAX = 300
MAINTAIN_EVERY_S = 3600                # housekeeping looks at most this often

MIGRATIONS = [
    (1, "CREATE TABLE bus_deck_overrides(code TEXT PRIMARY KEY, deck TEXT NOT NULL CHECK(deck IN ('single','double')), "
        "set_at TEXT NOT NULL, set_by TEXT NOT NULL)"),
]

_LOG = logging.getLogger('ih_jobs')


def _short(e):
    return ('%s: %s' % (type(e).__name__, e))[:ERROR_TEXT_MAX]


def fleet_number(code):
    """'OP-12345' -> '12345' (the key the plugin uses everywhere); None when it is not a fleet code."""
    m = CODE_RE.fullmatch(str(code or '').strip())
    return m.group(1) if m else None


class JobsDB:
    def __init__(self, data_dir, clock=time.time, log=None, legacy_dir=None):
        self.data_dir, self.clock, self.log = data_dir, clock, log or _LOG.info
        self.path = os.path.join(data_dir, DB_NAME)
        self.trash_dir = os.path.join(data_dir, TRASH_DIR)
        self.backup_dir = os.path.join(data_dir, BACKUP_DIR)
        self.legacy = os.path.join(legacy_dir or os.path.join(data_dir, LEGACY_PLUGIN), LEGACY_FILE)
        self._maint_at = 0
        self.db = None
        self.error = None                  # why the database is unusable (None = fine or not opened yet)
        self.imported = 0
        self.import_error = None
        self._lock = threading.Lock()

    # ---- opening
    def ready(self, live=True):
        """Open the database once (and, in live mode, import the old file). -> True when the database can be used."""
        with self._lock:
            if self.db is None and self.error is None:
                try:
                    self.db = ihdb.open(self.path, MIGRATIONS, snapshot_dir=self.backup_dir, clock=self.clock)
                except Exception as e:
                    self.error = _short(e)
                    self.log('jobsdb: DATABASE UNUSABLE, bus deck answers fall back to the old file: %s' % self.error)
            if self.db is not None and live:
                self._import_legacy()
            return self.db is not None

    def _put(self, con, data):
        if not isinstance(data, dict):
            raise ValueError('deck_overrides.json is not an object')
        n = 0
        for code, deck in data.items():
            num = fleet_number(code)
            if num and deck in DECKS:      # 'unknown' and junk were ignored by the old reader too
                con.execute('INSERT OR IGNORE INTO bus_deck_overrides(code, deck, set_at, set_by) VALUES(?, ?, ?, ?)',
                            (num, deck, self._now(), 'imported'))
                n += 1
            elif deck != 'unknown':
                self.log('jobsdb: ignored deck_overrides.json entry %r: %r (kept in the trash copy)' % (str(code)[:20], str(deck)[:20]))
        return n

    def _import_legacy(self):
        try:
            r = ihdb.import_legacy_json(self.db, self.legacy, self._put, self.trash_dir)
        except ihdb.LegacyImportError as e:
            self.import_error = _short(e)
            self.log('jobsdb: legacy import FAILED, file left in place: %s' % self.import_error)
            return
        if r['status'] == 'imported':
            self.imported += r['rows']
            self.import_error = None
            self.log('jobsdb: imported %d bus deck answers from deck_overrides.json, old file moved to trash' % r['rows'])
            self.snapshot()

    def _now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    def _legacy_file(self):
        try:
            import json
            with open(self.legacy) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    # ---- reads
    def overrides(self, live=True):
        """{'12345': 'single', ...}. From the database when it works (plus an old file not imported yet, e.g. in dry mode), else from the old file."""
        out = {}
        for code, deck in self._legacy_file().items():
            num = fleet_number(code)
            if num and deck in DECKS:
                out[num] = deck
        if self.ready(live):
            try:
                for code, deck in self.db.query('SELECT code, deck FROM bus_deck_overrides'):
                    out[code] = deck           # the database wins over an old file
            except Exception as e:
                self.error = _short(e)
        return out

    def override(self, code, live=True):
        num = fleet_number(code)
        return self.overrides(live).get(num) if num else None

    # ---- writes (a person's answer: the API calls these)
    def set_override(self, code, deck, by='api'):
        """Store 'single' or 'double' for a bus; 'unknown' removes the answer. -> (ok, message). Never raises."""
        num = fleet_number(code)
        deck = str(deck or '').strip().lower()
        if not num:
            return False, 'Choose a fleet code like OP-12345.'
        if deck not in DECKS + ('unknown',):
            return False, 'Choose single, double or unknown.'
        if not self.ready(True) or self.db is None:
            return False, 'The add-on database is unavailable (%s). Nothing was saved.' % (self.error or 'not open')
        try:
            if deck == 'unknown':
                self.db.execute('DELETE FROM bus_deck_overrides WHERE code=?', (num,))
            else:
                self.db.execute('INSERT OR REPLACE INTO bus_deck_overrides(code, deck, set_at, set_by) VALUES(?, ?, ?, ?)',
                                (num, deck, self._now(), re.sub(r'[^A-Za-z0-9 _.-]', '', str(by))[:BY_MAX] or 'api'))
        except Exception as e:
            self.error = _short(e)
            return False, 'Could not save: %s' % self.error
        return True, 'Saved %s as %s' % (num, deck)

    # ---- nightly housekeeping (cheap; called from the bus_watch pass, at most once a night does any work)
    def maintain(self):
        if self.db is None or self.clock() - self._maint_at < MAINTAIN_EVERY_S:
            return
        self._maint_at = self.clock()
        try:
            ihdb.purge_trash(self.trash_dir, clock=self.clock)
            names = ihdb._snapshot_names(self.backup_dir, os.path.splitext(DB_NAME)[0])
            newest = os.path.getmtime(names[-1]) if names else 0
            if self.clock() - newest > SNAPSHOT_MIN_AGE_S:
                self.snapshot()
        except Exception as e:
            self.log('jobsdb: housekeeping failed: %s' % _short(e))

    def snapshot(self):
        try:
            snap = ihdb.snapshot(self.path, self.backup_dir, clock=self.clock)
            ihdb.restore_check(snap)
        except Exception as e:
            self.log('jobsdb: snapshot failed: %s' % _short(e))

    def status(self):
        return {'db': self.db is not None, 'error': self.error, 'imported': self.imported, 'import_error': self.import_error,
                'schema': self.db.schema_version() if self.db is not None else None}


_SHARED = {}
_SHARED_LOCK = threading.Lock()


def shared(data_dir, log=None, legacy_dir=None):
    """One JobsDB per data folder for the whole process (plugins and the HTTP handler share it)."""
    with _SHARED_LOCK:
        key = (data_dir, legacy_dir)
        if key not in _SHARED:
            _SHARED[key] = JobsDB(data_dir, log=log, legacy_dir=legacy_dir)
        return _SHARED[key]


def close_all():
    """Close every shared database (tests, shutdown)."""
    with _SHARED_LOCK:
        items = list(_SHARED.values())
        _SHARED.clear()
    for j in items:
        if j.db is not None:
            try:
                j.db.close()
            except Exception:
                pass
            j.db = None
