# Changelog

## [0.1.2] - 2026-11-02
- Add an optional authenticated Brain MCP connection, restricted server-side to search/read of explicitly share:true notes. No Brain writes, inbox, private notes, attachments, or general Home Assistant access.

## [0.1.1] - 2026-11-02
- Encrypt and authenticate private inbound events and outbound reply text on the MQTT bus; reject unauthenticated ciphertext.

## [0.1.0] - 2026-11-02
- Add private Codex worker using the existing signed IH Discord transport.
- Persist incoming messages before transport acknowledgement and hold uncertain turn or delivery outcomes for manual recovery.
- Restrict the module to one configured channel and user, a private scratch workspace, denied escalations, and disabled tool network access.
