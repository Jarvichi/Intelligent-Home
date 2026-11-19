# Kindle Display

Set `source_url` to the existing Kindle picture URL to see the current picture. Import a validated Wall source mapping to configure read-only Home Assistant sources for the new preview. To include shared tasks and alerts, configure the existing IH Displays API URL and token and enable the corresponding settings. The current image is retained with a stale warning if its source stops responding.

The new picture is an early read-only preview, with explicit unavailable labels. Import a mapping containing the Kindle battery, last-seen and page helpers to use the power-aware preview and page offset. IH Displays 0.11.0 or newer supplies task groups for pinned messages and collections; earlier versions show a grouping warning. The selected media and game sources can replace their forecast areas.

Choose a screen to compare the dinner menu, home and birthday pictures. Configure the Cooking URL and token to preview meals and an active cooking session. The ingress preview remains read-only.

The optional device interface serves pictures and their touch zones as one authenticated generation. Set `device_enabled` and a unique `device_token` of at least 32 characters, publish its port only to a trusted network, and configure the device client with that token. Use an encrypted network if the device supports it: bearer credentials sent over HTTP are not encrypted. The adapter rejects stale frames and rechecks action sources. Home Assistant helper updates are checked immediately before writing but cannot be atomic with writes from other automations.

The client is an integration component: the existing device listener, charging behaviour, cached popup handling and rollback must be verified on the actual device before switching its feed. Installing this add-on alone does not make that switch. Controls in the ingress preview do not perform actions.

The optional client `device/ih-device.sh` receives its URL, private curl configuration, state directory and drawing command through `IH_DEVICE_URL`, `IH_DEVICE_CURL_CONFIG`, `IH_DEVICE_DIR` and `IH_DEVICE_DRAW`. If configured, `IH_DEVICE_WIFI_IMAGE` points to an existing private image on the device; its contents are not sent to the add-on. A touch listener must pass actual screen coordinates to `tap-xy`. This helper does not install or replace the existing listener.

For kindle-dash v2, `device/integrate_legacy.py CURRENT COPY` generates a separate patched copy and rejects an unexpected or already patched script. Back up the current device script and settings first, then review the generated copy against the running version. The integration remains off unless `IH_DEVICE_ENABLED=true` is set in the device-local adapter settings. The existing power schedule, quiet hours, touch budget and watchdog remain in the original loop. Actual installation and device verification are separate from generating the patch.

While charging, the client can prefetch dinner, home and birthday pictures. Navigation shows a cached picture immediately, then loads the live screen. If the service is unavailable, the cached picture remains read-only and offers a local Back action. Cached pictures cannot show new alerts while offline.

Set `renderer` to `html` to try the HTML page for the main screen (default `svg`). It draws the normal screen at the same positions as the SVG picture so the tap zones still line up. The cooking screen, Halloween and event-day headers use the SVG picture, and so does any picture Chromium cannot make. Compare the two in the preview before switching the device to the new add-on.

Each `render_lights` group may carry an optional `floor` (for example `Ground`, `1st floor`, `2nd floor`, `Outside`). When every group has one, the Kindle lists the rooms that are on floor by floor, in two columns, like the original picture. Without floors it shows one flat list.

For the lights-off action, source mappings may provide `kindle_lights_off_floors` as Home Assistant floor IDs and `kindle_lights_off_exclude` as entity-ID substrings. The add-on resolves currently on lights from those floors when the picture is created and again when the button is pressed. A changed selection is rejected. Alternatively, `kindle_lights_off_entities` provides a fixed light list; floor mappings take precedence if supplied.
