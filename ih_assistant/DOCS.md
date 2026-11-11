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
| `facts_url` | No | Setting `facts_url`. |
| `facts_key` | No | Setting `facts_key`. |
| `giphy_api_key` | No | GIPHY key. With it, a reply that carries `gif` search words also sends one PG GIF picture (attached, not a link), if the persona's gif level allows and none went out in the last `gif_gap_minutes`. Half the time a fitting picture from the cache is reused. |
| `gif_gap_minutes` | No | Minimum minutes between two GIFs in one chat. Default 10. |
| `usage_autoreply` | No | Default true. When a person's message stays unanswered for 60 seconds while Claude usage is out, the persona's bot posts one automatic message (see Usage auto-reply). false = off. |
| `usage_sensor_extra_enabled` | No | Sensor that says `True` when extra usage credits are on. Default `sensor.claude_usage_andrew_pro_extra_usage_enabled`. |
| `usage_sensor_extra` | No | Sensor with the extra credits used, in percent of the limit. Default `sensor.claude_usage_andrew_pro_extra_usage`. |
| `usage_sensor_session` | No | Setting `usage_sensor_session`. |
| `usage_sensor_week` | No | Setting `usage_sensor_week`. |
| `usage_sensor_session_reset` | No | Setting `usage_sensor_session_reset`. |
| `usage_sensor_week_reset` | No | Setting `usage_sensor_week_reset`. |
| `personas` | No | One entry per person: `key`, `user_id`, `bot_token`, `quiet_start`, `quiet_end`, `max_per_day`, `checkins`, `can_tell`, `live`, and the optional planner settings... |

## Usage auto-reply

For every persona. A message from the person that is still unanswered after 60 seconds (the persona has sent nothing since) while Claude usage is out gets one automatic message through the persona's own bot: "I cannot read your message right now: I am out of Claude usage until HH:MM. Your message is saved here. I answer when it resets." The time is London time, `Day HH:MM` when more than 12 hours away, "until it resets" when unknown. At most one such message per persona per 10 minutes. Out = session or week sensor at 99 percent or more (default sensors `sensor.claude_usage_andrew_pro_*`, changeable with the `usage_sensor_*` options), unless extra usage is enabled and below 98 percent of its limit; unavailable or unknown sensors count as not out. The reset time is the session one, the week one when only the week is out, the later one when both are. Dry-run only logs. The message stays in the inbox and is answered after the reset. Messages older than 1 hour unanswered get no late notice. Kill switch: file `STOP_AUTOREPLY` in `/data`.

The old House Bot feature (`claude_scripts/house_bot/commands_service/features/usage_autoreply.py`) also answers in Andrew's Clara DM. To avoid two replies, create `claude_scripts/house_bot/commands_service/STOP_AUTOREPLY` once this add-on version is live and checked.

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
| `POST /persona/<key>/complete` | `{event_id}` records a completed bridge-command outcome for a tracked direct message; it never sends or replays. A normal reply is recorded only after Discord confirms a send that includes the matching `reply_to`. |
| `POST /persona/<key>/typing` | `{}`: shows the typing indicator once (live). `{msg_id}`: keeps it going every 8 s until the persona has posted after that message or 5 minutes pass, then queues `still_waiting` |
| `POST /persona/<key>/ask` | `{text, options?}` posts the question as the persona and adds tick, cross and question reactions (`options`: any of `yes`, `no`, `unsure`); the person's reactions come back as `reaction` events `{msg_id, emoji}` (live). Dry-run only logs |
| `POST /persona/<key>/profile` | merges a seed profile |
| `POST /persona/<key>/identity` | `{username?, avatar?, nickname?}` changes the persona's own Discord name (2..32 characters, no `discord`, `@`, `#`, `:` or code fence), picture (base64 PNG or JPEG, up to 1 MiB) and server nickname (`""` clears it). Limit: 2 username changes an hour per persona (429 with `retry_after`). The new name is saved in the persona profile. Dry-run only logs lengths |
| `GET /persona/<key>/plan` | today's planned check-in times (`time`, `kind`, `status`) and the agreed times. Times only |
| `POST /persona/<key>/agree` | `{date, time}` (`date`: `today`, `tomorrow` or `YYYY-MM-DD`; `time`: `HH:MM`) saves a time agreed with the person; it joins that day's plan first. 409 if `checkin_plan` is off |
| `POST /persona/<key>/agree/remove` | `{date, time}` removes an agreed time |
| `POST /persona/<key>/remind` | `{when, text, repeat?}` saves a reminder (`when`: local `YYYY-MM-DD HH:MM`, at least 60 s ahead; `text` up to 500 characters; `repeat`: `none` (default), `daily`, `weekly`, `monthly`, `yearly`; month ends are clamped). Returns `{ok, id}`. 400 bad or past time, 404 unknown persona, 429 more than 50 pending. When due, a `reminder_due` event (source `reminders`) is queued with `suggested_text`; up to 24 h late it fires once with `facts.late`, older is dropped |
| `GET /persona/<key>/reminders` | the pending reminders (`id`, `due`, `text`, `repeat`) |
| `POST /persona/<key>/remind/cancel` | `{id}` removes a reminder (404 if not found) |
| `POST /persona/<from>/tell/<to>` | `{text, kind?}` leaves one `from_persona` event in the other persona's inbox |

