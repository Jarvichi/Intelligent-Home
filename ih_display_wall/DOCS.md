# IH Wall Display

Set `source_url` to an existing trusted PNG or JPEG image endpoint in the add-on configuration, then open the administrator-only ingress page. The page refreshes its preview every minute while open. If the source fails, the app retains the last valid image in memory and marks it stale; a restart clears that cache. Without a configured source or a valid cached picture, the page reports that no picture is available. Source addresses and picture content are never written to logs.

The source is a trusted administrator-supplied URL. The app follows no redirects, enforces a size limit and accepts PNG or JPEG image signatures. Do not point it at a URL containing credentials. The optional protected image endpoints are disabled without a token, and their port is not published by default.

Optionally set `displays_url`, `displays_token` and `profile_id` to read a wall profile from IH Displays. The shared API must be enabled with its own token. The app-owned preview uses the profile dimensions; the existing source picture remains independently available if the shared service is unavailable. The token is never included in responses or logs.

This is a staged preview adapter: it renders an independent layout, but does not configure a television, change the existing wallpaper URL, or implement touch controls. Preserve the existing TV layout, wallpaper provider, update schedule and access restrictions until a later verified cutover. An unavailable app does not interrupt the current display.

## Own picture prototype

Configure `render_entities` with up to six Home Assistant entity IDs and optionally `render_todo_entity` with a to-do list. The add-on reads their current states and open items through the Home Assistant API; it never writes them. The private side-by-side PNG preview uses the shared profile dimensions and reserves the bottom quarter and upper-right area for television overlays. The preview shows unavailable data explicitly and refreshes every 60 seconds. Its optional protected PNG endpoint does not by itself establish feature parity or switch any display. No entity IDs or household data are shipped in the add-on.

## Structured task feed

Optionally set `render_task_feed_entity` to a Home Assistant entity with an `items` attribute containing an array of `{title, detail, priority}` objects. Titles are required; detail defaults to empty and priority to `normal` (`high`, `normal`, or `low`). The feed is read-only. It can be combined with `render_todo_entity`; high-priority feed items appear first, order is otherwise stable, and matching titles from different sources remain separate. If a configured source is unavailable, the picture says so; it does not claim an empty list. No existing computed task feed is migrated or configured by this change.

Set `render_shared_messages` to true to include the configured IH Displays shared task feed. It can include messages and the other configured household task producers. When the feed or any configured source is unavailable, the preview warns that tasks may be incomplete. Message acknowledgment and clearing remain with the existing controls.

The picture labels tasks as selected sources. An empty configured subset does not mean the household has no other tasks.

Shared tasks may carry an urgent flag, which highlights the task without changing its source order.

Set `render_shared_alerts` to true to preview IH Displays urgent alerts. The preview marks unconfigured or incomplete alert sources explicitly and shows up to four warnings per rotating page. Energy dismissal state is read from the existing source; the preview does not provide dismissal controls. The existing TV feed remains the live display and should not be switched until warning and action parity are verified.

## Hourly forecast preview

Optionally set `weather_forecast_entity` and `weather_forecast_fallback_entity` to existing Home Assistant sensor entities with hourly forecast attributes. The preview reads the primary source first, then the fallback if it is unavailable or has no current data. It identifies fallback and unavailable states. Both settings default to empty; this does not change existing display feeds.

The first hourly entry is labelled “Now” only while it covers the current time; otherwise it is labelled “Next”. Daily high and low values are shown when present. Missing daily data is labelled unavailable without hiding valid hourly data.

Optionally set `weather_current_entity` to an existing weather entity. When the hourly forecast has no current-hour entry, its current conditions are used; if they are unavailable, the first future entry remains labelled “Next”.

The optional `image_api_token` enables the bearer-protected `GET /v1/frame` on the private service port. Each request refreshes the configured existing image source. If refresh fails after a successful fetch, the last frame is returned with `X-IH-Frame-Stale: true`; without a frame the service returns 503. This is a compatibility feed of the existing image, not the new renderer or a cutover. It is disabled by default. Do not publish the port or credential without reviewing client access.

The same token enables `GET /v1/render.png`, a PNG of the app-owned preview. It uses the shared profile and configured selected sources; it is not yet full legacy parity. Failure to render returns 503. Keep this private endpoint disabled until a compatible authorised client is available.

For clients that cannot send an Authorization header, `image_url_access` optionally enables `/v1/render/<image_api_token>.png` for URL-safe tokens of at least 32 characters. URL credentials can be copied from client configuration, histories or intermediaries; only enable this after reviewing access on the display and its network. Query strings may be used for cache busting and do not affect authorisation. The setting defaults to false and does not change the existing TV feed.

A client with an existing hard-coded image address needs an independently planned provider update or compatible route before it can use the new endpoint. Keep its current feed until its network access, refresh behaviour and rollback have been verified on the real display.

