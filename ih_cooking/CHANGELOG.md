# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.2.0] - 2026-10-28

### Added
- Recipes page served through ingress (no sidebar panel), same look and behaviour as the old host page.
- Write API (bearer token): save and create recipes (validated, backups), meal plan, shopping list, import for seeding, publish.
- Cook engine: port of the host cook loop (step timing, gate sliding, urgent repeat, music pause and resume, child message), the wall display button actions (done, acknowledge, prep tick, delay) and oven start, set and stop with retry until the oven confirms and a speaker plus Discord alert after one minute.
- Discord posting through a bot token option; wall display cooking sensor and rerender script through options.
- Hard guard: live control is refused while another controller's heartbeat is fresh, or when it cannot be checked.
- `seed_from_host.py` to copy a host cooking folder into the add-on.

### Changed
- Cook control and publishing are dry by default (logged only).
- Messages and plan texts use a configurable child name instead of a fixed one.

## [0.1.1] - 2026-10-28

### Added
- Recipes and meal plan data, plus the weekly meal-vs-calendar check (read-only).
