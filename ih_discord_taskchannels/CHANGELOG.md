# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.1.7] - 2026-11-04

### Fixed
- Remove deployment-specific identity examples from tracing source.

## [0.1.6] - 2026-11-04

### Added
- Shared trace library: optional typed span `attrs` (allowlisted keys, int or short-token values, max 8; `Span.set()`), never message text or IDs.

## [0.1.5] - 2026-11-03

### Added
- metadata-only trace spans per command (shared ihlib.trace); off without an ingest token.

## [0.1.3] - 2026-10-31

### Changed
- Prebuilt image, no code change.

## [0.1.2] - 2026-10-28

### Added
- One private channel per task as a module of the Discord transport. Shadow mode by default.

## 0.1.4
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
