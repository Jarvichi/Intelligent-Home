# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.2.6] - 2026-11-05

### Changed
- Ingress status page v2: one status line with a Check button, a plain "What it does" list, and a plain-language description.

## [0.2.5] - 2026-11-05

### Changed
- Ingress status page now uses the shared Games-style layout (page heading with service health, status and capabilities cards, collapsible About).

## [0.2.4] - 2026-11-05

### Added
- Thumbs up / down on answers: a thumbs-up or thumbs-down by an allowed user on one of the bot's answers is sent to the AI layer as feedback (+1 / -1) for the AI that wrote it, once per user and message. The bot asks the transport to watch each posted answer (`watch` operation, at most one hour); the last 500 answers (message id and request id only) are remembered in /data. Needs `watch` in the `ops` of the transport row; without it the bot works as before.
- "answered by Serper" label for the new search provider.

## [0.2.3] - 2026-11-05

### Added
- Successful answers end with a small "answered by <AI> · <model>" line. The answer is shortened, never that line, to stay under Discord's length limit. Error replies carry no such line.
- Requests to the AI layer now name this add-on as the caller, so its logs and usage show who asked.

## [0.2.2] - 2026-11-03

### Fixed
- Every accepted request failed with a NameError (trace id, root span id and start time were used before they were defined), so no reply was posted and no trace was sent. They are now created before the request is handled.

## [0.2.0] - 2026-11-02

### Added
- Emits metadata-only trace spans for accepted AI requests and Discord reply delivery.

## [0.1.3] - 2026-10-31

### Changed
- Prebuilt image, no code change.

## [0.1.2] - 2026-10-28

### Added
- Answers allow-listed users on chosen channels through the AI layer. Shadow mode by default.

## 0.2.1
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
