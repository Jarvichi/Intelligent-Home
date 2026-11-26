# Changelog

## [0.2.0] - 2026-11-25

### Added
- Read-only status page in the sidebar (ingress, port 8099, threaded server, gateway only): mode, pause reasons, the job running now (title only, from the Work add-on), the last 20 runs from the worker log (task, mode, result, duration), the pace gate line, the worker lock age, problems and failures. Shows "Work add-on not reachable" instead of failing. No buttons that start or stop anything; a link to the Work page (STOP switch). Task #136.
- `app/ui/ui.json` for the IH UI standard. Port 8847 (/healthz, /status) unchanged. Options and install flags unchanged.

## [0.1.0] - 2026-11-19

### Added
- First version (task #87, stage 1: built, not installed). Supervises the existing worker loop inside an add-on: options to environment, STOP and LIVE switches, one worker at a time (the worker's own lock file), early wake when a job waits, health page on 8847.
- Image: Node, Claude Code CLI, git, GitHub CLI, Python. NOT build-tested.
- Worker scripts are read from the mapped work_queue folder, not copied into the image.
