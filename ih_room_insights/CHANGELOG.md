# Changelog

## [0.2.0] - 2026-11-25
- New read-only UI page (ingress port 8813, admin only): warm-up minutes per room with a bar, suspect valves with last-change time, mould risk per humidity sensor, mode, last run, rooms analysed, history days. Shows in the IH Jump-to row.
- Frozen-valve entries now carry a `since` time (additive; existing API keys unchanged).
- Port 8812 API and api_token endpoints unchanged.

## [0.1.0] - 2026-11-19
- First version: learned warm-up minutes per room, frozen-valve check, mould risk. Read-only. Dry by default.
