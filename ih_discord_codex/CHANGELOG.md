# Changelog

## [0.1.11] - 2026-11-03

### Changed
- Health and status port moved from 8804 to 8815 (8804 belongs to the Gemini AI agent).

### Fixed
- Make `/status` tolerate older or stub status data without `claude_handoffs`; the command reports “no Claude handoffs” instead of raising `KeyError`.

## [0.1.9] - 2026-11-02
- Add a read-only tool for the explicitly approved live `todo.claude_to_do` list.
- Allow access-limited HA, Brain, host, and IH task requests to be handed off to Claude with a concise user-request summary.
- Keep HA tool results and Brain content out of Claude handoffs.

## [0.1.8] - 2026-11-02
- Default the code-only Claude handoff to the fixed `claude_review` channel key so enabling it does not depend on adding an option to an already-installed add-on schema.

## [0.1.7] - 2026-11-02
- Add a durable, one-way Claude handoff for code tasks that need host context or a final pass; send a concise code-only summary and optional public PR URL.
- Keep uncertain handoff sends from replaying automatically and expose handoff state in status.

## [0.1.6] - 2026-11-02
- Re-trigger image publishing after the first 0.1.5 workflow skipped its build matrix because the version bump was in an earlier commit in the same push.
- Keep repository ownership derived from the add-on image configuration so generic public source passes the house privacy scan.

## [0.1.5] - 2026-11-02
- Add filtered Home Assistant entity search/state/history tools through a loopback MCP server; only the add-on process handles the Supervisor token.
- Add one-step light/switch action proposals that execute only after the authorized owner confirms with `/approve CODE`; uncertain requests are never retried automatically.
- Describe the worker's coding workspace and limited HA capabilities accurately.

## [0.1.4] - 2026-11-02
- Auto-approve only the explicitly enabled read-only Brain search and read MCP tools, so the non-interactive worker can use them while all other approvals remain denied.

## [0.1.3] - 2026-11-02
- Start a fresh Codex thread once when the Brain tool-access policy changes, so older thread context cannot retain the previous no-Brain instructions.
- Mark the shareable Brain MCP server explicitly enabled in Codex configuration.

## [0.1.2] - 2026-11-02
- Add an optional authenticated Brain MCP connection, restricted server-side to search/read of explicitly share:true notes. No Brain writes, inbox, private notes, attachments, or general Home Assistant access.

## [0.1.1] - 2026-11-02
- Encrypt and authenticate private inbound events and outbound reply text on the MQTT bus; reject unauthenticated ciphertext.

## [0.1.0] - 2026-11-02
- Add private Codex worker using the existing signed IH Discord transport.
- Persist incoming messages before transport acknowledgement and hold uncertain turn or delivery outcomes for manual recovery.
- Restrict the module to one configured channel and user, a private scratch workspace, denied escalations, and disabled tool network access.

## 0.1.10
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
