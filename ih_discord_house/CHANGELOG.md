# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.2.3] - 2026-10-30

### Fixed
- Shared shopping feature copy: list ticks complete the open item by its id, never by name (a ticked twin with the same name swallowed ticks).

## [0.2.2] - 2026-10-28

### Changed
- Uses the shared ihlib library for Home Assistant REST calls (via core.py). No change in behaviour.

## [0.2.1] - 2026-10-28

### Added
- House status commands as a module of the Discord transport. Shadow mode by default.
