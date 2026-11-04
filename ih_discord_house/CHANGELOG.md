# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.2.7] - 2026-11-04

### Added
- Shared trace library: optional typed span `attrs` (allowlisted keys, int or short-token values, max 8; `Span.set()`), never message text or IDs.

## [0.2.6] - 2026-11-03

### Added
- metadata-only trace spans per command (shared ihlib.trace); off without an ingest token.

## [0.2.4] - 2026-10-31

### Changed
- Prebuilt image, no code change.

## [0.2.3] - 2026-10-30

### Fixed
- Shared shopping feature copy: list ticks complete the open item by its id, never by name (a ticked twin with the same name swallowed ticks).

## [0.2.2] - 2026-10-28

### Changed
- Uses the shared ihlib library for Home Assistant REST calls (via core.py). No change in behaviour.

## [0.2.1] - 2026-10-28

### Added
- House status commands as a module of the Discord transport. Shadow mode by default.

## 0.2.5
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
