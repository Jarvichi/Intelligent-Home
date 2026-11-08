# Intelligent Home - Work

## What it does

Keeps one line of jobs for Claude (first in, first out). A job is handed out only when Claude's weekly allowance is far enough under its pace. By day the pace must be well under; at night it may be closer. One job runs at a time. A helper (the "runner") claims a job over a protected local API, keeps a lease alive while it works, and must report the job done or failed. This add-on does **not** run jobs; the runner stays where the `claude` command lives.

## Requires

- Home Assistant OS or Supervised (add-ons need the Supervisor)
- The Home Assistant integration that provides the Claude usage sensors (week used %, 5-hour session used %, weekly reset time)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Work**.
2. Open the Configuration tab and set `api_token` (a long random text). With an empty token the API refuses everything.
3. Leave `mode` on `dry`. Start the add-on and read the log. It says which levels are in force.
4. Open the page (side bar: IH · Work). The Today card shows the line and why it waits.
5. When the log and the page look right, and the runner is ready, set `mode` to `live`.

## Why it needs Home Assistant API access (`homeassistant_api: true`)

- To **read** three sensors for the pace number (week used %, session used %, weekly reset time). Pace = week used % minus week elapsed % of the week (negative = under pace).
- To **read** the Claude to-do list, only when `feed_backlog` is on. Nothing is ever written to the to-do list in this version.
- It never calls any other service and never changes a device.

## Options

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Not used (nothing from requests is logged). |
| `mode` | No | `dry` (default): keeps the line, hands out nothing, logs `[DRY] would hand out ...`. `live`: hands out jobs. |
| `api_token` | Yes | Secret for the API (bearer). Empty = every call is refused except `/healthz`. |
| `stop` | No | On = nothing is handed out. The file `/data/STOP` does the same. |
| `timezone` | No | For day and night. Default `Europe/London`. |
| `lease_minutes` | No | How long a helper may hold a job without a heartbeat. Default 20. |
| `day_start` / `day_stop` | No | Day level: start below -5.0, stop at -4.0. |
| `night_start` / `night_stop` | No | Night level: start below -1.0, stop at 0.0. |
| `night_from` / `night_to` | No | Night hours, `HH:MM`. Default 23:00 to 06:00. |
| `week_usage_entity`, `session_usage_entity`, `weekly_reset_entity` | No | The three usage sensors. Empty = find the one sensor whose id starts `sensor.claude_usage_` and ends `_week_usage`, `_session_usage`, `_weekly_reset_time`. |
| `feed_backlog` | No | Off by default. On = see "Backlog feeder". |
| `todo_entity` | No | The to-do list to read. Default `todo.claude_to_do`. |
| `feed_prefixes` | No | Titles that start with one of these are work for Claude. Default `IH:`, `Check:`, `Review:`. |

## The pace gate

Each level has a start and a stop. Idle: a job starts only when pace is below the start. Draining: jobs follow one at a time while pace stays below the stop; at the stop or higher, the running job finishes and nothing more starts. Safeguards in both: 5-hour session under 85%, week under 90%, STOP switch, and if the sensors cannot be read nothing runs.

When the clock moves between night and day, draining is dropped at once. The next job then starts only if the new level allows a start. The reason text always names the level, for example `day band: pace -3.2, above -4, nothing runs` or `day band: pace -4.5, waiting until it drops below -5`. The service keeps the mode current every 30 seconds, also when the line is empty.

There is no switch in the API that skips the gate.

## Leases (how a job is held)

1. `claim` gives the first job to a named consumer with a lease (default 20 min). The job is `claimed`.
2. `heartbeat` renews the lease and sets the job to `running`. Send one at least every few minutes.
3. `complete` or `fail` ends the job. They work only for the consumer that holds the lease (the acknowledgement). A second ack is refused.
4. If the lease runs out, the job returns to the line in the same place (attempts + 1). After 3 expired leases it is `failed`. `retry` puts a failed job at the back and resets attempts.

## How a consumer claims work

All calls need `Authorization: Bearer <api_token>` and JSON bodies. Port 8802 (the Supervisor host name of the add-on, for example `http://<host>:8802`).

1. `POST /claim {"consumer": "runner-1"}` returns `{"claimed": true, "item": {...}, "lease_expires_at": "..."}`, or `{"claimed": false, "reason": "plain words"}`. In test mode: `{"claimed": false, "dry": true, "would_claim": {...}}`.
2. Do the work. Every few minutes `POST /heartbeat {"id": "...", "consumer": "runner-1"}`.
3. `POST /complete {"id", "consumer", "summary"}` or `POST /fail {"id", "consumer", "error"}`.

Other calls: `POST /enqueue {kind, ref, title, instruction, approved?, by?}` (same kind + ref while open returns the existing item), `GET /list?status=queued,failed` (`{"items": [...], "corrupt_files": [...]}`), `GET /item/<id>`, `POST /cancel {"id"}` (queued only), `POST /retry {"id"}` (failed only, goes to the back), `GET /status`, `GET /healthz` (open). Unknown fields are refused with 400. Errors: 400 bad input, 401 no/wrong token, 403 another consumer holds the job, 404 no such job, 409 wrong state.

`GET /status` has `counts` (queued, running, done, failed, cancelled; running includes claimed), `next`, `running`, `last_finished`, `worker` (`state`, `reason`, `mode`, `band`, `updated`), `gate` (one line), `gate_at`, `pace`, `week`, `session`, `corrupt_files`.

## Backlog feeder (`feed_backlog`, default off)

Replaces the nightly worker's picking. When the line is empty and the gate would allow a claim, the service adds ONE job from the to-do list (checked at most every 5 minutes), kind `todo`, ref = the to-do item id. Rules:
- Only items whose title starts with one of `feed_prefixes`, or that carry the tag `[claude]` or `[approved #N]`. Personal items are never picked.
- Never an item tagged `[waiting...]`, `[needs-ok ...]`, `[plan...]` or `[no-overnight]`.
- Items tagged `[approved #N]` first and queued as approved; all others are read-only (`approved: false`). Then the earliest due date, then the item worked on least, then list order.
- Never the same item twice within 7 days.
- In test mode it only logs `[DRY] would feed: <title>`.

When this works and the owner approves going live, the old nightly worker is retired. Until then both exist; decide who owns each item first so they do not pick the same one.

## Where things are kept

Everything is in the add-on's `/data`: `items/` (one file per job), `state.json` (mode, hour counters, fed list, last status), `seq.counter`. Uninstalling the add-on deletes the queue. No folders are mapped.

## Troubleshooting

- **Page says "Status is old".** The 30-second timer stopped. Restart the add-on and read the log.
- **Reason says "usage sensors could not be read".** Check the three sensor options, or that the usage integration is running.
- **Nothing is handed out.** Test mode (`mode: dry`), the STOP option or `/data/STOP`, or the gate reason on the page.
- **A job shows in `corrupt_files`.** That file is not valid JSON; it is skipped, not deleted.

## Privacy

Everything stays on your network. Nothing is sent out. The page shows job titles and counts only.
