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
