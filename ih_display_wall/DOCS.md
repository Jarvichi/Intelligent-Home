# IH Wall Display

Set `source_url` to an existing trusted PNG or JPEG image endpoint in the add-on configuration, then open the administrator-only ingress page. The page refreshes its preview every minute while open. If the source fails, the app retains the last valid image in memory and marks it stale; a restart clears that cache. Without a configured source or a valid cached picture, the page reports that no picture is available. Source addresses and picture content are never written to logs.

The source is a trusted administrator-supplied URL. The app follows no redirects, enforces a size limit and accepts PNG or JPEG image signatures. Do not point it at a URL containing credentials. It has no LAN-facing picture endpoint, and does not create a public copy of household information.

Optionally set `displays_url`, `displays_token` and `profile_id` to read a wall profile from IH Displays. The shared API must be enabled with its own token. Profile settings are shown for review and do not yet control the picture or refresh schedule. If the shared service is unavailable, picture preview continues independently. The token is never included in responses or logs.

This is a staged preview adapter: it does not render its own layout, configure a television, change the existing wallpaper URL, or implement touch controls. Preserve the existing TV layout, wallpaper provider, update schedule and access restrictions until a later verified cutover. An unavailable app does not interrupt the current display.
