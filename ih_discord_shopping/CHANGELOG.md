# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.2.0] - 2026-10-29

### Added
- iPhone shopping Shortcut endpoint `POST /shortcut/<token>` on the add-on port: same request formats and JSON reply as the old Home Assistant webhook. Options `shortcut_token` (empty = endpoint off) and `shortcut_dry` (default true: the list is not changed, the reply has `"dry": true`). Logs counts only.

## [0.1.2] - 2026-10-28

### Changed
- Uses the shared ihlib library for Home Assistant REST calls (via core.py). No change in behaviour.

## [0.1.1] - 2026-10-28

### Added
- Shopping list commands as a module of the Discord transport. Shadow mode by default.
