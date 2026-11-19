# Work Runner

Supervises the worker (`queue_worker.py`) in a loop: one pass (claim a job from the Work add-on, run it with the Claude CLI, report on the task), then wait up to `idle_max_s`, waking early when a job is queued and nothing runs.

## Options
| Option | Meaning |
|---|---|
| `mode` | `dry` (default, claims nothing) or `live`. Live also needs the file `work_queue/LIVE`. |
| `stop` | Pause without stopping the add-on. The file `work_queue/STOP` does the same. |
| `work_token` | API token of the Work add-on. |
| `claude_oauth_token` | Claude token. Optional if `/homeassistant/.claudecode/oauth_token` exists. |
| `mcp_config_file` | Path of a prepared Claude settings file with the House Brain and Home Assistant tools; copied into the add-on's private volume. |
| `work_url` | Only if the Work add-on is not reachable at its usual addresses. |
| `idle_max_s`, `idle_check_s` | Wait limits between passes. |

## Mapped folders
`/homeassistant` and `/share`, read-write. The worker scripts are read from `/homeassistant/claude_scripts/work_queue`; they are not copied into the image.

## Only one runner may be live
The worker takes the lock `work_queue/.worker.lock`, so a second runner (for example the old host loop) is refused, but switch the old one off before going live here.

## Switch-over (owner, in order)
1. Install, leave `mode: dry`, check `/healthz` shows no problems.
2. Stop the host session `workq`, remove its line from the host tmux config, set `mode: live`, create `work_queue/LIVE`.
3. Watch one full task. Set boot auto, watchdog `http://[HOST]:[PORT:8847]/healthz`, auto update.
Rollback: stop this add-on, restore the tmux line, restart `workq`.

## Not proven yet
Claude login and tools inside a container, `gh` push from the container, a running job surviving an add-on restart (the Work lease runs out and the job returns to the line).
