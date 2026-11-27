# Changelog

## [0.15.13] - 2026-11-27
- Main screen side margins cut from 48 px to 30 px on both sides (content now 30 to 1042 px, was 48 to 1024), as asked on 20 Nov. Every horizontal position and width on the main screen is scaled to the wider area (text sizes unchanged) and the touch zones move with them. The other screens (menu, dinner, cooking, birthdays) are unchanged.

## [0.15.12] - 2026-11-27
- To-do row detail text ('plug it in', 'in 3 days') is now the same size as the row title, medium weight and darker (white on the black row), as asked on 20 Nov. The sunrise/sunset text on the main screen moves down 15 px so it sits on the same line as the date (estimated from a photo of the Kindle; not rendered here).

## [0.15.11] - 2026-11-27
- Move the page to the shadcn/ui look (shared ih_ui) and trim custom styles that fought it.

## [0.15.10] - 2026-11-27
- Add five themes with shared colours and visual details.

## [0.15.9] - 2026-11-27
- Apply the active Intelligent Home appearance across add-ons.

## 0.15.8
- Update the shared Intelligent Home visual theme and components.

## 0.15.7

- The Kindle menu looks calmer: white rows with a thin outline and a small icon on the left, instead of big black buttons (a tapped row still draws pressed).
- The Refresh tap area on the main screen is now about 120x100 px around the icon (was 39x40 px, easy to miss).

## 0.15.6

- When the alert feed is incomplete because a smoke or CO alarm is offline, the picture now says which one ("Offline: Smoke alarm") instead of the generic "Alert sources unavailable".

## 0.15.5

- Stops dead Chromium helper processes piling up in the add-on (the add-on ran out of threads again on 24 Nov). The add-on had no init process, so Python was PID 1 and never cleaned up the helpers Chromium leaves behind after each picture; every refresh left a few more until nothing could start. `init: true` makes the add-on start with a tiny init that cleans them up.

## 0.15.4

- Stops the add-on running out of threads (`can't start new thread`, 22 Nov) when several pictures are drawn at once. Every panel used to start its own pool of 8 to 12 threads inside the main drawing pool, so one busy draw could hold over 100 threads. All the per-entity lookups now share one pool capped at 16 threads.

## 0.15.3

