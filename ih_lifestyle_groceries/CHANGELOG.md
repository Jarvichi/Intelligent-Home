# Changelog

## [0.3.0] - 2026-11-13
### Added
- Dockerfile, run.sh and image name so the add-on builds (0.2.0 had none).
- Receipts API on port 8801 (add-ons only, not published): `POST /receipts` and `POST /alias` (Bearer `api_token`; no token set = refused), read-only `GET /stats/due|prices|weeks|week_diff`, `GET /healthz`. Mode `dry` validates only and writes nothing; mode `live` stores. Payment details are refused. 13 tests.

## [0.2.0] - 2026-11-13
### Added
- Receipts ledger (slice 2): SQLite tables for whole receipts (every line, line discounts, cancelled lines kept), till-name aliases, pence-based money, duplicate and balance checks, payment details refused (`app/ledger.py`).
- Questions it answers (`app/stats.py`): price changes per product, weekly totals and why a week cost more or less, products due again from their buying gaps.
- Bundled `ihlib` (database helper). Still test mode: no API, no readers, not installed.

## [0.1.0] - 2026-11-10
### Added
- First slice (test mode): item library seeded from staples.json and aisles.json, with proposed shelf days. No live readers.
