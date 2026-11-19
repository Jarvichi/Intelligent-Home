# Changelog

## [0.1.0] - 2026-11-19

### Added
- First version (task #87, stage 1: built, not installed). Supervises the existing worker loop inside an add-on: options to environment, STOP and LIVE switches, one worker at a time (the worker's own lock file), early wake when a job waits, health page on 8847.
- Image: Node, Claude Code CLI, git, GitHub CLI, Python. NOT build-tested.
- Worker scripts are read from the mapped work_queue folder, not copied into the image.
