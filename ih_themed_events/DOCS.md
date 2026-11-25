# Intelligent Home - Themed Events

## What it does

One record per themed event. A record holds: name, dates (every year on a day, or fixed dates), the lights (and an optional light group), the speakers, the sounds folder, the automations to pause while the event runs, the effects, a look (colour) and an auto-off time. Each event also has a checklist (each item is due a number of days before the start) and a review (what worked, what to change) for each run.

The page can create and edit event plans using form controls, including choosing devices and browsing local media. Effects can use editable light, sound and wait steps, and can restore the lighting afterwards. Saving a plan does not apply it. With live control enabled, the administrator page offers a review and installation of new events and updates to items it previously created. Existing live events, custom effects and conflicting Home Assistant items are not taken over. Dates are planning information; mode starts manually. Turn the mode off before updating an installed event. The **generator** also exposes standard Home Assistant pieces at `GET /events/<slug>/generate`:

| File | Content |
|---|---|
| `helpers.yaml` | `input_boolean.<slug>_mode`, `_music`, `_busy` (and `_random_effects`), `input_text.<slug>_lights_off`, `_speakers_playing`, `_paused_automations`, and the speaker pick when there are random effects |
| `automations.yaml` | `<slug>_mode_switch` (ON: snapshot the lights, remember the speakers, pause the listed automations, start the music switch and the look; OFF: stop scripts, restore the lights, pause speakers that were not playing, turn the paused automations back on), `<slug>_safety` (auto-off, restart, stuck effect), `<slug>_random_effects`, and one `<slug>_<effect>_at` per effect that has a fixed time |
| `scripts.yaml` | `<slug>_look` and `<slug>_effect_sound` |
| `README.txt` | what to do first and how to install and roll back |

Applying from the page creates or updates owned helpers, scripts and automations in Home Assistant. The page shows a review first; Home Assistant reloads scripts and automations as they are saved. The add-on journals changes and can resume an interrupted operation when it can verify the affected items. An outside change to an owned rule requires explicit acknowledgement before it is replaced; ambiguous ownership stops the operation. An older installation without stored rule contents also requires explicit acknowledgement on its first update. Effects without steps still require their original hand-written scripts and cannot be installed through this flow.

## Records added on first start

- **Halloween**: described only. It is live in Home Assistant by hand. Moving it onto the generator is a separate task.
- **New Year's Eve**: planned. Kitchen, dining room and snug speakers, a midnight burst at 00:00 (cheer on dining room or snug only), auto-off 02:00. The lights are still to be chosen; fill them in on the record. The helpers `nye_*` already exist in Home Assistant.

## Options

| Option | Meaning |
|---|---|
| `api_token` | Bearer token for the API on port 8819. Empty = the API refuses everything except `/healthz`. |
| `control` | `off` (default) or `live`. With `live` the page can switch `input_boolean.<slug>_mode` on and off. The API token can never do this. |

## Install flags (IH runbook)

Boot: auto. Watchdog: `/healthz` on port 8819. Turn on `auto_update` in the Supervisor after the first start. Add-on data: `/data/ih.db` with a daily snapshot in `/data/backup`. No host folders are mapped and no secrets are stored in the database.

## API

See the top of `app/main.py`. Unknown fields are refused. Event text is never logged.

## Rollback

Stop and uninstall the add-on. Nothing in Home Assistant depends on it.
