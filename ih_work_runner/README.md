# Intelligent Home - Work Runner

Runs the Claude jobs that the Work add-on (`ih_work`) holds. Until now a tmux session on the host started this loop; this add-on does the same inside Home Assistant.

- **Family:** Work.
- **Needs:** `ih_work` (queue and pace gate), `ih_tasks` (task list). Home Assistant API access.
- **Starts safe:** mode `dry`. It only looks; nothing is claimed.
- Set-up and the switch-over steps: [DOCS.md](DOCS.md). Version history: [CHANGELOG.md](CHANGELOG.md)
