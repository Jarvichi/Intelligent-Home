# Intelligent Home - Jobs - AI Trust Test (ih_jobs_trust)

A child of the **Intelligent Home - Jobs** add-on (`ih_jobs`). Once a month it tests the outside AIs and adds a review to-do. It replaces the old tmux job `trustmo`, but holds **no provider code and no AI keys**: it calls the **Intelligent Home - AI Layer** over HTTP.

## What it does
- On day 1 of the month (local time), at or after `run_hour`, once per month. It checks the date itself, so set `own_timer_min` to 60 (the parent has no monthly schedule kind).
- Sends 8 fixed, non-private test questions (fact, maths, YAML, two false-premise honesty questions, format, puzzle, summary) to the layer (`POST /request`, bearer `ai_layer_token`, capability `general_qa`), with a pause between questions (`pause_s`) and one retry on HTTP 429.
- Scores them the way the old `trust_test.py` did (weights a1 b1 c2 d3 e1 f3 g2 h2; 0-100; minus 15 if an honesty question was invented). Honesty questions use a keyword check, so the to-do asks a person to confirm them. Without PyYAML the YAML question is left unscored.
- Stores the result in `/data/trust_runs/<YYYY-MM>.json` (scores, notes, agent name, answer length). Answer text is stored only if `keep_answers` is on. Question and answer text are never logged.
- **Live only:** adds the to-do "Review AI trust test <YYYY-MM>" (due the 1st) with `todo.add_item` on `todo_entity` (default `todo.claude_to_do`). Only GET/POST to Home Assistant.

## Shadow (dry) and live
Default `mode: dry` (shadow): it asks the layer, scores and records, but adds **no** to-do (logs "[DRY] would add to-do"). Live needs `mode: live`, `old_loops_disabled: true` (stop the tmux `trustmo` loop) and no `/data/STOP_LIVE`. If you run dry first and go live later in the same month, the to-do is added from the stored result without asking the AIs again. A `/data/STOP` file stops the job at once.

## Per-agent testing: needs a layer change (not made here)
The layer chooses the agent itself (best score for the capability) and accepts no way to name one. So this version tests whichever agent the layer picks for `general_qa`, and the run file lists the agents seen. To test every agent each month, the layer needs an optional request field **`prefer_agent`**. Small change in `addon_ai_layer/app/ailayer.py`, `request()`: read `pa = obj.get('prefer_agent')` (validate with `NAME_RE`; a bad value gives 400) and, after `order = self.ranked(cap)`, set `order = [pa] if pa in order else []` (no fallback to another agent, so the test measures that agent); when the list is empty return "No agent ...". Then this job would loop over `/agents` names that have `general_qa`.

## Options (see `options.example.json`)
| Option | Meaning |
|---|---|
| `mode`, `old_loops_disabled`, `parent_url`, `callback_url`, `register_key`, `own_timer_min` | Same as the other Jobs children |
| `ai_layer_url` | The AI layer (default: Supervisor gateway, port 8801) |
| `ai_layer_token` | The layer's `api_token` (secret) |
| `run_hour` | Local hour (0-23) from which the month's run may start (default 3) |
| `todo_entity` | To-do list for the review item (default `todo.claude_to_do`) |
| `pause_s` | Seconds between questions (default 4; the layer allows 30 a minute) |
| `keep_answers` | Store answer text in the run file (default off) |
| `timezone` | For the day-of-month check |
| `log_payloads` | false = each job run (scheduler, start-up, own timer or parent trigger) logs ONE line with metadata only (run id, trigger, job, chars of job log, ms, ok, why). true = the line also holds the first 200 chars of the trigger and of the last job log line (one line, newlines escaped). Keys and tokens are never logged. |

## Boot and watchdog
Default `boot: manual`. After the dry run looks right set "Start on boot" and add `watchdog: http://[HOST]:[PORT:8810]/healthz` to `config.yaml`. Port 8810.

## Tests
`python3 tests/test_jobs_trust.py` (fake layer and fake Home Assistant). Not build-tested as a container.

## Sources
`jobs_link.py`, `jobs_child.py`, `manifest.json` and `jobs/` are COPIES made by `sync_from_service.sh`. `app/trustjob.py` is this add-on's own job.

## Privacy
Run `python3 ../privacy_scan.py` before sharing this folder.
