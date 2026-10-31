# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.3.2] - 2026-10-31

### Changed
- Prebuilt image, no code change.

## [0.3.1] - 2026-10-30

### Fixed
- Ticks are saved again when the list holds the same name twice (a ticked one from last week and a new open one). Ticks now complete the open item by its id, never by name; before, Home Assistant could pick the already-ticked twin and the tick was lost.

## [0.3.0] - 2026-10-30

### Added
- Options `facts_url`, `facts_key`, `fresh_aisles`. When the first two are set, the shop checklist reads the orchestrator facts store (5 s limit, any failure = the old output): the aisle order and the item order come from `shopping/aisle_learned` (`{"order": [...], "rank": {...}}`, fallback: the options), and items in a fresh aisle get the date of the first planned meal that uses them from `cooking/fresh_dates`, for example `3️⃣ chicken thighs (Tue 3 Nov)`, so the use-by date to aim for is visible. Names are matched after removing numbers, units and brackets, plural or singular. The date is not part of the item name when you tick it. `fresh_aisles` defaults to Meat & fish, Fruit & veg, Dairy & chilled and Bakery.
- The module needs its own key: a `fact_clients` entry in the orchestrator (module `shopping`, write false).

## [0.2.1] - 2026-10-30

### Added
- Admin-only Ingress page "Shopping Shortcut" (HA sidebar, needs an admin login): shows the full Shortcut address with a copy button. Served on the internal ingress port 8796 to the Supervisor proxy only; never on the open port. New option `shortcut_base_url` (the address the phone uses, e.g. `http://<Home Assistant address>:8795`).

## [0.2.0] - 2026-10-29

### Added
- iPhone shopping Shortcut endpoint `POST /shortcut/<token>` on the add-on port: same request formats and JSON reply as the old Home Assistant webhook. Options `shortcut_token` (empty = endpoint off) and `shortcut_dry` (default true: the list is not changed, the reply has `"dry": true`). Logs counts only.

## [0.1.2] - 2026-10-28

### Changed
- Uses the shared ihlib library for Home Assistant REST calls (via core.py). No change in behaviour.

## [0.1.1] - 2026-10-28

### Added
- Shopping list commands as a module of the Discord transport. Shadow mode by default.
