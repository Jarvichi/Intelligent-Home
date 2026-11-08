# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.1.1] - 2026-11-08

### Changed
- Removed the "at most 3 jobs an hour" cap (Andrew: the job needs a pace cap, not an item cap). The pace levels, the 5-hour session limit, the week limit and the STOP switch are now the only limits.

## [0.1.0] - 2026-11-05

### Added
- First version. Queue store (one file per item, first in first out) with leases: a helper claims the next job, renews with a heartbeat, and must report it done or failed. A lease that runs out puts the job back; after 3 expired leases it is failed.
- Pace gate as a pure module with a day level (start below -5, stop at -4) and a night level (start below -1, stop at 0), hysteresis, 5-hour session under 85%, week under 90%, one at a time, at most 3 an hour, STOP switch. All levels and the night hours are options.
- Token-protected API: enqueue, list, item, claim, heartbeat, complete, fail, cancel, retry, status, healthz. No way for a caller to bypass the gate.
- Page (ingress) with the shared status layout, a back bar and a Today card (running, next 5 waiting, done and failed today, and why it waits).
- Optional backlog feeder from the Claude to-do list (`feed_backlog`, off by default).
- Starts in test mode (`mode: dry`): the line is kept, nothing is handed out, and the log says what it would do.
