# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.2.9] - 2026-11-04

### Changed
- Give the rich application a clear Brain sidebar title and purpose-specific icon.
- Declare administrator-only sidebar access explicitly; visibility stays a per-installation Home Assistant preference.

## [0.2.8] - 2026-11-04

### Fixed
- Remove deployment-specific identity examples from tracing source.

## [0.2.7] - 2026-11-04

### Added
- Shared trace library: optional typed span `attrs` (allowlisted keys, int or short-token values, max 8; `Span.set()`), never message text or IDs. MCP spans carry request and response sizes.

## [0.2.6] - 2026-11-04

### Changed
- Improve browse and search with area filtering, result counts, keyboard tabs, race-safe updates and return-to-selection behavior.

## [0.2.5] - 2026-11-03

### Added
- optional `ingest_url` / `ingest_token` options; with a token the add-on sends metadata-only request traces (note search/read) to the Orchestrator. The Orchestrator fills an empty token. Empty = tracing off.

## [0.2.3] - 2026-11-02

### Added
- Admin-only Home Assistant ingress UI for Brain note browsing, search, recent notes, and connection summaries. UI API is read-only, same-origin, and accepts requests only from the Supervisor ingress gateway.
- Local shared IH UI tokens and component primitives; no external assets or third-party requests.

## [0.2.2] - 2026-11-02

### Added
- Separate `/codex-mcp` endpoint and `codex_read_token`; it exposes only search/read over explicitly `share: true` markdown notes. Inbox, private notes, attachments and write tools are unavailable.

## [0.2.1] - 2026-10-31

### Added
- Option `cors_origins` (comma separated exact origins, empty = off, `*` never accepted): answers browser preflight (OPTIONS) and adds CORS headers for those origins only (Authorization, Content-Type; POST, OPTIONS). Lets a notes page on another port call /mcp.

## [0.2.0] - 2026-10-31

### Added
- `POST /apple-notes`: stores one Apple Note in a staging folder outside the brain (dry unless `apple_notes_live`).
- Pocket brain sync (`POST /pocket-sync` and optional timer): pulls the phone inbox into the brain, pushes scrubbed shareable notes to Cloudflare KV, changed notes only (dry unless `pocket_live`). New options for the token and key; both are never logged.
- Option `scrub_names` (`Name=Replacement,...`): names replaced in pushed notes. Empty by default, so no personal names are in the code.
- `/status` shows whether each feature is live and the last sync result (no secrets).

## [0.1.0] - 2026-10-28

### Added
- MCP server over HTTP (JSON-RPC 2.0, `POST /mcp`, bearer token) with the same 14 tools as the House Brain component: help, index, search, read, list, recent, links, graph, image, text, file, files, write, append.
- `read_only` option (default on): write tools refuse with a clear message.
- `/healthz` and `/status` (note count, last index time, no note content).

## 0.2.4
- Improve the Brain UI on phones with progressive note lists and a separate reader view.
- Restore the canvas-based ForceGraph connection view and native touch navigation.
