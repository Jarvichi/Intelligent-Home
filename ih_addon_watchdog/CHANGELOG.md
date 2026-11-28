# Changelog

## [0.1.5] - 2026-11-27
- Add five themes with shared colours and visual details.

## [0.1.4] - 2026-11-27
- Apply the active Intelligent Home appearance across add-ons.

## [0.1.3] - 2026-11-27
- Update the shared Intelligent Home visual theme and components.

## 0.1.2

- Retry incident task creation after outages and report filing failures accurately. Prefer health endpoints and detect HTTP server errors.

## [0.1.1] - 2026-11-26

### Fixed
- Media probe gave false failures on a working HomePod (and needlessly reloaded its config entry): the 1 s silent clip had finished before the first state check. The clip is now 5 s (an existing short file is replaced), the state is polled every 0.25 s, and a state change counts as success as well as 'playing'. A player that is playing or buffering is skipped (never interrupted) and checked again next cycle.

## [0.1.0] - 2026-11-26

### Added
- First version: probe loop (Supervisor state plus HTTP on the ingress port), restart with cool-down and hourly limit, never-restart list, log excerpts in its own database, one high task per add-on, silent-clip check of HomePod players, UI page. DRY by default.
