# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.1.10] - 2026-11-05

### Changed
- Ingress status page: new "Last test" card (score per AI, date of the last test, next test date, test-mode note) from a new read-only `/api/today` that reads this add-on's own monthly results. Question and answer text is never read out.

## [0.1.9] - 2026-11-05

### Changed
- Ingress status page v2: one status line with a Check button, a plain "What it does" list, and a plain-language description.

## [0.1.8] - 2026-11-05

### Changed
- Ingress status page now uses the shared Games-style layout (page heading with service health, status and capabilities cards, collapsible About).

## [0.1.7] - 2026-11-04

### Fixed
- Remove deployment-specific identity examples from tracing source.

## [0.1.6] - 2026-11-04

### Added
- Shared trace library: optional typed span `attrs` (allowlisted keys, int or short-token values, max 8; `Span.set()`), never message text or IDs.

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
- Monthly scored test of the outside AIs through the AI layer. Dry-run by default.

## 0.1.5
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
