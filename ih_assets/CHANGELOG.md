# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.2.0] - 2026-11-26

### Added
- First release of the add-on. A list of things you own (game, DVD, toy, device, book, other), each with quantity, cost, low and high value, bought and sold dates.
- Daily low/high total history and a price log per item, in the add-on's own database (`/data/ih.db`, daily snapshots in `/data/backup`).
- Admin page in the shared IH style: totals, low/high bar chart over time with a text summary, searchable table with category and priced filters and sorting, add item, set price, mark sold (with confirmation), barcode lookup.
- Token-protected API on port 8850 (`api_token`; an empty token refuses everything except `/healthz`).
- `mode: live` publishes `sensor.assets_low` and `sensor.assets_high`; the default `dry` publishes nothing.
- One-time import from `/data/import/inventory.json` (and `history.csv`); refused when the database already has data.

### Not included
- Monthly price refresh from a price site. Set prices on the page for now.
