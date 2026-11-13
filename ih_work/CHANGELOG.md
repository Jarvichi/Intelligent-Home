# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.3.0] - 2026-11-13

### Added
- Runs. A night run and a day run, each with on/off, start time, stop-by time, pace target and a number of items (or unlimited), set on the page and kept in `/data/run_settings.json` (add-on options `night_run_*` and `day_run_*` give the defaults; night on at 01:00, day off). A Run now button with its own pace target and number of items, a status line and a Stop button. One run at a time.
- While a run is active the backlog feeder queues the next open to-do item (same rules as before) and the gate follows the run's pace target instead of the day and night levels. A run ends at the item count, the pace target (once it has started something; a manual run ends at once if pace is already at or above target), the stop-by time, the 5-hour session or week limit, the STOP switch, an empty backlog, or the Stop button.
- API: `GET /runs`, `POST /runs/settings`, `POST /runs/start`, `POST /runs/stop` (token). The page reaches them through the ingress server, which keeps the token on the server.
- In test mode a run only logs `[DRY] would feed: ...` and counts those as started.

### Unchanged
- Urgent handoffs, leases, the always-on `feed_backlog` option and the day/night gate when no run is active.

## [0.2.2] - 2026-11-11

### Changed
- ihlib: add db.py (shared SQLite helper, not used yet).

## [0.2.1] - 2026-11-09

### Changed
- Rephrase a changelog sentence to remove a private name caught by the publication privacy scan.

## [0.2.0] - 2026-11-08

### Added
- Urgent handoffs. An item of kind `other` whose ref starts with `handoff-` (a request an assistant passed on while a person waits) is claimed first, even ahead of older items, and it skips the pace pause. The STOP switch, the 5-hour session limit (85%), the week limit (90%) and a cap of 30 such claims in 24 hours still apply. Ordinary items are unchanged. The gate override is still not available over the API.

## [0.1.1] - 2026-11-08

### Changed
- Removed the "at most 3 jobs an hour" cap (the queue needs a pace cap, not an item cap). The pace levels, the 5-hour session limit, the week limit and the STOP switch are now the only limits.

## [0.1.0] - 2026-11-05

### Added
- First version. Queue store (one file per item, first in first out) with leases: a helper claims the next job, renews with a heartbeat, and must report it done or failed. A lease that runs out puts the job back; after 3 expired leases it is failed.
- Pace gate as a pure module with a day level (start below -5, stop at -4) and a night level (start below -1, stop at 0), hysteresis, 5-hour session under 85%, week under 90%, one at a time, at most 3 an hour, STOP switch. All levels and the night hours are options.
- Token-protected API: enqueue, list, item, claim, heartbeat, complete, fail, cancel, retry, status, healthz. No way for a caller to bypass the gate.
- Page (ingress) with the shared status layout, a back bar and a Today card (running, next 5 waiting, done and failed today, and why it waits).
- Optional backlog feeder from the Claude to-do list (`feed_backlog`, off by default).
- Starts in test mode (`mode: dry`): the line is kept, nothing is handed out, and the log says what it would do.
