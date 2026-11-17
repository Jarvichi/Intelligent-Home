# IH Displays

Profiles describe display capabilities: type, dimensions, colour, touch, power, transport and refresh interval. The private database lives in the add-on's `/data` and is backed up with the app. It starts empty; no household devices are preconfigured. Values are settings, not evidence that a device is connected or working.

Open the administrator-only ingress page to add, edit or remove a profile. Changes use revisions to reject stale edits. Each profile displays its ID for use when configuring an adapter. The private API offers `GET /v1/profiles` and `GET /v1/profiles/<id>` with `Authorization: Bearer <api_token>`; it is disabled if the token is empty and has no write routes. Do not publish the API port to the LAN. `/healthz` reports service health only.

This foundation does not connect to an e-reader, TV or ESP32, render images, change touch actions, or replace the existing displays. The display adapters, device discovery, access model and compatibility handover require future work. Existing displays continue independently if this add-on is unavailable. No secrets, device addresses, tokens or household records belong in profile fields or the public repository.

## Display messages

Optionally list up to 12 Home Assistant `input_text` entity IDs in `message_entities`. Each nonempty state is read as `title|detail`; a missing detail is allowed. The authenticated read-only `GET /v1/message-tasks` returns the messages in configured order and marks the feed incomplete if a source cannot be read. It does not clear, write or acknowledge messages. Missing or unavailable messages are never treated as a complete empty feed. The source list starts empty.

## Dishwasher task

Set `dishwasher_pending_entity` to the Home Assistant unloading boolean and `dishwasher_finished_entity` to its finish-time helper. The optional shared `GET /v1/tasks` feed includes this task with existing display messages. While pending, it shows cooling for four hours after the finish time and then ready to unload. If the finish time is unavailable, it falls back to the pending helper's last-changed time and marks the feed incomplete. This does not control or acknowledge the dishwasher. Both settings are empty by default.

## Collection tasks

Configure `collection_sources` as a list of `{entity, label}` objects for waste-collection sensors, and optionally `collection_calendar` for today's fallback event. The feed groups the next dates, marks today and tomorrow, and moves a collection to collected-today after noon. It reads only configured entities and marks the shared feed incomplete when a source cannot be read. Settings default empty; no household-specific names or entity IDs are shipped. Collection items are preview data, not a control or confirmation that a collection happened.

## Free-electricity events

Set `free_power_event` to an event entity with an `events` attribute containing start/end timestamps. Upcoming entries are shown within 48 hours; consecutive intervals are combined, and events underway or starting within two hours carry an urgent flag. Invalid or unreadable events mark the feed incomplete. This reads the event data only and never books, cancels or changes a session. The setting defaults empty.

## Robot status tasks

`robots` optionally lists vacuum and mower entities with their names, last-completed sensors and optional last-active, error, last-error, battery and blade sensors. Mower weather, forecast, recent-rain and winter-storage entities are configured separately. The feed reports offline devices or missed runs using last activity as well as last completion; it considers the configured error, battery, blade and weather data for the reason. A configured winter-storage switch suppresses mower tasks. It never controls a robot or sends notifications. No devices are configured by default. When a required configured source cannot be read, the feed is marked incomplete.

## Home and display health tasks

Optional `health_*_entity` settings provide DNS, smoke-device connectivity, backup age, frost, system capacity and display heartbeat/battery data. Each source is read only; unset sources produce no task. Unreadable configured inputs mark the shared task feed incomplete. Urgent states are flagged for display, not sent as notifications. `health_display_name` supplies the label for heartbeat and charge tasks. No household-specific entity IDs are shipped.

## Battery and maintenance tasks

Optional battery tasks read Home Assistant states, entity/device/area registries and the configured forecast and ignored-device helpers; they never change the source entities or shopping list. Configure battery-model types, exceptional device overrides and exclusion tokens privately. Forecasts below 20 days, low fallback levels, repeated drop-outs and offline battery devices produce tasks; urgent thresholds are retained. A missing ignored-device helper suppresses battery output and marks the feed incomplete. Optional maintenance groups supply part sensors and thresholds; a configured winter-storage switch suppresses that group. Data stays in memory and registry snapshots are cached for one minute. Both features start disabled or empty.

## Household reminders

Optional entity settings provide free-hour booking failures, wheel results, oven-cleaning reminders and the first event tomorrow. Set `energy_provider_name` to the provider name used in booking and wheel labels. The oven reminder can use the existing `free_power_event` to suggest a time. Event titles are returned only through the authenticated task API. These sources are read-only: the app does not book an event, spin a wheel, operate an oven or change a calendar. Sources default empty and unavailable configured inputs mark the feed incomplete.
