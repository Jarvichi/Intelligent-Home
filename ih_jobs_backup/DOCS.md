# Intelligent Home - Jobs - Backup

## What it does

Child of Intelligent Home - Jobs: backup storage sensors. Dry-run by default. Child of `ih_jobs`.

## Requires

- `ih_jobs` add-on
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Jobs - Backup**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `register_key` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in dry-run mode, so it changes nothing yet.
5. When the log looks right, set `mode` to `live`.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `mode` | No | `dry` logs what it would do and changes nothing; `live` acts. Default: `dry`. |
| `old_loops_disabled` | No | You switched older scripts off. Default: `false`. |
| `parent_url` | No | Address of the parent add-on. Default: `http://172.30.32.1:8792`. |
| `callback_url` | No | How the parent reaches this add-on. Empty = the Supervisor gateway and this add-on's port. |
| `register_key` | Yes | Shared secret with the parent (secret) |
| `own_timer_min` | No | Run by itself every N minutes (0 = only when the parent or start-up asks). Default: `0`. |
| `drive_used_entity` | No | Your two Google Drive storage sensors; empty = no drive sensor |
| `drive_total_entity` | No | Your two Google Drive storage sensors; empty = no drive sensor |
| `disk_used_entity` | No | Optional sensors for the Pi disk (see above) |
| `disk_total_entity` | No | Optional sensors for the Pi disk (see above) |
| `host_info` | No | Try the Supervisor `host/info` (default on) |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `mode` is `dry`. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Parent does not list this add-on / "cannot reach parent".** `register_key` must be identical in both add-ons, and `parent_url` must point at the parent (default gateway address and port). An empty key turns the link off.

## Privacy

Everything stays on your network, except optional calls to the services you give keys for (for example the Met Office). No personal data is sent.

## More details

### Data storage (database since 0.2.0)

The add-on keeps its history in one SQLite file, `/data/ih.db` (shared helper `ihlib/db.py`, WAL mode). Every file under `/data` was reviewed:

| File under `/data` | Where it lives now | Why |
|---|---|---|
| `options.json` | Stays a file | Supervisor owns it; holds `register_key`. Secrets never go into the database or its snapshots |
| `STOP`, `STOP_LIVE` | Stay files | Kill switches you create or delete by hand |
| Add-on log (container log) | Stays text | Logs stay text |
| (no other state file existed before 0.2.0) | Nothing to migrate | The job only reads Home Assistant and publishes sensors |
| `ih.db` table `backup_samples` | New: one row per run (time, mode, local used GB, backups GB and count, Drive figures) | History for later graphs; rows older than 400 days are deleted |
| `ih.db` key-value `backup/last_run` | New | Time and mode of the last run |
| `trash/` | New folder | Imported degraded files, deleted after 30 days |
| `backup/` | New folder | Nightly snapshots of `ih.db`, newest 3 (these are NOT Home Assistant backups) |
| `degraded/` | New folder, only used when the database cannot be used | Size-limited plain file, imported again after a restore |

The Home Assistant backups themselves (archives) are never stored in this database.

**A database problem never stops a run.** The job reads Home Assistant, publishes the sensors, and only then records its row. Recording never raises, waits at most 8 seconds and is skipped on any failure.

**Start-up:** the add-on opens `ih.db`, applies numbered migrations (a snapshot is taken first when the file already has data), imports a `degraded/` file if one exists (copy, check the rows in the same transaction, then move to `/data/trash`), purges the trash, and from 04:00 takes one snapshot a night, opens the copy read-only and runs the integrity check (counts only in the log).

**Status:** `/status` shows `db`: schema version, rows per table, last snapshot, snapshots kept, last restore check, trash, import errors and `degraded`.

**If the database is damaged or too new:** backup-storage runs continue as normal. Status shows `db_degraded: true`, a `WARNING` and the error; the log says `DATABASE DEGRADED`. History rows go to `/data/degraded/backup_samples.jsonl` (stops growing at 256 KiB). The damaged file is moved aside as `ih.db.corrupt-<time>`; nothing is recreated.

**Restore** (add-on stopped): copy the newest file in `/data/backup` to `/data/ih.db`, delete `ih.db-wal` and `ih.db-shm`, start the add-on. The `degraded/` file is then imported automatically. To go back to 0.1.x: install it; no old files need to be put back.

Untested on the real device: power loss, a restore from a Home Assistant backup, running two copies at once.

### How it joins the parent
Give this add-on and the parent the **same** `register_key` (any long random text, set in each Configuration tab). Every 30 seconds the child posts a signed registration to the parent (`parent_url`). The parent then lists it (`GET /children`) and forwards scheduled runs to it. The child keeps working if the parent is stopped (it runs once 90 s after start, and every `own_timer_min` minutes if you set that above 0). With no `register_key` the link is off.

### Going live
Acts only when `mode` is `live`, `old_loops_disabled` is on (you stopped any older script doing the same) and `/data/STOP_LIVE` does not exist (kill switch).

### Disk size (decision for the owner)
The Supervisor may refuse `host/info` (the disk size), which used to make the job fail. Now the disk figure is taken from, in order:
1. `disk_total_entity` + `disk_used_entity`: two Home Assistant sensors in the same unit family (for example from the System Monitor integration). No extra permission needed.
2. The Supervisor `host/info` (switch off with `host_info: false` to stop the refused calls).
3. A `/backup` folder, if one exists.

If none works the job does **not** fail: the sensor shows the backups size only (`disk_known: false`, no total/free/percent). This add-on never asks for more permissions.