### Outcome progress

The add-on publishes only per-persona counters, timestamps, an opaque stream epoch, and whether that persona is live to `assistant/progress_<key>` in the orchestrator facts store. It does not publish message text, Discord IDs, or credentials. Configure the existing `assistant` fact client with `write: true`; the key remains scoped to the `assistant/*` namespace. Facts expire after five minutes and are refreshed every minute. A reply counts only after Discord confirms the send and the request includes the matching `reply_to`. The host bridge records a command outcome after its handler succeeds. Reading an inbox event alone never advances the completion watermark.

### Behaviour levels (0 to 10)
Per persona options, all optional: `emoji_level`, `gif_level`, `checkin_level`, `humour_level`, `formality_level`, `verbosity_level`, `banter_level`, `reminders_level`. Whole numbers 0 to 10; anything else is clamped (bad values fall back to the persona's default). Defaults are the same for every persona (emoji 2, gif 5, all others 5); set the persona options to change them. Optional `fixed_rule` (text, up to 300 characters) is added to the prompt block as a fixed line that the levels cannot change. Optional top-level `usage_sensor_session`, `usage_sensor_week`, `usage_sensor_session_reset`, `usage_sensor_week_reset` name the Home Assistant sensors used by the usage auto-reply. 0 = never, 5 = middle, 10 = as much as possible. Full scale: brain note `process/assistant-behaviour-levels`.

Hard gates in the add-on: `checkin_level` 0 sends no check-ins at all; otherwise the daily cap is `max_per_day` scaled by the level (at least 1) and the planned gap `min_gap_h` is stretched (level 5 = twice as long). `emoji_level` caps the emoji in each `reply` (0 removes all; extras are cut from the end). `gif_level`: `GET /behaviour` returns `gif_ok` (0 = always false, 10 = always true, else chance level/10); the engine must send a GIF only when it is true. The other levels shape the persona prompt only: add the `prompt` text from `GET /behaviour` to it. Levels change only in the add-on options (no chat command).

### Tell
A persona may tell only the keys in its own `can_tell` list (default none; 403 otherwise). Unknown persona 404. Limit 20 a day per pair (429). The add-on writes one event to the target's inbox and reads nothing of the target; the sender's log keeps only the length.

### Check-ins
**Fixed times.** At each time in `checkins` a `checkin_due` event is queued (`{slot, planned, reason: "fixed"}`), unless it is within quiet hours, the persona is already at `max_per_day` outgoing messages today, or the time passed more than 30 minutes ago (for example the add-on was off).

**Planned times** (per persona, option `checkin_plan`, default off). The add-on only plans and queues the event; your engine still writes the message. Each day, at the first tick, it builds a plan of `checkins_per_day` times (0 to 4, or a range such as `2-3` picked per date with a seed so the plan is stable; default 2): times saved with `agree` come first, then random times inside `wake_start`..`wake_end` (default 09:00 to 21:30). Random times avoid timed events in the `calendars` (all-day events never block), meal times, `busy_windows` and quiet hours, and are at least `min_gap_h` hours apart (default 3). Meal times are 12:30 to 13:30 and 18:00 to 19:15, or, if `meal_plan_entity` is a sensor whose state is today's eating time (`HH:MM` or a date-time), 60 minutes before to 45 after that time. The plan is stored in the persona's own folder and is made once a day (times already past are not invented). Within 30 minutes after a planned time the event `{type: "checkin_due", planned: "HH:MM", reason: "agreed|random"}` is queued, unless: quiet hours, `max_per_day` reached, the person messaged the persona in the last `quiet_if_messaged_h` hours (default 2; read from the persona's own inbox timestamps), another check-in fired within `min_gap_h` (random only), `presence_entity` says the person is away (random only), or the calendar or meal time has become busy (random only). An agreed time keeps its promise through all the "random only" checks.

`busy_windows` and `free_windows` are lists like `mon-fri 09:45-17:45` (days: `mon-fri`, `sat,sun`, `daily`). A free window cancels busy windows and the default meal windows inside it (for example `mon-fri 12:00-13:30` keeps a work-day lunch break open); calendar events and the `meal_plan_entity` eating time always block.

Also skipped (random only): the persona sent a reply or question in the last `quiet_if_messaged_h` hours (from its own outbox log), or its last message is newer than the person's last one and under 6 hours old (unanswered). Agreed times are exempt from these, the gap, presence and busy checks, but not from quiet hours, waking hours, `max_per_day` or the person having just messaged.

If Home Assistant was down when the day's plan was made, the add-on retries every minute and, once it answers, rebuilds the plan: only random times still in the future change; agreed times and finished ones stay.

**Calendar access (0.10.0).** Per persona: `calendar_read` (extra calendars it may read; the planning `calendars` are readable too) and `calendar_write` (calendars it may ADD events to; also readable). `GET /persona/<key>/calendar?days=14&start=YYYY-MM-DD` returns the events (soonest first, at most 80, up to 90 days). `POST /persona/<key>/calendar/add` with `{calendar, summary, start, end?, location?, description?}` adds one event: `start` is `YYYY-MM-DD` (all day) or `YYYY-MM-DD HH:MM` in local time; end defaults to one day or one hour. Add only: no edit, no delete. A calendar not in the persona's own `calendar_write` list is refused (403). Dry mode adds nothing. Each add is counted in the outbox (calendar name and title length, not the title).

**Calendar edit and delete (0.11.0).** Home Assistant can only create events on iCloud (CalDAV) calendars, so edit and delete talk to CalDAV directly with their own login: options `caldav_user`, `caldav_password` (an iCloud app-specific password) and `caldav_url` (default `https://caldav.icloud.com`). `POST /persona/<key>/calendar/edit` with `{calendar, uid, summary_was, summary?, start?, end?, location?, description?}` and `POST /persona/<key>/calendar/delete` with `{calendar, uid, summary_was}`. `uid` comes from the calendar listing. `summary_was` must match the event's current title (case-insensitive) or the call is refused (409) and the current title is returned: this stops a wrong uid from touching the wrong event. Only calendars in the persona's own `calendar_write` list; non-recurring events only (a recurring event is refused). Times are written in UTC. Dry mode changes nothing. Delete cannot be undone.

**Where everyone is (0.12.0).** `GET /persona/<key>/where` (read only). Per persona option `location_entities`: entity ids that tell where THIS person is (a `person.*` or `device_tracker.*` with a GPS position, and/or a home/away switch such as `switch.x_home`); `presence_entity` is used too. The best signal is GPS (state, named place, distance from home in km, accuracy), with the home/away switch as the fallback; all signals are returned and a disagreement is flagged. The answer also has `household` (everyone else: home or away only, nothing more) and `calendar_now` (timed events from the persona's readable calendars from 30 minutes ago to 3 hours ahead, with their location text) so the persona can check the two against each other.

**Dated reminders (0.13.0).** Per persona: `dates` (lines `YYYY-MM-DD|Name|family` for a birth date, so the age is shown, or `MM-DD|Name|other`), `dates_leads` (days before, default `7,3`), `dates_at` / `dates_latest` (window, default 18:00 to 21:30). Once a day inside the window the add-on queues one `reminder_due` event (source `dates`) with the facts and a suggested text; the persona writes the message in its own voice. Quiet hours are respected, and a day is marked done before the event is queued, so it never repeats.

Calendars, the meal sensor and the presence entity are read from Home Assistant (`homeassistant_api` is on). If Home Assistant cannot be reached the plan is still made, without calendars, and the log names the error type.

**Not ported from the host script:** the game follow-up question after a gaming session. The Games add-on sends `game_session` events to the persona instead.

### Push from Discord (0.15.0)
Each persona bot keeps one connection to the Discord Gateway (a websocket; code in `app/gateway.py`, standard library only). When the person writes or reacts in the private chat, Discord announces it on that connection and the add-on fetches the message at once with the normal REST call. The Gateway is used as a wake-up signal only: message text is read in one place (the polling code) and is never logged.

- **Option** `discord_push` (default `true`). `false` = no Gateway connection, polling every 3 s as before 0.15.0.
- **Fallback.** Polling never stops. Push `ready`: messages and reactions are checked every 60 s as a safety net, and at once on a push (then every 3 s for 10 s). Any other state (`connecting`, `failed`, `off`): every 3 s (reactions every 20 s), exactly as before.
- **States** in `GET /status` (`push` per persona): `ready` (identified or resumed), `connecting` (first connect, or waiting to reconnect), `failed` (Discord refused the token or the intents; polling only until the add-on restarts), `off`.
- **Connection care.** Heartbeat at the interval Discord gives; a heartbeat that is not acknowledged before the next one closes the connection and resumes. After a drop the session is resumed (missed events are replayed by Discord); if Discord says the session is invalid a new one is started. Reconnect waits 1 s, doubling to 5 minutes, with random jitter.
- **Intents:** `DIRECT_MESSAGES` + `DIRECT_MESSAGE_REACTIONS`. Nothing needs switching on in the Discord developer portal.
- **Dry-run:** the connection is read-only, so it runs in dry-run too (as polling does).
- **Log lines** (no content, ids or token): `gateway ready`, `gateway closed (code N)`, `gateway reconnect in N s`, `gateway connect failed (ErrorType)`.
- The bot shows as online in Discord while connected.

### Dry-run
A persona sends only when `mode` is `live` **and** its own `live` is true. Reading is not gated: in dry-run the add-on still polls the person's private messages (read-only) into the inbox. Posting, reactions and typing are logged only. Message text is never written to the log, only lengths. File `/data/STOP` makes the API answer 503.
# Request traces (0.9.0)
When `ingest_token` is populated, each newly received direct message creates a content-free trace. The settings hub provisions the existing Orchestrator `ingest_token`; the URL defaults to the existing facts URL or the local Orchestrator address. A successful reply linked with `reply_to` adds the external processing interval and Discord send. Message text, Discord IDs and reply text are never uploaded. This shows the host Claude bridge as one external-processing interval; it does not expose Claude's internal tool/model steps. Tracing is best-effort and off when the token is blank.

Optional persona `sender_label` supplies a short non-sensitive trace label (1–48 characters, starting with a letter; letters, digits, underscores, periods and hyphens only). It defaults to `unknown`. Do not use user IDs or message text.