- Game art shows again while a PlayStation game is playing. A PlayStation's `entity_picture` is a full https address (Sony's image server), which the add-on refuses to fetch, so the "now playing" card had no picture. It now tries `entity_picture_local` (the same picture through Home Assistant's own proxy) first, then `entity_picture`.

## 0.15.2

- Fixes the Kindle going "Offline" when the add-on ran out of threads (`can't start new thread`, 21 Nov). When a Chromium picture timed out only the main Chromium process was killed, so its helper processes piled up. Chromium now runs in its own process group and the whole group is killed on a timeout (and swept after every run).
- A request waits at most 20 seconds for the single Chromium slot, then uses the plain picture, instead of waiting without limit. Before, every blocked request held a thread, so a hung Chromium soon stopped the add-on answering at all.

## 0.15.1

- The COOK DINNER tile on the main screen is laid out like its neighbours: small bold heading at the top-left ("COOK DINNER") with "Pick a meal" beneath, instead of centred two-line text that sat low.

## 0.15.0

- Taps held on the Kindle can be sent together: `POST /device/v1/tap` accepts several zone ids joined by commas (at most 12) and applies them in one go. Row taps on the cooking screen (DONE, DID IT, ON IT, -5, +5, prep tick) go to the cooking add-on as one request, so ticking several things off in a row works. A Kindle opts in with `&batch=1` on the frame request; only then are those zones marked `batch` in the zone list, so an old Kindle script sees no change. `device/ih-device.sh` knows `batch`, sends `batch=1` and accepts a comma list.

## 0.14.2

- Only one Chromium picture is drawn at a time. Several quick taps on the Kindle ask for several pictures at once; running them in parallel on the Pi made some time out and fall back to the plain picture.

## 0.14.1

- A tapped button now loses its fill and keeps its outline (only the button shape is inverted), instead of showing a black rectangle with the white button inside. The page around rounded corners is left alone.

## 0.14.0

- After a meal is picked on the Kindle the main screen now shows "Getting your recipe…" (for up to 2½ minutes, until the cooking screen is ready) instead of "Cooking unavailable · controls paused". No taps are offered while it shows.

## 0.13.1

- A tap the add-on refuses is now logged with a short reason (for example `stale display`, `stale frame or zone`, `cooking unreachable`), and the cooking add-on's reason is passed through, so a Kindle "Tap not sent" can be traced. No household data is logged.

## 0.13.0

- New on the device listener: `GET /device/v1/pressed/<frame>/<zone>` returns the tapped button's area of the picture on screen, inverted (black and white swapped), as a PNG. The Kindle can draw it at once on a tap so the button looks pressed and keeps its label, instead of a plain black box.

## 0.12.0

- New picture on the device listener for scripts: `GET /device/v1/card/tyres` (same bearer token) with the four tyre pressures and targets in PSI (`fl fr rl rr tfl tfr trl trr`) returns a 600 x 760 PNG of the car from above, with tyres under 90% of target drawn black. It needs `renderer: html`. This replaces the Lovelace screenshot the MINI tyre warning used.

## 0.11.0

- Weather icons are now the Meteocons monochrome set (Bas Milius, MIT; licence in `app/weather_svg/`): black line art that reads better on e-ink than the 0.10.0 set, drawn larger. Day and night versions of sunny, partly cloudy and rainy; the other conditions look the same day and night.

## 0.10.0

- Weather icons are now the Makin-Things static set (MIT licence, `app/weather_svg/LICENSE`) in place of the hand-drawn vector icons. The coloured icons are turned into dark greys when loaded so they read on e-ink. After dark there are night versions of sunny (moon), partly cloudy, rainy, pouring, snowy and fog: the current icon follows the sun entity, and each hourly icon is night when its UV index is 0. Same conditions as before (sunny, clear-night, cloudy, partly cloudy, rainy, pouring, snowy, snowy-rainy, lightning, lightning-rainy, fog, hail, windy, exceptional).

## 0.9.0

- The HTML screens are now templates: each screen has a Jinja2 template (`app/templates/*.html`) that says what is on it, and one stylesheet (`kindle.css`) that holds every position. Python only prepares the values. Positions did not change (checked against the previous version for the same data).
- You can change the layout without a release: put your own `main.html`, `dinner.html`, `home.html`, `birthdays.html`, `menu.html`, `cooking.html`, `base.html` or `kindle.css` in the add-on's own config folder under `templates/`. Your files are used before the built-in ones. A broken template falls back to the SVG picture, and the log says why. A fresh copy of the built-in files is written to `templates/default/` each time the add-on starts, to copy from.
- The add-on now maps its own private config folder (`addon_config`).
- The image installs Jinja2. The image build renders every template as a check.

## 0.8.0

- With `renderer: html` the dinner menu, who's-home, birthdays, Kindle menu and cooking screens are now HTML pages too, at the same positions as the SVG pictures so the touch zones still line up. Any screen Chromium cannot make falls back to the SVG picture.

## 0.7.0

- New option `renderer` (`svg` default, or `html`). With `html` the main Kindle screen is built as an HTML and CSS page and rendered by headless Chromium to a greyscale picture. The browser does the text layout, fonts and emoji. The cooking screen, Halloween and event-day headers still use the SVG picture automatically, and so does any picture Chromium fails to make.
- The image now includes Chromium, Noto Color Emoji and Pillow (about 400 MB larger). Chromium only runs while a picture is being made.

## 0.6.0

- Draw every Home Assistant weather condition as a clear greyscale vector icon (no font needed). Fog, lightning, hail, wind and warnings were blank or wrong before; clear nights show a moon.
- Use the original Kindle wording: condition names (Showers, Heavy rain, Thunder, Snow, Clear ...), `2–8° 1 mm rain` forecast text, `⚡ Plugged in` / `🔋 Kindle 87%`, `Locked` / `Idle` with `all locked`, `22:00 tmrw`, `Dry 3 days`, `in 5 days`.
- Split the energy row like the original: price and next rate on the left (`⚡ 31.9p now (peak) · 13.3p from 00:30`), `Today ⚡ £7.68 🔥 £5.92` on the right; saving sessions read `⚡ Saving session ...`.
- Optional `floor` on each `render_lights` group lists the lights floor by floor (Ground, 1st floor, 2nd floor, Outside). Without it the flat list is kept.
- Put an envelope in front of message rows.
- Not yet ported: the ✕ buttons that clear a message or status row (zones `clear_0` to `clear_4`).

## 0.5.1

- Restore the original Roboto typeface and weather and seasonal symbols.
- Fit long status, task, cooking and recipe text into the available space.

## 0.5.0

- Restore seasonal headers, event countdowns, cat visitors and weather scenes.
- Match warning, wind and door visuals more closely and restore the Halloween party control when available.

## 0.4.1

- Render symbol and emoji glyphs on the device and keep the first dinner-time label inside its button.

## 0.4.0

- Preview Cooking, dinner, home and birthday screens using owner-provided data.
- Add an opt-in authenticated picture and touch interface with source and generation checks.

## 0.3.0

- Match pinned task paging, power-aware preview controls and adaptive task layouts.
- Show selected music and game activity in the existing weather areas.

## 0.2.2

- Keep smoke and CO warnings visible when multiple alerts are active.

## 0.2.1

- Bring preview weather, presence and task details closer to the existing picture.

## 0.2.0

- Add a read-only portrait preview with imported display sources.

## 0.1.0

- Add an administrator-only preview of an existing Kindle picture.
