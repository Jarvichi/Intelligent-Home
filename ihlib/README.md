# ihlib: shared helpers for the Intelligent Home add-ons

Standard library only (Python 3.12 or newer). Each add-on carries its own copy in `app/ihlib/`. Never edit a copy: edit the master, then copy it into the add-ons (`sync_ihlib.sh`), with a version bump and a CHANGELOG entry for every add-on that receives it.

**State on this branch:** this folder holds only the new module `db.py`. It is not copied into any add-on yet and no add-on uses it. The other modules (`ha`, `discord`, `state`, `log`, `http`, `series`, `trace`) are still only in the add-on copies.

## db.py: one SQLite file per add-on

Each add-on owns one file, `/data/ih.db`. There is no shared database server. **No secrets go into the database, ever**: tokens, passwords, keys and PINs stay in the add-on options. Human-readable `.log` files and rebuildable caches stay files.

```python
from ihlib import db

MIGRATIONS = [
    (1, 'CREATE TABLE runs(id INTEGER PRIMARY KEY, at TEXT NOT NULL)'),
    (2, lambda con: con.execute('ALTER TABLE runs ADD COLUMN ok INTEGER')),
]
d = db.open('/data/ih.db', MIGRATIONS, snapshot_dir='/data/backup')
kv = db.KV(d, 'trust')                                    # replaces state.Store('/data/trust')
db.import_legacy_json(d, '/data/trust/state.json', kv, '/data/trash')
db.purge_trash('/data/trash')                             # run once a day
db.snapshot('/data/ih.db', '/data/backup')                # run once a night
```

| Call | What it does |
|---|---|
| `open(path, migrations=(), snapshot_dir=None, accept_fresh=False, clock=time.time)` (also `open_db`) | Opens or creates the file with WAL, `busy_timeout`, `foreign_keys=ON`, `synchronous=NORMAL`, mode 600. Runs `quick_check`. Applies pending migrations. Returns a `DB`. |
| `DB.con` | The calling thread's own connection. |
| `DB.tx()` | Write transaction: `BEGIN IMMEDIATE`, commit at the end, rollback on any exception. Nesting in one thread joins the outer transaction. |
| `DB.execute(sql, params)`, `DB.query(sql, params)` | One write in a transaction; rows of a read. |
| `DB.migrate()`, `DB.schema_version()`, `DB.table_counts()`, `DB.close()` | Apply pending steps; highest applied version; rows per table; close every thread's connection. |
| `KV(db, namespace)` | JSON values in the table `kv`. `load`, `save`, `update` behave like `state.Store`. Extra: `get`, `set`, `delete`, `items`, `mutate(name, fn, default)`. |
| `import_legacy_json(db, path, target, trash_dir, name=None, skip_bad_lines=False)` | Imports an old `.json` or `.jsonl` file once, then moves it into the trash folder. `target` is a `KV`, a table name or a `callable(con, data)`. |
| `move_to_trash(path, trash_dir)`, `purge_trash(trash_dir, days=TRASH_KEEP_DAYS)` | Move a file to the trash folder; delete files that were there longer than 30 days. |
| `snapshot(db_path, dest_dir, keep=SNAPSHOT_KEEP)` | Copies the live file with the SQLite backup API, runs `integrity_check` on the copy, keeps the newest 3. |
| `restore_check(snapshot_path)` | Opens a snapshot read-only. Returns `ok`, `integrity`, `schema_version`, `tables` (rows per table) and `error`. Never raises. |
| `quarantined(path)` | Lists damaged copies that were moved aside. |

### Rules

- **Threads:** one connection per thread. Readers never block the writer (WAL). A second writer waits up to `BUSY_TIMEOUT_MS` (5 s). Use long-lived worker threads: the connection of a thread that ended stays open until `DB.close()`.
- **Migrations:** a list of `(version, SQL text or callable(con))`, versions rising from 1, kept in the add-on's own code. Each step and its `schema_version` row are one transaction, so a crash or an error leaves the old version. Never edit a released step; add a new one. Do not call `executescript()` or `commit()` inside a step: both end the transaction early.
- **Newer file than code:** `SchemaTooNewError`. Nothing is changed. Install the newer add-on version or restore a snapshot.
- **Damaged file:** the file (and its `-wal`/`-shm`) is moved to `<name>.corrupt-<time>` and `CorruptError` is raised. The next start raises the same error. A new empty file is made only after a snapshot is restored or with `accept_fresh=True`. A locked file or a full disk raises `DBError` and moves nothing.
- **KV and Store differences:** `save()` returns the database path, not a JSON file path. `update()` and `mutate()` are atomic (the file Store could lose one of two changes made at the same time). One namespace replaces one Store folder.
- **Legacy import:** the import and its marker row (table `ih_legacy_import`, key = absolute path of the old file) are one transaction. If the old file appears again later, it is moved to the trash folder without being imported. A broken file raises `LegacyImportError` and stays in place.
- **Trash age** counts from the move, not from the last change of the old file.
- **Restore:** stop the add-on, copy a snapshot over `/data/ih.db`, delete `ih.db-wal` and `ih.db-shm`, start the add-on.
- **Limits are named constants** at the top of `db.py` (`BUSY_TIMEOUT_MS`, `TRASH_KEEP_DAYS`, `SNAPSHOT_KEEP`, `FILE_MODE`...).

### Tests

`python3 tests/test_ihlib_db.py` (temp folders only). Covers: migrations once and in order, newer-schema refusal, failed step rollback, a real process killed inside a step, snapshot before an upgrade, KV round trip, 8 writer threads, legacy import (KV, table, `.jsonl`, twice, broken files, crash before the move), trash purge with a fake clock, snapshot + keep + restore_check, snapshot while writing, damaged file handling.
