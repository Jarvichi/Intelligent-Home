# Intelligent Home - Jobs - AI Trust Test

## What it does

Child of Intelligent Home - Jobs: monthly trust test of the outside AIs via the AI layer, adds a review to-do. Dry-run (shadow) by default. Child of `ih_jobs`.

## Requires

- `ih_jobs` add-on
- `ih_ai_layer` add-on
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Jobs - AI Trust Test**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `register_key`, `ai_layer_token` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in dry-run mode, so it changes nothing yet.
5. When the log looks right, set `mode` to `live`.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `mode` | No | `dry` logs what it would do and changes nothing; `live` acts. Default: `dry`. |
| `old_loops_disabled` | No | Same as the other Jobs children. Default: `false`. |
| `parent_url` | No | Address of the parent add-on. Default: `http://172.30.32.1:8792`. |
| `callback_url` | No | How the parent reaches this add-on. Empty = the Supervisor gateway and this add-on's port. |
| `register_key` | Yes | Same as the other Jobs children |
| `own_timer_min` | No | Same as the other Jobs children. Default: `60`. |
| `ai_layer_url` | No | The AI layer (default: Supervisor gateway, port 8801) |
| `ai_layer_token` | Yes | The layer's `api_token` (secret) |
| `run_hour` | No | Local hour (0-23) from which the month's run may start (default 3) |
| `todo_entity` | No | To-do list for the review item (default `todo.claude_to_do`) |
| `pause_s` | No | Seconds between questions (default 4; the layer allows 30 a minute) |
| `keep_answers` | No | Store answer text in the run file (default off) |
| `timezone` | No | For the day-of-month check. Default: `Europe/London`. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `mode` is `dry`. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Parent does not list this add-on / "cannot reach parent".** `register_key` must be identical in both add-ons, and `parent_url` must point at the parent (default gateway address and port). An empty key turns the link off.

## Privacy

Only fixed, non-private test questions are sent, through the AI layer to the outside AI services you enabled. Answers are not kept unless `keep_answers` is true.

## More details

### Shadow (dry) and live
Default `mode: dry` (shadow): it asks the layer, scores and records, but adds **no** to-do (logs "[DRY] would add to-do"). Live needs `mode: live`, `old_loops_disabled: true` (stop the tmux `trustmo` loop) and no `/data/STOP_LIVE`. If you run dry first and go live later in the same month, the to-do is added from the stored result without asking the AIs again. A `/data/STOP` file stops the job at once.

### Data storage (database since 0.2.0)

The add-on keeps its data in one SQLite file, `/data/ih.db` (shared helper `ihlib/db.py`, WAL mode). Every file under `/data` was reviewed:

| File under `/data` | Where it lives now | Why |
|---|---|---|
| `trust_state.json` (month done, to-do added, summary) | Database: key-value row, namespace `trust`, name `state` | Small state |
| `trust_runs/<YYYY-MM>.json` (monthly scores) | Database: table `trust_runs` (month, finished, dry, json) | One record per month |
| `options.json` | Stays a file | Supervisor owns it; holds the secrets (`ai_layer_token`, `register_key`). Secrets are never stored in the database or its snapshots |
| `STOP`, `STOP_LIVE` | Stay files | Kill switches: you create or delete them by hand |
| Add-on log (container log) | Stays text | Logs stay text |
| `trash/` | New folder | Old JSON files, deleted after 30 days |
| `backup/` | New folder | Nightly snapshots of `ih.db`, newest 3 |
| `degraded/` | New folder, only used when the database cannot be used | Plain files, imported again after a restore |

No history or cache files exist besides these (answer text is stored only when `keep_answers` is on, inside the run row).

**Start-up:** the add-on opens `ih.db`, applies numbered migrations (a snapshot is taken first when the file already has data), copies each old JSON file into the database, checks the rows inside the same transaction, and only then moves the file to `/data/trash`. It purges the trash at start and once a day. From 04:00 it takes one snapshot a night into `/data/backup`, opens the copy read-only, runs the integrity check and logs the row counts (counts only, no text).

**Status:** `/status` (and `/api/status` on the ingress page) shows `db`: schema version, rows per table, last snapshot time, snapshots kept, last restore check, trash files and bytes, import errors, and `degraded`.

**If the database is damaged or too new:** the job keeps running. Status shows `db_degraded: true`, a `WARNING` and the error, and the log says `DATABASE DEGRADED`. Results go to `/data/degraded/`. The damaged file is moved aside as `ih.db.corrupt-<time>`; nothing is recreated.

**Restore** (add-on stopped): copy the newest file in `/data/backup` to `/data/ih.db`, delete `ih.db-wal` and `ih.db-shm`, start the add-on. Files in `/data/degraded/` are then imported automatically. To go back to the previous add-on version: install it, and copy the files from `/data/trash` back to their old places (`/data/trust_state.json`, `/data/trust_runs/`).

Untested on the real device: power loss, a restore from a Home Assistant backup, running two copies at once.

### Every AI gets the same questions
Each month the test asks the AI Layer which chat AIs are ready (its `/status` list: able to answer general questions, not in test mode, not switched off, set up). It then sends the same 8 questions to each AI, one AI at a time, and forces the AI by its model name (the layer's `model` field, so there is no fallback to another AI). If a different AI answers than the one asked, that question is marked `misrouted` and gets no mark. Search and tool providers are never asked.

OpenAI is left out by default because its billing is not known. Eight calls a month is small, but the default stays. To include it, put `openai` in `providers`.

- `providers`: comma list of AI names to test (for example `groq, gemini, openai`). Empty = every ready chat AI. A name in this list is tested even if `exclude` names it.
- `exclude`: comma list of AI names to skip when `providers` is empty. Default `openai`. Use `none` to skip nothing.

The run waits `pause_s` seconds between calls and retries once after a rate-limit answer. If the layer keeps refusing (3 in a row), the run stops and keeps what it has (marked "stopped early"). A bad token or a stopped layer ends the run at once. An AI the layer cannot serve is skipped.

Scores are 0-100 per AI with the same marking as the old script. A question with no usable answer (error, misroute) gets no mark and is not counted as zero. Run records (table `trust_runs`, one row per month, `format` 2) hold `results[ai][question]`; records from before (one AI per question) are still read. Question and answer text is never logged.
