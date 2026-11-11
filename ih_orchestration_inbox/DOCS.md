# Intelligent Home - Orchestration - Inbox

## What it does

Orchestration inbox: durable queue of messages on chosen Discord channel keys (bus module of ih_discord), eye-emoji acknowledgement, token-protected read API. Never answers in the channel. Shadow mode by default: queue written, no reactions. Module of the `ih_discord` transport.

## Requires

- `ih_discord` add-on
- an MQTT broker (the Mosquitto add-on works)
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Orchestration - Inbox**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `signing_key`, `api_token`, `mqtt_password`, `ingest_token` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in shadow mode, so it changes nothing yet.
5. When the log looks right, set `shadow` to `false` to go live.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `module_name` | No | Name on the bus. Must match the transport row. Default: `ih_orchestration_inbox`. |
| `signing_key` | Yes | Secret shared with the transport row |
| `shadow` | No | On (default): answers or logs only, posts nothing. Turn off to go live. |
| `channel_keys` | No | Channel KEYS to claim (catch-all). Empty = claims nothing |
| `ack_emoji` | No | Emoji put on each message. Default: `👀`. |
| `retention_days` | No | Days to keep the queue (1 to 30). Default: `7`. |
| `api_token` | Yes | Bearer token for `/queue` and `/ack`. Empty = refused |
| `mqtt_host` | No | MQTT broker host (default: the Mosquitto add-on). |
| `mqtt_port` | No | MQTT broker port. Default: `1883`. |
| `mqtt_user` | No | MQTT user name for this add-on. Default: `ih_orchestration_inbox`. |
| `mqtt_password` | Yes | MQTT password for this add-on. |
| `ingest_url` | No | Orchestrator status push (optional). Default: `http://172.30.32.1:8790`. |
| `ingest_token` | Yes | Orchestrator status push (optional) |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `shadow` is on. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Silent on the bus.** Check the `mqtt_*` options and that `signing_key` matches this module's entry in the orchestrator's `module_keys` (or let the settings hub fill it).

## Privacy

Messages and replies travel through Discord via the transport add-on. Home Assistant data is read locally. Nothing else leaves your network.

## More details

### What it stores

For each message: `seq`, message id, channel key, author id, time, text (up to 2000 characters), `reply_to`, attachment count and kinds, and whether the
author is on the transport allow-list. Rows of the table `queue_messages` in `/data/ih.db` (rows removed after `retention_days`, default 7; `seq` never repeats).
The message text is never written to the add-on log or to `/status`. Only allowed users get the emoji and are returned by the API (`all=1` shows the rest).

### Shadow mode (default)

`shadow: true`: the queue **is written**, but the reaction is sent with `dry: true` and logged as `[DRY] would react ...`. Nothing appears in Discord.
`/status` shows `would_total` and `last_would`. The file `/data/STOP_LIVE` forces the same at once.

### Setup (needs the transport options first; the transport row is the real switch)

1. In the `ih_discord` options add a `modules` row for this module with the **`react`** operation, for example
   `{"name":"ih_orchestration_inbox","signing_key":"<secret>","channels":["<key>"],"ops":["react"],"rate_per_min":60,"shadow":true}`,
   and make sure `<key>` exists as a channel key. Without `react` in `ops` the emoji is refused (messages are still queued).
2. Create an MQTT user for this module; put the same signing key here and in the transport row.
3. Set `channel_keys` (for example `["<key>"]`) and an `api_token` (any long random string). Without `api_token` the API refuses everything.
4. Start the add-on. Check `http://<host>:8800/status`: `bus.connected` true, `bus.last_heartbeat_s` small. Leave `shadow` on until the queue looks right, then turn it off.

### API (port 8800, header `Authorization: Bearer <api_token>`)

