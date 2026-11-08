# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.1.7] - 2026-11-08

### Fixed
- Same date fix in the add-on's own `scrub`: a plain date is no longer replaced with `[number]`.

## [0.1.6] - 2026-11-05

### Changed
- The add-on page has a sticky top bar: "‹ Intelligent Home" goes back to the Operations console in the whole Home Assistant window (it used to open inside the page frame, and only from a footer link at the very bottom). The footer link also opens in the whole window.

## [0.1.5] - 2026-11-05

### Changed
- Ingress status page: the shared page can now show rows without a score and bars without used/cap (for other add-ons' Today cards). No change to this add-on's own page.

## [0.1.4] - 2026-11-05

### Changed
- Ingress status page v2: one status line with a Check button, a plain "What it does" list, and a plain-language description.

## [0.1.3] - 2026-11-05

### Changed
- Ingress status page now uses the shared Games-style layout (page heading with service health, status and capabilities cards, collapsible About).

## [0.1.2] - 2026-11-03

### Added
- Ingress panel (sidebar page) showing this add-on's status.

## [0.1.1] - 2026-10-31

### Changed
- Prebuilt image, no code change.

## [0.1.0] - 2026-10-28

### Added
- Agent for the AI layer running the Claude Code CLI with all tools off, with a weekly usage gate.

## 0.1.2
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
