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
| `personas` | No | One entry per person: `key`, `user_id`, `bot_token`, `quiet_start`, `quiet_end`, `max_per_day`, `checkins`, `can_tell`, `live`, and the optional planner settings... |

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
| `GET /persona/<key>/plan` | today's planned check-in times (`time`, `kind`, `status`) and the agreed times. Times only |
| `POST /persona/<key>/agree` | `{date, time}` (`date`: `today`, `tomorrow` or `YYYY-MM-DD`; `time`: `HH:MM`) saves a time agreed with the person; it joins that day's plan first. 409 if `checkin_plan` is off |
| `POST /persona/<key>/agree/remove` | `{date, time}` removes an agreed time |
| `POST /persona/<from>/tell/<to>` | `{text, kind?}` leaves one `from_persona` event in the other persona's inbox |

### Tell
A persona may tell only the keys in its own `can_tell` list (default none; 403 otherwise). Unknown persona 404. Limit 20 a day per pair (429). The add-on writes one event to the target's inbox and reads nothing of the target; the sender's log keeps only the length.

### Check-ins
**Fixed times.** At each time in `checkins` a `checkin_due` event is queued (`{slot, planned, reason: "fixed"}`), unless it is within quiet hours, the persona is already at `max_per_day` outgoing messages today, or the time passed more than 30 minutes ago (for example the add-on was off).

**Planned times** (per persona, option `checkin_plan`, default off). The add-on only plans and queues the event; your engine still writes the message. Each day, at the first tick, it builds a plan of `checkins_per_day` times (0 to 4, or a range such as `2-3` picked per date with a seed so the plan is stable; default 2): times saved with `agree` come first, then random times inside `wake_start`..`wake_end` (default 09:00 to 21:30). Random times avoid timed events in the `calendars` (all-day events never block), meal times, `busy_windows` and quiet hours, and are at least `min_gap_h` hours apart (default 3). Meal times are 12:30 to 13:30 and 18:00 to 19:15, or, if `meal_plan_entity` is a sensor whose state is today's eating time (`HH:MM` or a date-time), 60 minutes before to 45 after that time. The plan is stored in the persona's own folder and is made once a day (times already past are not invented). Within 30 minutes after a planned time the event `{type: "checkin_due", planned: "HH:MM", reason: "agreed|random"}` is queued, unless: quiet hours, `max_per_day` reached, the person messaged the persona in the last `quiet_if_messaged_h` hours (default 2; read from the persona's own inbox timestamps), another check-in fired within `min_gap_h` (random only), `presence_entity` says the person is away (random only), or the calendar or meal time has become busy (random only). An agreed time keeps its promise through all the "random only" checks.

`busy_windows` and `free_windows` are lists like `mon-fri 09:45-17:45` (days: `mon-fri`, `sat,sun`, `daily`). A free window cancels busy windows and the default meal windows inside it (for example `mon-fri 12:00-13:30` keeps a work-day lunch break open); calendar events and the `meal_plan_entity` eating time always block.

Also skipped (random only): the persona sent a reply or question in the last `quiet_if_messaged_h` hours (from its own outbox log), or its last message is newer than the person's last one and under 6 hours old (unanswered). Agreed times are exempt from these, the gap, presence and busy checks, but not from quiet hours, waking hours, `max_per_day` or the person having just messaged.

If Home Assistant was down when the day's plan was made, the add-on retries every minute and, once it answers, rebuilds the plan: only random times still in the future change; agreed times and finished ones stay.

Calendars, the meal sensor and the presence entity are read from Home Assistant (`homeassistant_api` is on). If Home Assistant cannot be reached the plan is still made, without calendars, and the log names the error type.

**Not ported from the host script:** the game follow-up question after a gaming session. The Games add-on sends `game_session` events to the persona instead.

### Dry-run
A persona sends only when `mode` is `live` **and** its own `live` is true. Reading is not gated: in dry-run the add-on still polls the person's private messages (read-only) into the inbox. Posting, reactions and typing are logged only. Message text is never written to the log, only lengths. File `/data/STOP` makes the API answer 503.
