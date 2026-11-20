# Changelog

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
