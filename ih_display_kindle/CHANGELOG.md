# Changelog

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
