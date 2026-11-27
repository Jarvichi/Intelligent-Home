# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.1.9] - 2026-11-27

### Fixed
- Release database connections when threaded HTTP requests finish and retry failed daily backups.

## [0.1.8] - 2026-11-26

### Added
- Test the current effect sequence on selected devices from the editor, including unsaved changes. Testing requires live control and an inactive event mode.

## [0.1.7] - 2026-11-25

### Added
- Edit installed event plans and review and apply the changes. Detect outside changes to owned rules and resume journalled interrupted updates when ownership can be verified.

## [0.1.6] - 2026-11-25

### Added
- Review and apply a new event from the administrator page when live control is enabled. Existing or partially installed items are not overwritten.

### Fixed
- Respect individual random-effect intervals and time windows, and snapshot a light group when it is the only lighting target.

## [0.1.5] - 2026-11-24

### Added
- Build effects from editable lightning, flicker, colour burst and power-cut presets, with light, sound and wait steps and optional lighting restoration.

## [0.1.4] - 2026-11-24

### Changed
- Replace the long event form with a guided planner, searchable device choices and a review step, using the shared IH design tokens.

## [0.1.3] - 2026-11-24

### Added
- Create and edit event plans using form controls and select devices and local media on the page. Applying plans and implementing effect actions remain separate work.

### Changed
- The page no longer displays generated configuration files; the existing API remains available.

## [0.1.2] - 2026-11-20

### Fixed
- The page sat on a loading circle: the single-threaded server was blocked by an idle connection from the ingress gateway. It now serves each connection in its own thread.

## [0.1.1] - 2026-11-20

### Fixed
- No more traceback spam in the log when the ingress gateway drops a connection (connection reset / broken pipe).

## [0.1.0] - 2026-11-19

### Added
- First version (task #86). Events as records (slug, name, dates, lights, speakers, sounds folder, automations to pause, effects, auto-off), a checklist and a review per run, an own database, a token-protected API and a page.
- Generator: helpers, mode on/off automation, safety automation, random and timed effects, look and effect-sound scripts, as YAML text. It never writes to Home Assistant.
- Two records are added on first start: Halloween (live by hand, described only) and New Year's Eve (planned).
- Optional switching of `input_boolean.<slug>_mode` from the page, off by default (option `control`).
