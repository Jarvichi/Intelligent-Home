# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.3.0] - 2026-10-29

### Added
- Options `brain_url` and `brain_token`: publishing writes the recipe notes straight to the Brain add-on (MCP `brain_write`). Only notes whose content changed are sent. Dry unless `publish_live` is true; note text is never logged.
- Recipes page: create a new recipe, show the full cook result (steps), cook status and stop button.

### Changed
- The www part of publish is skipped quietly when `publish_webhook_url` is empty.

## [0.2.2] - 2026-10-28

### Added
- Home Assistant API access (`homeassistant_api`). With `ha_token` empty and the default `ha_url`, the add-on uses its own access. No long-lived token needed.

## [0.2.1] - 2026-10-28

### Changed
- Home Assistant REST calls (calendar and sensor reads, oven and speaker service calls) now go through the shared ihlib library. No change in behaviour.

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
