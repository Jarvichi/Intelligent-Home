# Changelog

## [0.2.0] - 2026-11-13
### Added
- Receipts ledger (slice 2): SQLite tables for whole receipts (every line, line discounts, cancelled lines kept), till-name aliases, pence-based money, duplicate and balance checks, payment details refused (`app/ledger.py`).
- Questions it answers (`app/stats.py`): price changes per product, weekly totals and why a week cost more or less, products due again from their buying gaps.
- Bundled `ihlib` (database helper). Still test mode: no API, no readers, not installed.

## [0.1.0] - 2026-11-10
### Added
- First slice (test mode): item library seeded from staples.json and aisles.json, with proposed shelf days. No live readers.
