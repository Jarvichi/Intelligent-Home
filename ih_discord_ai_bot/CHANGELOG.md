# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

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
