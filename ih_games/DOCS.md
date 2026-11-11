# Intelligent Home Games

## What it does

Turns game activity (PlayStation trophies, Xbox gamerscore, Steam, Destiny 2) into friendly chat: decides what is worth saying and queues messages for the chat bots. Dry-run by default; limits per player per day, quiet at night. Stand-alone family member.

## Requires

- `ih_discord` add-on
- an MQTT broker (the Mosquitto add-on works)
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home Games**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `ingest_token`, `outbox_token`, `bungie_api_key` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in dry-run mode, so it changes nothing yet.
5. When the log looks right, set `mode` to `live`.
6. Open the add-on's **Open Web UI** page for the single Games dashboard. It includes each player's library, complete game details, and the Games research queue. The old `/house-games/<player>` tabs now open the matching player view in this add-on. The UI uses the existing `admin_token` on the server; it is not sent to the browser.
7. To add cover art, use **Find official box art** on a game's card. This asks the IH AI Layer's scoped image-search capability, then keeps the image under this add-on's `/data/artwork` store and serves it locally. Search uses the configured AI Layer provider quota. Only results linked to recognized official store or publisher pages are accepted. If the search finds nothing, a dialog offers to upload your own image instead (JPEG, PNG or WebP, up to 6 MB). Refresh on an existing cover looks for a portrait image and offers the upload if there is none.

### PlayStation trophy names

Follow [PSN_TROPHY_SETUP.md](PSN_TROPHY_SETUP.md) to configure the PSN Online ID and NPSSO password option. Never send the token in chat or put it in source control. Live API lookups require your own token.

The same token and Online ID let the Games page list every trophy of a game: open a game card and tap a trophy tile (Platinum, Gold, Silver or Bronze) to see that tier's trophies marked "Earned" or "Not earned". The list is read from PSN when you tap, kept in `/data/trophy_lists/` and reused for 15 minutes. Without the token, or when PSN cannot be reached and no list was saved before, the page shows only the earned trophies whose names this add-on recorded, and says that the list is incomplete.

### PlayStation play time

PSN reports no play time for most games. When `psn_trophies` is enabled, the add-on also reads `sensor.<trophy_prefix>_now_playing` on every provider run and sums the play sessions it sees (a session ends after 10 minutes without a title; sessions under 5 minutes are dropped). Totals are kept in `/data/psn_playtime_<player>.json` and count only from the day tracking began; the card says "Play time since <date>". To fill in earlier sessions from the Home Assistant recorder, run `app/tools/psn_backfill.py` once (read-only unless `--write` is given).

### The stat box on a game card

Next to "Last played" a card shows one box: quest progress when the game's tool reports it, else trophies earned out of the total, else a play time above zero. With none of these the box is hidden.

### Steam achievement names

Follow [STEAM_ACHIEVEMENT_SETUP.md](STEAM_ACHIEVEMENT_SETUP.md) to configure the Steam profile URL you supplied and the private Steam API key option. The provider reads the current game and App ID from Home Assistant's Steam Online integration. Steam's Web API returns earned achievements and the game's published schema; profile game details must be public for the lookup to work.

The same configuration also syncs the account's owned games into the selected player's Games library, including playtime and last-played time where Steam provides them. It refreshes at most once per day.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `mode` | No | `dry` logs what it would do and changes nothing; `live` acts. Default: `dry`. |
| `players` | No | list |
| `providers_enabled` | No | list |
| `quiet_start` | No | str. Default: `22:00`. |
| `quiet_end` | No | str. Default: `08:00`. |
| `timezone` | No | str. Default: `Europe/London`. |
| `ingest_url` | No | Status page address (orchestrator). Default: `http://172.30.32.1:8790`. |
| `ingest_token` | Yes | Token for the status page (orchestrator). Same value there. |
| `mqtt_host` | No | MQTT broker host (default: the Mosquitto add-on). |
| `mqtt_port` | No | MQTT broker port. Default: `1883`. |
| `mqtt_user` | No | MQTT user name for this add-on. Default: `ih_games`. |
| `mqtt_password` | No | MQTT password for this add-on. |
| `enable_scheduler` | No | Turn on the signed MQTT scheduler. Default: `false`. |
| `signing_key` | No | Key used to sign messages. Must match this module's entry in the orchestrator's `module_keys`. |
| `outbox_token` | Yes | Token the chat side uses to fetch and acknowledge queued messages. |
| `assistant_url` | No | str, password |
| `assistant_token` | No | str, password |
| `bungie_api_key` | Yes | Your own Bungie API key (only for the Destiny provider). |
| `psn_npsso` | No | Optional PSN session token for trophy-name lookups. Configure privately in add-on options. |
| `destiny_poll_s` | No | How often the Destiny provider polls, in seconds. Default: `900`. |
| `admin_token` | No | Setting `admin_token`. |
| `ai_layer_url` | No | IH AI Layer request endpoint. Default: `http://172.30.32.1:8801`. |
| `ai_layer_art_token` | No | Scoped token accepted only for Games box-art search. It is configured by the owner and never shown in the UI. |
| `arc_data_dir` | No | Setting `arc_data_dir`. |
| `destiny_reset_utc_hour` | No | Setting `destiny_reset_utc_hour`. Default: `17`. |
| `todo_entity` | No | Setting `todo_entity`. Default: `todo.claude_to_do`. |
| `todo_sync` | No | Setting `todo_sync`. Default: `true`. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `mode` is `dry`. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Silent on the bus.** Check the `mqtt_*` options and that `signing_key` matches this module's entry in the orchestrator's `module_keys` (or let the settings hub fill it).
- **No game messages.** Check `providers_enabled`, that each player has the matching sensor prefix, and that it is not within `quiet_start` to `quiet_end`.
- **Cover art search says no provider is available.** In IH AI Layer, enable and configure a provider that supports `game_art_search` (the built-in SerpApi provider does), and set `shadow` to `false`. Configure the scoped `games_art_token` in IH AI Layer and the matching `ai_layer_art_token` in IH Games. The Games UI reports when the layer has no eligible provider or is still in shadow mode.

