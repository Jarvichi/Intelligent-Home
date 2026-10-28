# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.1.0] - 2026-10-28

### Added
- MCP server over HTTP (JSON-RPC 2.0, `POST /mcp`, bearer token) with the same 14 tools as the House Brain component: help, index, search, read, list, recent, links, graph, image, text, file, files, write, append.
- `read_only` option (default on): write tools refuse with a clear message.
- `/healthz` and `/status` (note count, last index time, no note content).