Optional `render_media_entities` and `render_overlay_entities` read media player and binary sensor states. Active media includes a title and artist when supplied; active binary sources are labelled. Unavailable configured sources are indicated. These are read-only selected sources, not a replacement for all legacy media or intrusion rules.

`render_game_entities` accepts media players representing games consoles; a title is shown whenever the player is available and not off, even if its state is not `playing`.

Optional `render_cooking_entity` reads a cooking plan sensor and displays the active title, eating time and available oven/target information. This preview never starts or changes a cooking session.

Alternatively, set `cooking_url` and `cooking_token` to the Cooking add-on's private read-only API access. The app then reads its `/cook` status directly and uses that source in preference to the legacy sensor. If the legacy sensor is also configured, oven readings are shown only when its active title and eating time match the owner; otherwise the oven reading is marked unavailable. Failed or malformed owner responses are marked unavailable.

The optional `render_intrusion_alert`, `render_intrusion_log` and `render_intrusion_presence` sources preview the existing motion-while-away log when its alert is on and a configured household presence switch is on. All sources are read only; missing or unavailable inputs are marked unavailable. No notifications or alarm actions are sent.

The optional `render_heat_boiler`, `render_heat_thermostat` and `render_heat_floors` sources show boiler and floor heating, with thermostat temperatures where available. Configure the full set together; unavailable configured inputs are marked unavailable.

Optional `render_doors` entries pair a label with a lock and contact sensor; unavailable inputs are never presented as locked. Optional `render_energy` sources show current and next rates, daily costs and upcoming or active saving sessions, using configured existing sensors and events. Unavailable configured inputs are indicated.

Optional `render_alarm` and `render_presence` read an alarm panel and household presence switches. Unavailable inputs are indicated rather than treated as disarmed or away.

Optional `render_rooms` maps room labels to temperature sensors or climate entities. Rooms appear among the paged cards; missing readings are marked unavailable.

`render_sun_entity` supplies the next sunrise or sunset and helps `render_night_mode: auto` select the daylight or dark palette. Auto uses a dimmer palette from 23:00 to 06:00. Explicit `light`, `dark` and `deep` modes are also available; urgent warning contrast is retained.

Optional `render_birthdays_entity` and `render_halloween_entity` provide household-specific occasion data and seasonal mode. Birthday names remain in the existing private sensor; they are not shipped in the add-on. The preview can show selected public holidays and upcoming birthdays without changing any entity.

An administrator may import a JSON source mapping from the ingress page. Imported source fields are validated and stored in the add-on's private data directory, applied on the next preview without a restart, and override the corresponding configured source fields. Fields omitted from the import retain their existing values. This importer accepts source mappings only; it cannot change URLs, credentials, listener settings or action permissions.

The television layout places date and weather on the left and tasks on the right, reserving the television's upper-right overlay area. `render_background_path` and optional `picture` fields in `render_presence` can refer to existing Home Assistant `/local/` images; the app fetches them from the local Home Assistant server and embeds them in the private picture. If an image is unavailable, the display falls back to a dark background or an initial. The migration tool includes these paths when the legacy wall template supplies them; reimport the generated mapping after upgrading an older installation.

For installations using the legacy generated dashboard, `tools/import_legacy_sources.py` can prepare the mapping from the existing dashboard JSON, rule script and wall template. Supply those three paths with `--dashboard`, `--rule-script` and `--wall-template`, plus a new private output path with `--output`. The script validates the extracted mapping and refuses to overwrite an existing file. Review the file, then import it through admin ingress. It contains household entity IDs and must not be committed or published. Configure any Cooking API credential separately; credentials are never imported.

### Optional legacy screensaver proxy

The compatibility proxy is disabled by default. It is intended for an existing unauthenticated LAN image client whose URL cannot be changed. When enabled, the listener on container port 8839 forwards requests to `legacy_proxy_upstream`, an HTTP origin for the existing screensaver. It accepts the client's cache-busting query and forwards other paths and methods to that origin. With `legacy_proxy_wall: false`, `/7` also stays on the existing screensaver; with `legacy_proxy_wall: true`, `/7` serves the Wall renderer and falls back to the existing picture if rendering fails. The `X-IH-Image-Source` response header identifies `wall` or `legacy`.

The listener is unauthenticated, like the legacy client, and can expose household pictures and the upstream's other routes. Enable and publish it only on a trusted network after reviewing the upstream's access. Keep port 8839 unpublished during testing. Configure the upstream using the screensaver's private add-on hostname and container port rather than the public address being replaced: pointing it back at this proxy would create a loop. Before moving a host port, verify all existing Kindle and other clients, their paths, and the upstream route. A port cannot belong to both add-ons; moving it requires changing the screensaver's host port and restarting services. If the Wall add-on is stopped, the proxy cannot serve any forwarded route. To roll back, restore the original host-port mapping to the screensaver; keep the existing service and its configuration available until the rollback is proven. A switch in options requires restarting Wall.