- `GET /queue?since=<seq>&channel=<key>&consumer=<name>&limit=<n>&all=1`: records with `seq` greater than `since`. Without `since` the consumer's cursor is used (default consumer `default`).
- `POST /ack` with `{"seq": N, "consumer": "name"}`: moves that consumer's cursor to N (never backwards, never past the newest). Each consumer has its own cursor.
- `POST /events` with `{"kind": "handoff", "source": "my_service", "ref": "unique-id", "text": "..."}`: stores one event from a local service in the same queue. `kind` is lower case letters, digits and `_` (not `message`); `source` and `ref` are letters, digits and `_ . : -`; `text` is cut at 2000 characters. The same `kind` and `ref` twice is stored once (answer `"stored": false`). Events are never reacted to and never posted anywhere.
- `GET /digest?consumer=<name>&wait=<seconds>`: `{"new": 3, "kinds": {"handoff": 2, "reaction": 1}, ...}` for that consumer's cursor. It holds no message text, so it is safe to show.
- `wait=<seconds>` (up to 25) on `/queue` and `/digest`: the request stays open until a record arrives or the time is over (long poll). `kind=<kind>` on `/queue` filters by kind; Discord messages have the kind `message`.
- `GET /status` and `GET /healthz`: no token, no secrets, no message text.

### One notice instead of many watchers

A reader that cannot hold a connection open (for example an interactive AI session) needs something to tell it that records are waiting. Run one small process next to the reader: it calls `/digest?wait=25` in a loop, and when `new` is above zero it gives the reader ONE short notice built from the digest (counts only, no text). The reader then calls `/queue`, handles the records and calls `/ack`. Records wait in the queue while the reader is restarting, so nothing is lost and nothing has to be re-armed.

### Data storage (database since 0.3.0)

The add-on keeps its data in one SQLite file, `/data/ih.db` (shared helper `ihlib/db.py`, WAL mode). Every file under `/data` was reviewed:

| File under `/data` | Where it lives now | Why |
|---|---|---|
| `queue/q-<seq>.jsonl` (stored messages and events) | Database: table `queue_messages` (seq, msg_id, channel_key, author_id, allowed, ts, received, text, reply_to, attachments, kind, source, ref) | Growing records with a time rule; the API output is identical |
| `cursors.json` (one cursor per consumer) | Database: table `queue_cursors` (consumer, seq) | Small state, now updated in one transaction |
| `options.json` | Stays a file | Supervisor owns it; holds the secrets (`signing_key`, `api_token`, `mqtt_password`, `ingest_token`). Secrets are never stored in the database or its snapshots |
| `STOP`, `STOP_LIVE` | Stay files | Kill switches: you create or delete them by hand |
| Add-on log (container log, last lines in `/status`) | Stays text | Logs stay text |
| `trash/` | New folder | Old queue files, deleted after 30 days |
| `backup/` | New folder | Nightly snapshots of `ih.db`, newest 3 |
| `degraded/` | New folder, only used when the database cannot be used | The old file queue (same layout as before), imported again after a restore |

**Start-up:** the add-on opens `ih.db`, applies numbered migrations (a snapshot is taken first when the file already has data), copies each old file into the database, checks the rows inside the same transaction, and only then moves the file to `/data/trash`. It purges the trash at start and once a day. From 04:00 it takes one snapshot a night into `/data/backup`, opens the copy read-only, runs the integrity check and logs the row counts (counts only, no text).

**Status:** `/status` shows `db`: schema version, rows per table, last snapshot time, snapshots kept, last restore check, trash files and bytes, import errors, and `degraded`. In `queue`, `segments` is 0, `bytes` is the database size and `rows` is the message count.

**If the database is damaged or too new:** the inbox keeps working. Status shows `db_degraded: true`, a `WARNING` and the error, and the log says `DATABASE DEGRADED`. Messages and cursors go to `/data/degraded/` (the old file layout). `/healthz` stays 200. The damaged file is moved aside as `ih.db.corrupt-<time>`; nothing is recreated.

**Restore** (add-on stopped): copy the newest file in `/data/backup` to `/data/ih.db`, delete `ih.db-wal` and `ih.db-shm`, start the add-on. Files in `/data/degraded/` are then imported automatically (their messages get new `seq` numbers; cursors keep the higher value, so a consumer may see those messages once). To go back to the previous add-on version: install it, and copy the files from `/data/trash` back to their old places (`/data/queue/`, `/data/cursors.json`).

**Tests:** `python3 -m unittest discover -s tests` from the add-on folder.

Untested on the real device: power loss, a restore from a Home Assistant backup, running two copies at once.