## Privacy

Game data is read from Home Assistant sensors. If the Destiny provider is on, public membership ids are sent to the Bungie API with your key. Chat messages go out through the chat bots you connect.

## More details

### Going Live
Set `mode: "live"` in options. By default, `ih_games` runs in `dry` mode (logs actions without emitting external events). Creating `/data/STOP_LIVE` acts as an instant kill-switch forcing dry mode.

### Library API (bearer `admin_token`)
- `GET /library?player=<key>`: the player's games. Each game has `card_stat`: `{"kind": "quests", "done", "total", "label"}`, `{"kind": "trophies", "earned", "total"}`, `{"kind": "playtime", "minutes", "tracked", "since"}` or `null`.
- `GET /games/<id>/details?player=<key>`: tool details plus `trophies` (totals and latest named trophies).
- `GET /games/<id>/trophies?player=<key>`: every trophy of the game: `trophies` = `[{id, group, name, detail, tier, earned, earned_at, hidden}]` (earned first), `complete` (false = only locally recorded earned names), `source` (`psn` or `history`), `fetched`, `stale`, `missing` (`not_configured`, `lookup_failed`, `not_found` or null) and `note` (text shown to the user). The Web UI proxies it as `api/games/<id>/trophies`.

### Game tool progress
A tool in `app/tools/` may define `progress(tc, player)` returning `{"done": int, "total": int, "label": "Quests"}`. The game card then shows "done / total" in its stat box.

### Cyberpunk 2077 quest tracker
PlayStation gives no quest data, so quests are ticked by hand. Open Cyberpunk 2077 on the Games page: each group (Base game, Phantom Liberty, Patch quests) lists its open quests with a tick box, and "Done quests" lists the ticked ones (untick to undo). Nothing is ticked at the start. The card's stat box shows ticked quests out of all quests. Commands for the assistant: `done <quest name or id>`, `undone <quest name or id>`, `status`, `left [base|phantom_liberty|patch]`. Ticks are kept per player in `/data/cyberpunk/state_<player>.json`.

The quest list is `app/tools/cyberpunk_quests.json`: 281 quests, fetched on 2026-11-11.

| Group | Main jobs | Side jobs | Gigs | Total |
|---|---|---|---|---|
| Base game | 42 | 112 | 72 | 226 |
| Phantom Liberty | 22 | 20 | 9 | 51 |
| Patch quests | 0 | 4 | 0 | 4 |

- Source of names, ids and groups: the Cyberpunk Wiki on Fandom (list pages "Cyberpunk 2077 Main Jobs", "Side Jobs", "Minor Jobs", the category "Cyberpunk 2077 Gigs", and the pages "Update 2.5" and "Patch 1.6"). The ids are the wiki's BaseID column. "Side jobs" here holds the wiki's Side Jobs and Minor Jobs.
- Second source: the Fextralife Cyberpunk 2077 wiki. Its gig list has the same 72 base-game gigs and the same 9 Phantom Liberty gigs by name. Its main-job and side-job pages are shorter than the Fandom lists: they name 40 of the 42 base main jobs (one more, "Tapeworm", is on its side-job page), 12 of the 22 Phantom Liberty main jobs, 70 of the 112 base side jobs and 10 of the 20 Phantom Liberty side jobs. The other names rest on the Fandom wiki alone (its list tables and its categories agree with each other).
- Patch quests: "Over the Edge" (Patch 1.6, the Edgerunners Update) and the three quests of Update 2.5 (the Edgerunners 2 Update): "Clampdown", "Welcome to Paradise" and "You Tell Me". The publisher's Update 2.5 news post confirms the first two by name and the third as an unnamed extension of "Fool on the Hill"; the name "You Tell Me" is from the Fandom wiki only. The three gigs that Patch 1.6 added are listed with the base-game gigs.
- Not every quest can be done in one playthrough: the three lifepath openings exclude each other, and so do the endings and some Phantom Liberty paths. A total of 281 ticked is therefore not reachable in one save.
- Not in the list: NCPD scanner hustles, cyberpsycho sightings as separate entries (the job "Psycho Killer" covers them), and five apartment quests ("Welcome Home" x4, "Houses In Motion: Makeovers") that the Fandom category has but its list pages do not.

### How to add a Provider in 5 steps
1. Create `app/providers/<name>.py`.
2. Define `NAME = "<name>"` at module level.
3. Implement `run(ctx, player)` accepting context and a player dict.
4. Compare state using files in `/data/state_<name>_<player>.json` and append new events.
5. Atomically write feed to `/data/feeds/<name>_<player>.json` keeping the last 50 events. Register the provider in `app/main.py`.
