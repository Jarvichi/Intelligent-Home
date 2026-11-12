# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.2.0] - 2026-11-12

### Added
- Events: `POST /events` (token) stores an event from a local service (for example a hand-over, a reaction or a direct message) in the same queue as the Discord messages. Fields: `kind`, `source`, optional `ref` and `text`. The same `kind` and `ref` twice is stored once.
- `GET /digest?consumer=<name>`: the number of new records per kind for a consumer, without any message text.
- Long poll: `wait=<seconds>` (up to 25) on `GET /queue` and `GET /digest` holds the request open until a record arrives. A small doorbell process can then tell a reader at once, with one notice for many records, and nothing has to poll or be re-armed.
- `kind=<kind>` filter on `GET /queue`. Discord messages have the kind `message`.
- `/status` counts `events` and `event_duplicates`.

### Why
- The reader used to be woken by several background watchers, one per source, that had to be started again after every restart. A missed start meant missed messages, and each wake-up cost a full read of the reader's context. One durable queue plus one notice fixes both.

### Unchanged
- No new options. Without `api_token` the API still refuses everything. Event text is never written to the log or to `/status`.

## [0.1.11] - 2026-11-11

### Changed
- ihlib: add db.py (shared SQLite helper, not used yet).

## [0.1.10] - 2026-11-05

### Changed
- The add-on page has a sticky top bar: "‹ Intelligent Home" goes back to the Operations console in the whole Home Assistant window (it used to open inside the page frame, and only from a footer link at the very bottom). The footer link also opens in the whole window.

## [0.1.9] - 2026-11-05

### Changed
- Ingress status page: the shared page can now show rows without a score and bars without used/cap (for other add-ons' Today cards). No change to this add-on's own page.

## [0.1.8] - 2026-11-05

### Changed
- Ingress status page v2: one status line with a Check button, a plain "What it does" list, and a plain-language description.

## [0.1.7] - 2026-11-05

### Changed
- Ingress status page now uses the shared Games-style layout (page heading with service health, status and capabilities cards, collapsible About).

## [0.1.6] - 2026-11-04

### Fixed
- Remove deployment-specific identity examples from tracing source.

## [0.1.5] - 2026-11-04

### Added
- Shared trace library: optional typed span `attrs` (allowlisted keys, int or short-token values, max 8; `Span.set()`), never message text or IDs.

## [0.1.4] - 2026-11-03

### Added
- metadata-only trace spans (shared ihlib.trace); off without an ingest token.

## [0.1.2] - 2026-10-31

### Changed
- Prebuilt image, no code change.

## [0.1.1] - 2026-10-28

### Added
- Durable message inbox for chosen channels as a module of the Discord transport. Shadow mode by default.

## 0.1.3
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
