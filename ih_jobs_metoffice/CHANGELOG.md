# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.1.13] - 2026-11-11

### Changed
- ihlib: add db.py (shared SQLite helper, not used yet).

## [0.1.12] - 2026-11-05

### Changed
- The add-on page has a sticky top bar: "‹ Intelligent Home" goes back to the Operations console in the whole Home Assistant window (it used to open inside the page frame, and only from a footer link at the very bottom). The footer link also opens in the whole window.

## [0.1.11] - 2026-11-05

### Changed
- Ingress status page: new "Today" card (today, tomorrow and the next day: temperature, rain chance, forecast age) from a new read-only `/api/today`. Shared page can now show rows without a score and bars without used/cap.

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
- Met Office forecast sensors as a child of the Jobs add-on. Dry-run by default.

## 0.1.5
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
