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
| `usage_sensor_extra_enabled` | No | Sensor that says `True` when extra usage credits are on. Default `sensor.claude_usage_andrew_pro_extra_usage_enabled`. | <!-- privacy-scan: ignore LOCAL_TERM -->
| `usage_sensor_extra` | No | Sensor with the extra credits used, in percent of the limit. Default `sensor.claude_usage_andrew_pro_extra_usage`. | <!-- privacy-scan: ignore LOCAL_TERM -->
| `usage_sensor_session` | No | Setting `usage_sensor_session`. |
| `usage_sensor_week` | No | Setting `usage_sensor_week`. |
| `usage_sensor_session_reset` | No | Setting `usage_sensor_session_reset`. |
| `usage_sensor_week_reset` | No | Setting `usage_sensor_week_reset`. |
| `presence_entities` | No | List of `name=entity` texts: each person's home/away switch (read only), shared by every persona. See "Who is home". Default: empty. |
| `inbox_events` | No | `shadow` (default) or `live`. Applies to personas with `inbox_reactions: true`: their person's emoji reactions on the persona bot's messages are posted to the orchestration inbox (`kind` `reaction`, source `dm-<key>`, ref = message id + emoji). Shadow only logs "would post" (no text). |
| `inbox_url` | No | Orchestration inbox address. Default `http://172.30.32.1:8800`. |
| `inbox_token` | Needed for live | The inbox add-on's `api_token` (secret). |
| `personas` | No | One entry per person: `key`, `user_id`, `bot_token`, `quiet_start`, `quiet_end`, `max_per_day`, `checkins`, `can_tell`, `live`, `inbox_reactions` (post this person's reactions to the inbox), and the optional planner settings... |

## Usage auto-reply

For every persona. A message from the person that is still unanswered after 60 seconds (the persona has sent nothing since) while Claude usage is out gets one automatic message through the persona's own bot: "I cannot read your message right now: I am out of Claude usage until HH:MM. Your message is saved here. I answer when it resets." The time is London time, `Day HH:MM` when more than 12 hours away, "until it resets" when unknown. At most one such message per persona per 10 minutes. Out = session or week sensor at 99 percent or more (default sensors `sensor.claude_usage_andrew_pro_*`, changeable with the `usage_sensor_*` options), unless extra usage is enabled and below 98 percent of its limit; unavailable or unknown sensors count as not out. The reset time is the session one, the week one when only the week is out, the later one when both are. Dry-run only logs. The message stays in the inbox and is answered after the reset. Messages older than 1 hour unanswered get no late notice. Kill switch: file `STOP_AUTOREPLY` in `/data`. <!-- privacy-scan: ignore LOCAL_TERM -->

Own wording per persona (0.16.1): options `usage_texts` (reset time known) and `usage_texts_notime` (reset time unknown), each a list of up to 10 strings of 1 to 500 characters. One variant is picked at random per notice, never the same one twice in a row for that persona. `{time}` in a variant is replaced by the reset time text described above (`19:30` or `Thu 05:00`; `it resets` in the no-time list); a variant without `{time}` is fine. A variant with any other `{placeholder}` or stray brace is skipped, never sent. Order of use: `usage_texts` (non-empty and usable) > `usage_text` (single text, `{t}` for the time) > built-in wording; with no reset time: `usage_texts_notime` > `usage_text_notime` > built-in "until it resets" wording. Example: `"usage_texts": ["Back at {time}, saved your message", "Saved your message, will answer later"]`.

The old House Bot feature (`claude_scripts/house_bot/commands_service/features/usage_autoreply.py`) also answers in Andrew's Clara DM. To avoid two replies, create `claude_scripts/house_bot/commands_service/STOP_AUTOREPLY` once this add-on version is live and checked. <!-- privacy-scan: ignore LOCAL_TERM -->

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `mode` is `dry`. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Nothing is sent.** A persona sends only when `mode` is `live` and its own `live` is true. In dry-run the log says `[DRY] would send N chars`.
- **401 on every call.** Send the header `Authorization: Bearer <api_token>`. An empty `api_token` refuses everything.
- **No messages arrive in the inbox.** Check the persona's `user_id` and `bot_token`, and that the person can message that bot privately. The first start only follows new messages.

## Privacy

Private messages are read from and sent to Discord through the bot of each persona. Each persona keeps its own folder in the add-on data; message text is stored only in that persona's inbox file (and in reminders the person asks for) and is never written to the log or to `/status` (lengths only). Nothing is shared between personas except messages one persona is explicitly allowed to send another.

## More details

### Separate datastores
Every persona has its own rows in the database (its own key-value namespace and its own outbox rows) and its own folder `/data/personas/<key>/` for its inbox file. Code that touches a persona's data goes through one store object bound to that persona. No call takes a file path, no call reads another persona's data, and links are refused. See "Data storage" below.

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
| `POST /persona/<key>/usage_texts` | `{texts, notime}` (0.17.0) the persona's own out-of-usage notice variants, stored in its own data store: `texts` for a known reset time (may use `{time}`), `notime` for an unknown one. Each list up to 10 texts of 1 to 500 characters; only `{time}` is allowed as a placeholder; one bad entry gives 400 with the reason and nothing is stored. Replaces both stored lists; two empty lists clear the store. 429 after 20 accepted writes a day. Store lists beat the `usage_texts` options, which beat `usage_text`, which beats the built-in wording. Returns the effective view |
| `GET /persona/<key>/usage_texts` | `{texts, notime, source, source_texts, source_notime}`: the lists in use now; source is `store`, `options` or `builtin` |
| `GET /persona/<key>/plan` | today's planned check-in times (`time`, `kind`, `status`: pending, deferred, sent, skipped, missed) and the agreed times. Times only |
| `POST /persona/<key>/agree` | `{date, time}` (`date`: `today`, `tomorrow` or `YYYY-MM-DD`; `time`: `HH:MM`) saves a time agreed with the person; it joins that day's plan first. 409 if `checkin_plan` is off |
| `POST /persona/<key>/agree/remove` | `{date, time}` removes an agreed time |
| `POST /persona/<key>/remind` | `{when, text, repeat?}` saves a reminder (`when`: local `YYYY-MM-DD HH:MM`, at least 60 s ahead; `text` up to 500 characters; `repeat`: `none` (default), `daily`, `weekly`, `monthly`, `yearly`; month ends are clamped). Returns `{ok, id}`. 400 bad or past time, 404 unknown persona, 429 more than 50 pending. When due, a `reminder_due` event (source `reminders`) is queued with `suggested_text`; up to 24 h late it fires once with `facts.late`, older is dropped |
| `POST /persona/<key>/remind` (arrival, 0.18.0; `person` 0.19.0) | `{trigger: "arrive_home", person?, text, expires_h?}` instead of `when`: fires once when that person's presence switch goes off -> on (arrival at home); `person` is a name from `presence_entities`, default the persona's own person, as the same `reminder_due` event with `facts.trigger`. Expires after 24 h by default (`expires_h` more than 0 and up to 168). 400 if no presence switch is known for the person (or `person` is not a known name) or `repeat` is not `none`. The first state seen after a start is only remembered, so an arrival is never guessed |
| `GET /persona/<key>/reminders` | the pending reminders (`id`, `due`, `text`, `repeat`; arrival reminders have `trigger`, `expires_at` and `due` null) |
| `POST /persona/<key>/remind/cancel` | `{id}` removes a reminder (404 if not found) |
| `POST /persona/<from>/tell/<to>` | `{text, kind?}` leaves one `from_persona` event in the other persona's inbox |

### Who is home (0.19.0)
Shared option `presence_entities` (list of `name=entity`, for example `person_a=switch.person_a_home`; up to 8) names each person's home/away switch. Every persona reads it, read only (the add-on never writes to these switches). The per-persona `presence_entity` still works as before and is the default for the persona's own person; `presence_person` names that person (default: the name in `presence_entities` whose entity equals `presence_entity`). Events carry `home_now` (names at home, comma separated, or `nobody`; names only). Arrival reminders can name any person (`person`), see the endpoints table.

### Outcome progress

The add-on publishes only per-persona counters, timestamps, an opaque stream epoch, and whether that persona is live to `assistant/progress_<key>` in the orchestrator facts store. It does not publish message text, Discord IDs, or credentials. Configure the existing `assistant` fact client with `write: true`; the key remains scoped to the `assistant/*` namespace. Facts expire after five minutes and are refreshed every minute. A reply counts only after Discord confirms the send and the request includes the matching `reply_to`. The host bridge records a command outcome after its handler succeeds. Reading an inbox event alone never advances the completion watermark.

### Behaviour levels (0 to 10)
Per persona options, all optional: `emoji_level`, `gif_level`, `checkin_level`, `humour_level`, `formality_level`, `verbosity_level`, `banter_level`, `reminders_level`. Whole numbers 0 to 10; anything else is clamped (bad values fall back to the persona's default). Defaults are the same for every persona (emoji 2, gif 5, all others 5); set the persona options to change them. Optional `fixed_rule` (text, up to 300 characters) is added to the prompt block as a fixed line that the levels cannot change. Optional top-level `usage_sensor_session`, `usage_sensor_week`, `usage_sensor_session_reset`, `usage_sensor_week_reset` name the Home Assistant sensors used by the usage auto-reply. 0 = never, 5 = middle, 10 = as much as possible. Full scale: brain note `process/assistant-behaviour-levels`.

Hard gates in the add-on: `checkin_level` 0 sends no check-ins at all; otherwise the daily cap is `max_per_day` scaled by the level (at least 1) and the planned gap `min_gap_h` is stretched (level 5 = twice as long). `emoji_level` caps the emoji in each `reply` (0 removes all; extras are cut from the end). `gif_level`: `GET /behaviour` returns `gif_ok` (0 = always false, 10 = always true, else chance level/10); the engine must send a GIF only when it is true. The other levels shape the persona prompt only: add the `prompt` text from `GET /behaviour` to it. Levels change only in the add-on options (no chat command).

Check-in offer (0.20.0): `GET /behaviour` also returns `checkin_offer` and `offer_prompt` (already added to `prompt`). When true, the persona asks once "Would you like me to check in more often?" and reports with `POST /persona/<key>/checkin_offer` `{"answer": "asked"|"no"|"yes"}`. At most one ask per 7 days; `no` stops it for good; `yes` pauses it and the persona posts a request for the owner to raise `checkin_level` or add a planned slot (the add-on cannot change its own options). Not offered at level 10.

### Tell
A persona may tell only the keys in its own `can_tell` list (default none; 403 otherwise). Unknown persona 404. Limit 20 a day per pair (429). The add-on writes one event to the target's inbox and reads nothing of the target; the sender's log keeps only the length.

### Check-ins
**Fixed times.** At each time in `checkins` a `checkin_due` event is queued (`{slot, planned, reason: "fixed"}`), unless it is within quiet hours, the persona is already at `max_per_day` outgoing messages today, or the time passed more than 30 minutes ago (for example the add-on was off).

**Planned times** (per persona, option `checkin_plan`, default off). The add-on only plans and queues the event; your engine still writes the message. Each day, at the first tick, it builds a plan of `checkins_per_day` times (0 to 4, or a range such as `2-3` picked per date with a seed so the plan is stable; default 2): times saved with `agree` come first, then random times inside `wake_start`..`wake_end` (default 09:00 to 21:30). Random times avoid timed events in the `calendars` (all-day events never block), meal times, `busy_windows` and quiet hours, and are at least `min_gap_h` hours apart (default 3). Meal times are 12:30 to 13:30 and 18:00 to 19:15, or, if `meal_plan_entity` is a sensor whose state is today's eating time (`HH:MM` or a date-time), 60 minutes before to 45 after that time. The plan is stored in the persona's own folder and is made once a day (times already past are not invented). Within 30 minutes after a planned time the event `{type: "checkin_due", planned: "HH:MM", reason: "agreed|random"}` is queued, unless: quiet hours, `max_per_day` reached, the person messaged the persona in the last `quiet_if_messaged_h` hours (default 2; read from the persona's own inbox timestamps), another check-in fired within `min_gap_h` (random only), `presence_entity` says the person is away (random only), or the calendar or meal time has become busy (random only). An agreed time keeps its promise through all the "random only" checks.

`busy_windows` and `free_windows` are lists like `mon-fri 09:45-17:45` (days: `mon-fri`, `sat,sun`, `daily`). A free window cancels busy windows and the default meal windows inside it (for example `mon-fri 12:00-13:30` keeps a work-day lunch break open); calendar events and the `meal_plan_entity` eating time always block.

Deferred (0.29.0): a slot held back for a soft reason (messaged recently, spoke recently, unanswered, too soon, not home, busy now) is `deferred` and re-checked every 15 minutes until the earlier of 3 hours or 15 minutes before `wake_end`, then sent or `missed`. Quiet hours, `max_per_day` and outside waking hours end the slot at once. Replies and questions to automated `reminder_due` events are marked `auto` (send `auto: true`, or the add-on notices a `reminder_due` as the newest event) and do not count for "spoke recently" or "unanswered".

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

### Data storage (database since 0.18.0)

The add-on keeps its own working data in one SQLite file, `/data/ih.db` (shared helper `ihlib/db.py`, numbered migrations, schema version 1). Nothing is encrypted at rest, before or after: the old files were plain files in `/data` (mode 0600) and the database file is plain too, in the same volume, with the same access. Private chats of the people live elsewhere (Claude's private brain, encrypted) and are not part of this add-on.

| Old file | Now | Why |
|---|---|---|
| `personas/<key>/state.json` (reminders, check-in plans and agreed times, dates already sent, last-seen ids, usage notice texts and counters, rate counters, reply-progress watermarks) | Database: key-value row, namespace `<key>`, name `state` | Small state, one writer at a time per persona. Always read and rewritten whole, as before |
| `personas/<key>/profile.json` | Database: key-value row, namespace `<key>`, name `profile` | Small state |
| `personas/<key>/outbox.jsonl` (kind, time, lengths: no text) | Database: table `outbox_log` (newest 1000 rows per persona) | Growing log with a count and a "newest of kind" query |
| `personas/<key>/inbox.jsonl` (the people's message text) | **Stays a file** | Holds private message text. Moving it adds no protection and no feature, and the reply-progress check compares it with the state row. Left alone on purpose in this step |
| `options.json` (tokens, passwords, keys) | Stays a file | Supervisor owns it. Secrets are never stored in the database or its snapshots |
| `STOP`, `STOP_AUTOREPLY` | Stay files | Kill switches you create by hand |
| `gif_cache.json`, `gifs/` | Stay files | Rebuildable cache and pictures |
| add-on log | Stays text | Human readable |
| `trash/<key>/` | New folder | Old JSON files wait here 30 days, then are deleted |
| `backup/` | New folder | Nightly snapshots of `ih.db`, newest 3 |
| `degraded/` | New folder, only used when the database cannot be used | Plain files, imported again after a restore |

**Start-up:** the add-on opens `ih.db`, applies numbered migrations (a snapshot is taken first when the file already holds data), and for each persona copies the old JSON files into the database, checks the rows inside the same transaction, and only then moves the file to `/data/trash/<key>/`. A file that fails the check (for example it is not valid JSON) stays where it is and is named in `/status` under `db.legacy_import_errors`. The trash is purged at start and once a day. From 04:00 local time one snapshot a night goes to `/data/backup`; the copy is opened read-only, checked, and logged with row counts only.

**Threads:** the database is used by one long-lived worker thread; the persona workers and HTTP threads hand their calls to it. Each persona keeps its own lock around read-change-write, so two requests cannot lose each other's change.

**Status:** `/status` has `db` (schema version, rows per table, last snapshot, snapshots kept, last restore check, trash files and bytes, import errors, error) and `db_degraded`. Counts only: never message or reminder text.

**If the database is damaged or too new:** the add-on keeps running. `/status` shows `db_degraded: true` and a `WARNING`, and the log says `DATABASE DEGRADED`. State goes to plain files in `/data/degraded/personas/<key>/`, first filled from the newest snapshot. The damaged file is moved aside as `ih.db.corrupt-<time>`; a new empty database is never made by itself (a too-new file is left untouched). In this mode the outbox log starts empty. **If a single database call fails while the file is open** (disk full, locked), the call fails and the error shows in `db.error`; it is not redirected to files, so the data is never split in two.

**Restore** (add-on stopped): copy the newest file in `/data/backup` to `/data/ih.db`, delete `ih.db-wal` and `ih.db-shm`, start the add-on. Files in `/data/degraded/` are imported automatically. To go back to version 0.17.0: install it, then copy the files in `/data/trash/<key>/` back to `/data/personas/<key>/`.

### Dry-run
A persona sends only when `mode` is `live` **and** its own `live` is true. Reading is not gated: in dry-run the add-on still polls the person's private messages (read-only) into the inbox. Posting, reactions and typing are logged only. Message text is never written to the log, only lengths. File `/data/STOP` makes the API answer 503.
# Request traces (0.9.0)
When `ingest_token` is populated, each newly received direct message creates a content-free trace. The settings hub provisions the existing Orchestrator `ingest_token`; the URL defaults to the existing facts URL or the local Orchestrator address. A successful reply linked with `reply_to` adds the external processing interval and Discord send. Message text, Discord IDs and reply text are never uploaded. This shows the host Claude bridge as one external-processing interval; it does not expose Claude's internal tool/model steps. Tracing is best-effort and off when the token is blank.

Optional persona `sender_label` supplies a short non-sensitive trace label (1–48 characters, starting with a letter; letters, digits, underscores, periods and hyphens only). It defaults to `unknown`. Do not use user IDs or message text.

### Hearth: the shared household assistant (0.22.0)
Hearth belongs to the whole house, not to one person. Add a persona with `key: hearth`, `household: true` and (optional) `household_users`; it needs no Discord bot. Hearth decides **what** to say and sends the text to the **Intelligent Home - Speaker** add-on (`speaker_url`, `speaker_token`), which speaks it and enforces the quiet-hour and per-persona rules. It is off until the `hearth` option has `enabled: true`, and dry until both `mode` and the persona's `live` are on (dry = it asks the speaker for a `dry_run` and logs the answer).

- **Bedtime message.** `alarm_entity` goes armed, or fails to arm. `bedroom_lamps`: silent unless a lamp is on or went off less than 5 minutes ago (a failed arming always speaks). Items: `locks` and `doors` (`entity=spoken name`), `lights_downstairs`, `lights_outside`, `dishwasher_state_entity` plus `dishwasher_unload_entity`. Frost line from `forecast_entity` (attribute `daily`, first day `templow`) at or below `frost_at`. Tomorrow line from `workday_calendar` (an all-day event on the day = work day, wake-up time from `wake_up_entity`), `own_calendar` (events matching `off_pattern` cancel the work day) and `event_calendars` (first timed event before 12:00 for a lie-in). `lock_door_pairs` (`lock=door sensor`): if a lock is locked but its sensor reads open, Hearth says it is just checking, waits up to 12 s, then reports.
- **Picard welcome.** `hearth.picard`: `door_entity`, `person_switch`, `person_name`, `speaker`, `voice` (a voice name from the speaker add-on), `home_minutes`, `window_start`, `window_end`, `min_gap_min`, `lines` (use `{name}`). Volume is left alone.
- **Preview.** `GET /hearth/preview?event=armed` (bearer token) shows the text Hearth would say now and plays nothing.

The Home Assistant script and automations that do this today stay on until each cut-over is approved; switch them off only after hearing the add-on path once.

## Private maintenance prompts (0.25.0)

Per persona option `autonomy_level` (0 to 10, default 3, 0 = off). Once a day the add-on may queue one silent `self_prompt` event (type `self_prompt`, `silent: true`, `prompt_id`, `text`) taken from `app/self_prompts.json`. Roughly 1 prompt a week at level 3, 3 at level 5, 7 at level 10. The event text starts "INTERNAL, NOT FROM <user>": the persona answers privately, may act (one setting, memory, one message) and nothing goes to Discord unless the persona itself writes. It then calls `POST /persona/<key>/selfdone` with `{"prompt_id": "...", "action": "none|setting|memory|message|identity|other", "outcome": "one short line"}`. `GET /persona/<key>/selflog` lists the audit (time, prompt id, status, action, line).

Fixed rules in code: not in quiet hours; not within 2 h of a check-in or reminder; 12 h minimum between prompts; weekly cap from the level; per-prompt cooldown (7 to 90 days); a missed day is dropped, never fired late; none for dry or household personas. A persona cannot change its own add-on options, tokens or permissions (it has no call for it). `identity` takes `avatar_file` (file name inside `/data/avatars`); inside an open prompt the nickname and the picture can each change once in 30 days and a picture must come from that folder.

## Special days (0.26.0)

Uses the existing `dates` option. For the persona's own person (name or first-name match) the add-on queues a `self_prompt` event (`source: special_day`, not silent) with `prompt_id` `special_day_ahead` (3 days before), `special_day_morning` (the persona sends one personal greeting) or `special_day_after`. A `dates` line with kind `anniversary` (`MM-DD|Name|anniversary`) is an anniversary; every other kind is a birthday. Once per date, never in quiet hours. On the day `GET /persona/<key>/behaviour` returns `special_day: true` and one extra line in `prompt`.

### New releases and tastes (0.27.0)

Weekly private prompts `new_releases`, `taste_followup`, `taste_use` (group `discovery`, autonomy level 3 and up). The persona suggests at most one film, album or game that fits the saved tastes, never a purchase link, nothing if the person said no. State is kept per persona: `POST /persona/<key>/asked` `{"kind":"film|album|game","creator":"","title":"","date":"","answer":"yes|no|later|liked|disliked"}` (a repeat is `duplicate: true`), `POST /persona/<key>/taste` `{"key":"film:sci-fi","verdict":"liked|disliked|clear"}`, `POST /persona/<key>/discovery` `{"allowed":false}`, `GET /persona/<key>/asked` (list and tastes).
