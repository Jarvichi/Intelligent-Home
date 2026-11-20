# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.1.1] - 2026-11-20

### Fixed
- No more traceback spam in the log when the ingress gateway drops a connection (connection reset / broken pipe).

## [0.1.0] - 2026-11-19

### Added
- First version (task #86). Events as records (slug, name, dates, lights, speakers, sounds folder, automations to pause, effects, auto-off), a checklist and a review per run, an own database, a token-protected API and a page.
- Generator: helpers, mode on/off automation, safety automation, random and timed effects, look and effect-sound scripts, as YAML text. It never writes to Home Assistant.
- Two records are added on first start: Halloween (live by hand, described only) and New Year's Eve (planned).
- Optional switching of `input_boolean.<slug>_mode` from the page, off by default (option `control`).
