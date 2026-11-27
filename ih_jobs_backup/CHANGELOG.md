# Changelog

## [0.2.2] - 2026-11-27
- Apply the active Intelligent Home appearance across add-ons.

## [0.2.1] - 2026-11-27
- Update the shared Intelligent Home visual theme and components.

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.2.0] - 2026-11-12

### Added
- The add-on has its own SQLite database `/data/ih.db` (shared `ihlib/db.py`, schema version 1). Each storage run now also stores one history row (table `backup_samples`: local and Drive used space, backups size and count) and a last-run marker. Rows older than 400 days are deleted.
- A nightly snapshot (from 04:00 local time, newest 3 kept) goes to `/data/backup`, followed by a restore check that logs counts only. `/data/trash` is purged after 30 days.
- `/status` has a new `db` block (schema version, rows, last snapshot, restore check, trash size, import errors) and `db_degraded`.

### Safety
- A database problem can never stop a backup-storage run. The job publishes its sensors first and records its row afterwards, never waits more than 8 seconds for the database and ignores any failure. If the database is damaged, newer than this add-on or cannot be opened, the add-on runs in degraded mode: rows go to a size-limited plain file in `/data/degraded/` and are imported after a restore. The database is never recreated by itself.
- The add-on had no state files before this version, so nothing was migrated. Options, the kill-switch file, the log and the Home Assistant backups themselves stay where they were.

## [0.1.13] - 2026-11-11

### Changed
- ihlib: add db.py (shared SQLite helper, not used yet).

## [0.1.12] - 2026-11-05

### Changed
- The add-on page has a sticky top bar: "‹ Intelligent Home" goes back to the Operations console in the whole Home Assistant window (it used to open inside the page frame, and only from a footer link at the very bottom). The footer link also opens in the whole window.

## [0.1.11] - 2026-11-05

### Changed
- Ingress status page: new "Today" card (local disk and Google Drive space, backup counts, last automatic backup) from a new read-only `/api/today`. Shared page can now show rows without a score and bars without used/cap. No tokens or names are shown.

## [0.1.10] - 2026-11-05

### Changed
- Ingress status page v2: one status line with a Check button, a plain "What it does" list, and a plain-language description.

## [0.1.9] - 2026-11-05

### Changed
- Ingress status page now uses the shared Games-style layout (page heading with service health, status and capabilities cards, collapsible About).

## [0.1.8] - 2026-11-04

### Fixed
- Remove deployment-specific identity examples from tracing source.

## [0.1.7] - 2026-11-04

### Added
- Shared trace library: optional typed span `attrs` (allowlisted keys, int or short-token values, max 8; `Span.set()`), never message text or IDs.

## [0.1.6] - 2026-11-03

### Fixed
- The version reported by the add-on API now matches the add-on version.

## [0.1.5] - 2026-11-03

### Added
- Ingress panel (sidebar page) showing this add-on's status.

## [0.1.4] - 2026-10-31

### Changed
- Prebuilt image, no code change.

## [0.1.3] - 2026-10-28

### Changed
- Updated copy of the shared ihlib library (websocket ping fix). No change in behaviour for this add-on.

## [0.1.2] - 2026-10-28

### Changed

- Uses the shared ihlib library for Home Assistant calls and log masking. No change in behaviour.

## [0.1.1] - 2026-10-28

### Added
- Backup storage sensors as a child of the Jobs add-on. Dry-run by default.

## 0.1.5
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
