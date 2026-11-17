# IH Wall Display

Set `source_url` to an existing trusted PNG or JPEG image endpoint in the add-on configuration, then open the administrator-only ingress page. The page refreshes its preview every minute while open. If the source fails, the app retains the last valid image in memory and marks it stale; a restart clears that cache. Without a configured source or a valid cached picture, the page reports that no picture is available. Source addresses and picture content are never written to logs.

The source is a trusted administrator-supplied URL. The app follows no redirects, enforces a size limit and accepts PNG or JPEG image signatures. Do not point it at a URL containing credentials. It has no LAN-facing picture endpoint, and does not create a public copy of household information.

Optionally set `displays_url`, `displays_token` and `profile_id` to read a wall profile from IH Displays. The shared API must be enabled with its own token. Profile settings are shown for review and do not yet control the picture or refresh schedule. If the shared service is unavailable, picture preview continues independently. The token is never included in responses or logs.

This is a staged preview adapter: it does not render its own layout, configure a television, change the existing wallpaper URL, or implement touch controls. Preserve the existing TV layout, wallpaper provider, update schedule and access restrictions until a later verified cutover. An unavailable app does not interrupt the current display.

## Own picture prototype

Configure `render_entities` with up to six Home Assistant entity IDs and optionally `render_todo_entity` with a to-do list. The add-on reads their current states and open items through the Home Assistant API; it never writes them. The private side-by-side SVG preview uses the shared profile dimensions and reserves the bottom quarter and upper-right area for television overlays. The prototype shows unavailable data explicitly and uses a 60-second preview refresh. It is an initial layout, not full parity with the existing wall picture, and it is not a TV-compatible PNG feed or a cutover. No entity IDs or household data are shipped in the add-on.

## Structured task feed

Optionally set `render_task_feed_entity` to a Home Assistant entity with an `items` attribute containing an array of `{title, detail, priority}` objects. Titles are required; detail defaults to empty and priority to `normal` (`high`, `normal`, or `low`). The feed is read-only. It can be combined with `render_todo_entity`; high-priority feed items appear first, order is otherwise stable, and matching titles from different sources remain separate. If a configured source is unavailable, the picture says so; it does not claim an empty list. No existing computed task feed is migrated or configured by this change.

Set `render_shared_messages` to true to include the configured IH Displays message feed. When the feed or any configured source is unavailable, the preview warns that tasks may be incomplete. Message acknowledgment and clearing remain with the existing controls. This migrates message display only; the other household task rules still need their own producers.

The picture labels tasks as selected sources. An empty configured subset does not mean the household has no other tasks.
