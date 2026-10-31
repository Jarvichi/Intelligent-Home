# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

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
