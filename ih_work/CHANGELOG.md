# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.6.0] - 2026-11-13

### Added
- New run phase REVIEW between PLAN and BLOCK: the run queues one `review_batch` job (ref `review-<run id>-<n>`) and waits for it, at most 60 minutes (cancelled if it never started; a failed job never stops the run). The host runner has Claude check the oldest unreviewed outside-AI answers and records good or wrong; unsure ones stay on the owner's list with a note. The job does not count as a to-do item.
- New run setting `review_batch_size` (0 to 30, default 10, 0 = skip), per run (night, day, Run now): Runs card field "Answers to check per run (0 = skip)", options `night_run_review_batch_size` and `day_run_review_batch_size`, `POST /runs/settings`, optional field in `POST /runs/start`.
- New job kind `review_batch`. Test mode only logs `[DRY] would queue the review job`.

## [0.5.3] - 2026-11-13

### Added
- The feeder skips a to-do with an open `[needs-you #N]` marker (a job only the owner can do or decide). A matching `[done-you #N]` marker clears it.

## [0.5.2] - 2026-11-13

### Added
- A to-do fed to Claude with no plan marker (PLAN, AI job or BUILT) now tells Claude to write the plan first: the outside AIs may have skipped it, refused it or run out of quota. Private items are planned there too, never sent out.

## [0.5.1] - 2026-11-13

### Fixed
- Runs only fed items whose title started with an allowed prefix (IH:, Check:, Review:) or carried a tag, so on a real list almost nothing was eligible and a run ended at once with "backlog empty". A run now feeds any open to-do item except the skip tags (waiting, needs OK, plan, no-overnight) and items fed in the last 7 days, as the 1am worker did. The prefixes still limit the always-on feeder.

## [0.5.0] - 2026-11-13

### Added
- Runs work in phases. DRAIN: while the queue holds queued or running jobs the run feeds nothing; the runner consumes them first. PLAN: once the queue is empty the run queues one `plan_todos` job (free outside AIs write plans for to-dos that lack one; no Claude call) and waits for it, at most 60 minutes. BLOCK: the run queues a block of open to-dos at once, waits until the runner has consumed the block, then starts again with DRAIN. A block that is smaller than asked means the backlog is empty and the run ends after it.
- New run setting `block_size` (1 to 20, default 5): in the Runs card for the night run, the day run and Run now; options `night_run_block_size` and `day_run_block_size`; `POST /runs/settings` and optional `block_size` in `POST /runs/start`.
- The run status and the Runs card show the phase and the block progress, for example "block 2: 3 of 5 done".
- New job kind `plan_todos` (handled by the host runner with the free planner, never by Claude).

### Changed
- "Max to-dos" still counts to-dos fed in total; the last block is cut to the remaining count. Only the to-dos a run fed count as its items (not the planning job and not other jobs).
- When a run ends for any reason, the queued jobs it fed itself (block items not yet started, a waiting plan job) are cancelled by id. Started jobs finish. Other jobs are never touched.
- The pace gate is unchanged: new claims only while pace is under the run's target; a block part done when the target is reached ends the run.
- Test mode (`mode: dry`) logs per phase (`would queue the planning job`, `would feed: ...`, `block N: n to-do(s) would be fed`), queues nothing and does not wait for existing jobs.

## [0.4.0] - 2026-11-13

### Added
- Working now. A card at the top of the page shows each Claude worker that is running: task title, worker, stage (Starting, Assessing, Planning, Implementing, Reporting), a short detail, how long it has run, and a Stalled flag when it has sent no update for 5 minutes. It refreshes every 5 seconds.
- API: `POST /workers/status {worker, stage, title?, ref?, started_at?, detail?, updated_at?}` and `GET /workers` (token). The runner sends a message when its stage or detail changes (at most one every 5 seconds) and a final `idle`, which removes the entry. Entries with no update for 30 minutes are dropped. The add-on keeps the list in memory and a short copy in `/data/workers.json`.

### Unchanged
- The queue, leases, the pace gate and runs.

## [0.3.1] - 2026-11-13

### Changed
- Runs card: pace target is a spinner from -10 to 10 (step 0.5, also enforced by the add-on and its options); the item count is labelled "Max to-dos per run" with a checkbox "No limit" that says what it means.

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
