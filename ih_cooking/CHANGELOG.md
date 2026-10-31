# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.6.0] - 2026-10-31

### Added
- `POST /shop/merge` (bearer): the meal-plan shopping merge. Body `{"recipes": [slug], "groups": [staple group], "have": ["items already on the list"], "commit": false}`. Returns `add` (lines not yet on the list, matched on lower case without a `Meals:`/`Staples:` prefix, also within the new lines), `skipped`, `message` (the weekly Discord text) and `ledger_written`. It never touches any shopping list: the caller adds the lines. The meal-plan ledger is only updated when `commit` is true. Existing `POST /shop` is unchanged.

## [0.5.0] - 2026-10-30

### Added
- Fact `cooking/fresh_dates` (same `facts_url` / `facts_key` options, off when either is empty): `{"<ingredient>": "YYYY-MM-DD"}` = for each ingredient of the planned meals in the next 7 days (the weekly plan mon..sun, recipes' `shopping` lines), the date of the first planned meal that uses it. Names are lower case without quantity, unit or brackets (`2 chicken breasts (about 450 g)` becomes `chicken breasts`). The shop checklist (ih_discord_shopping 0.3.0) shows it as a use-by date. Checked every 10 minutes, sent when it changes and at least every 12 hours, expires after 36 hours, cleared when the plan is empty. Over 4 KiB the latest dates are dropped. Sending is in the background; a failure is logged (type only) and retried.

## [0.4.0] - 2026-10-29

### Added
- Options `facts_url` and `facts_key`. While a cook plan exists, the cook engine writes its eat time to the orchestrator facts store as `cooking/eat_time` = `{"date": "YYYY-MM-DD", "eat": "HH:MM"}`, expiring at the next local midnight. It is sent again when the eat time moves (a delay, or waiting for the ingredients) and deleted when the plan is stopped or gone. Other add-ons (the Assistant's check-in planner) read it to avoid the meal. Off when either option is empty. Sending is in the background, retried once a minute on failure, and never blocks cooking. Values and the key are never logged.
- The eat time is known only while a cook is started. The weekly meal plan holds recipe names, not eat times, so a day without a started cook has no fact.

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
