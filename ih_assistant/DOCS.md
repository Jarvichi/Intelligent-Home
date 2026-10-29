# Intelligent Home - Assistant - Personas

## What it does

Personal assistant transport for several personas in one add-on: reads each person's private Discord messages through that persona's own bot, queues check-ins and sends replies written elsewhere. Every persona has a separate datastore. Dry-run by default. Stand-alone family member.

## Requires

- No other Intelligent Home add-on is required.
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Assistant - Personas**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `api_token`.
4. Start the add-on and read the log. It starts in dry-run mode, so it changes nothing yet.
5. When the log looks right, set `mode` to `live`.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `mode` | No | `dry` logs what it would do and changes nothing; `live` acts. Default: `dry`. |
| `api_token` | Yes | Bearer token callers must send. Empty = everything refused. |
| `timezone` | No | Time zone for check-ins and quiet hours. Default: `Europe/London`. |
| `personas` | No | One entry per person: `key`, `user_id`, `bot_token`, `quiet_start`, `quiet_end`, `max_per_day`, `checkins`, `can_tell`, `live`. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `mode` is `dry`. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Nothing is sent.** A persona sends only when `mode` is `live` and its own `live` is true. In dry-run the log says `[DRY] would send N chars`.
- **401 on every call.** Send the header `Authorization: Bearer <api_token>`. An empty `api_token` refuses everything.
- **No messages arrive in the inbox.** Check the persona's `user_id` and `bot_token`, and that the person can message that bot privately. The first start only follows new messages.

## Privacy

Private messages are read from and sent to Discord through the bot of each persona. Each persona keeps its own folder in the add-on data; message text is stored there only in its inbox and is never written to the log (lengths only). Nothing is shared between personas except messages one persona is explicitly allowed to send another.

## More details

### Separate datastores
Every persona has its own folder `/data/personas/<key>/` with `profile.json`, `state.json`, `inbox.jsonl` and `outbox.jsonl`. Code that touches a persona's files goes through one store object bound to that folder. No call takes a file path, no call reads another persona's folder, and links are refused.

### Nameless start
A new persona has an empty profile (no name, no tone). Before its first outgoing action it queues an `intro_due` event; your engine writes the introduction. Seed a profile with `POST /persona/<key>/profile` (fields `name`, `person_name`, `tone`, `notes`, `seeded_from`, `introduced`; unknown fields are ignored and long text is refused).

### Endpoints (all but `/healthz` need `Authorization: Bearer <api_token>`)
| Path | Does |
|---|---|
| `GET /healthz` | `ok` |
| `GET /status` | mode and per-persona counts (no ids, no text) |
| `GET /persona/<key>/inbox?after=<id>&limit=<n>` | events after an id: `message`, `checkin_due`, `intro_due`, `from_persona`, `reaction`, `still_waiting` |
| `POST /persona/<key>/reply` | `{text, reply_to?}` sends as that persona's bot (live) or only logs `[DRY] would send N chars` |
| `POST /persona/<key>/typing` | `{}`: shows the typing indicator once (live). `{msg_id}`: keeps it going every 8 s until the persona has posted after that message or 5 minutes pass, then queues `still_waiting` |
| `POST /persona/<key>/ask` | `{text, options?}` posts the question as the persona and adds tick, cross and question reactions (`options`: any of `yes`, `no`, `unsure`); the person's reactions come back as `reaction` events `{msg_id, emoji}` (live). Dry-run only logs |
| `POST /persona/<key>/profile` | merges a seed profile |
| `POST /persona/<key>/identity` | `{username?, avatar?, nickname?}` changes the persona's own Discord name (2..32 characters, no `discord`, `@`, `#`, `:` or code fence), picture (base64 PNG or JPEG, up to 1 MiB) and server nickname (`""` clears it). Limit: 2 username changes an hour per persona (429 with `retry_after`). The new name is saved in the persona profile. Dry-run only logs lengths |
| `POST /persona/<from>/tell/<to>` | `{text, kind?}` leaves one `from_persona` event in the other persona's inbox |

### Tell
A persona may tell only the keys in its own `can_tell` list (default none; 403 otherwise). Unknown persona 404. Limit 20 a day per pair (429). The add-on writes one event to the target's inbox and reads nothing of the target; the sender's log keeps only the length.

### Check-ins
At each time in `checkins` a `checkin_due` event is queued, unless it is within quiet hours, the persona is already at `max_per_day` outgoing messages today, or the time passed more than 30 minutes ago (for example the add-on was off).

### Dry-run
A persona sends only when `mode` is `live` **and** its own `live` is true. Reading is not gated: in dry-run the add-on still polls the person's private messages (read-only) into the inbox. Posting, reactions and typing are logged only. Message text is never written to the log, only lengths. File `/data/STOP` makes the API answer 503.
