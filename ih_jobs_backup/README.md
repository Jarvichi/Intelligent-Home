# Intelligent Home - Jobs - Backup (ih_jobs_backup)

A child of the **Intelligent Home - Jobs** add-on (`ih_jobs`). It runs one hourly job and writes Home Assistant sensors:

- `sensor.backup_storage_local`: used GB, backups versus other files, free space.
- `sensor.backup_storage_drive` (only if you name the two drive sensors): the same for Google Drive.

It starts in **dry-run** (logs "would set ...", changes nothing) and maps no folders.

## How it joins the parent
Give this add-on and the parent the **same** `register_key` (any long random text, set in each Configuration tab). Every 30 seconds the child posts a signed registration to the parent (`parent_url`). The parent then lists it (`GET /children`) and forwards scheduled runs to it. The child keeps working if the parent is stopped (it runs once 90 s after start, and every `own_timer_min` minutes if you set that above 0). With no `register_key` the link is off.

## Going live
Acts only when `mode` is `live`, `old_loops_disabled` is on (you stopped any older script doing the same) and `/data/STOP_LIVE` does not exist (kill switch).

## Disk size (decision for the owner)
The Supervisor may refuse `host/info` (the disk size), which used to make the job fail. Now the disk figure is taken from, in order:
1. `disk_total_entity` + `disk_used_entity`: two Home Assistant sensors in the same unit family (for example from the System Monitor integration). No extra permission needed.
2. The Supervisor `host/info` (switch off with `host_info: false` to stop the refused calls).
3. A `/backup` folder, if one exists.

If none works the job does **not** fail: the sensor shows the backups size only (`disk_known: false`, no total/free/percent). This add-on never asks for more permissions.

## Options (examples are fake; see `options.example.json`)
| Option | Meaning |
|---|---|
| `mode` | `dry` or `live` |
| `old_loops_disabled` | You switched older scripts off |
| `parent_url` | The parent add-on (default: Supervisor gateway, port 8792) |
| `callback_url` | How the parent reaches this child (empty = Supervisor gateway and this add-on's port 8798) |
| `register_key` | Shared secret with the parent (secret) |
| `own_timer_min` | Run by itself every N minutes (0 = only when the parent or start-up asks) |
| `drive_used_entity`, `drive_total_entity` | Your two Google Drive storage sensors; empty = no drive sensor |
| `disk_used_entity`, `disk_total_entity` | Optional sensors for the Pi disk (see above) |
| `host_info` | Try the Supervisor `host/info` (default on) |
| `log_payloads` | false = each job run (scheduler, start-up, own timer or parent trigger) logs ONE line with metadata only (run id, trigger, job, chars of job log, ms, ok, why). true = the line also holds the first 200 chars of the trigger and of the last job log line (one line, newlines escaped). Keys and tokens are never logged. |

## Boot and watchdog
Default `boot: manual`. For automatic start set the Supervisor option "Start on boot" and add `watchdog: http://[HOST]:[PORT:8798]/healthz` to `config.yaml` after the dry-run looks right.

## Sources
`jobs_link.py`, `jobs_child.py`, `manifest.json` and `jobs/` are COPIES made by `sync_from_service.sh`.

## Privacy
Run `python3 ../privacy_scan.py` before sharing this folder.
