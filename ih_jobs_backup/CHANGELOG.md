# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

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
