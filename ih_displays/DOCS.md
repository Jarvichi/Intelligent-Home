# IH Displays

Profiles describe display capabilities: type, dimensions, colour, touch, power, transport and refresh interval. The private database lives in the add-on's `/data` and is backed up with the app. It starts empty; no household devices are preconfigured. Values are settings, not evidence that a device is connected or working.

Open the administrator-only ingress page to add, edit or remove a profile. Changes use revisions to reject stale edits. The private API offers `GET /v1/profiles` and `GET /v1/profiles/<id>` with `Authorization: Bearer <api_token>`; it is disabled if the token is empty and has no write routes. Do not publish the API port to the LAN. `/healthz` reports service health only.

This foundation does not connect to a Kindle, TV or ESP32, render images, change touch actions, or replace the existing displays. The display adapters, device discovery, access model and compatibility handover require future work. Existing displays continue independently if this add-on is unavailable. No secrets, device addresses, tokens or household records belong in profile fields or the public repository.
