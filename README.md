# Intelligent Home add-ons

Intelligent Home (IH) is a set of small Home Assistant add-ons. Each one does one job, starts in a safe mode where it changes nothing, and keeps working if the others are off. One parent add-on, **Intelligent Home**, gives you a sidebar panel to install and watch the rest, and a settings hub that fills shared keys in every add-on from one page.

[![Add this repository to your Home Assistant.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fexample.invalid%2FOWNER%2FREPO)

## Requirements

- Home Assistant OS or Home Assistant Supervised (add-ons need the Supervisor).
- A 64-bit board or PC (`aarch64` or `amd64`).
- An MQTT broker add-on (for example Mosquitto) if you use the Discord modules or the scheduler. The table below shows which add-ons need it.
- Your own keys for any outside service you switch on (Discord bot, AI providers). The add-ons never ship with keys.

## Quick start

1. Select the "Add repository" badge above. Or open Settings, Add-ons, Add-on store, the three-dot menu, Repositories, and paste `https://example.invalid/OWNER/REPO`.
2. Install the add-on **Intelligent Home**.
3. Open its Configuration tab.
4. Set `ingest_token` to a long random text.
5. Set `mqtt_password` if you use an MQTT broker.
6. Turn on `provision` to switch on the settings hub.
7. Fill the shared settings in the `shared` list. Add one name and value per line. A name without a dot (for example `register_key`) applies to every add-on. A name with a dot (`ih_ai_layer_groq.groq_key`) applies to one add-on.
8. Select Save, then start the add-on.
9. Open **Intelligent Home** in the sidebar.
10. Open the Install tab and install the modules you want.
11. Wait for the settings hub to fill the empty options of the new add-ons. This runs every 10 minutes and once after each install.
12. Start each new add-on. They start in dry-run or shadow mode and post nothing.
13. Read the add-on's DOCS page, then turn dry-run or shadow off when you are ready to go live.

Nothing is copied by hand between add-ons.

## Add-ons

### Orchestrator

| Add-on | What it does | Needs | Docs |
|---|---|---|---|
| **Intelligent Home** (`ih_orchestrator`) | Status page, signed scheduler, settings hub and the sidebar manager that installs the other add-ons. | nothing else | [DOCS](ih_orchestrator/DOCS.md) |

### Discord

| Add-on | What it does | Needs | Docs |
|---|---|---|---|
| **Intelligent Home - Discord** (`ih_discord`) | Discord transport: one bot token, one poller per channel, signed MQTT bus for feature modules (ih_discord_house, ih_discord_shopping). | `mqtt` | [DOCS](ih_discord/DOCS.md) |
| **Intelligent Home - Discord - AI Bot** (`ih_discord_ai_bot`) | Discord AI bot: answers messages from allow-listed users on chosen channel keys (bus module of ih_discord) by asking the AI layer API and posting the reply. | `ih_discord`, `ih_ai_layer`, `mqtt` | [DOCS](ih_discord_ai_bot/DOCS.md) |
| **Intelligent Home - Discord - House** (`ih_discord_house`) | House status commands for Discord as a bus module of ih_discord (who is home, alarm, bins, heating, one device). | `ih_discord`, `mqtt` | [DOCS](ih_discord_house/DOCS.md) |
| **Intelligent Home - Discord - Shopping** (`ih_discord_shopping`) | Shopping list commands for Discord as a bus module of ih_discord (add items, shop checklist by aisle with tick reactions, got ...). | `ih_discord`, `mqtt` | [DOCS](ih_discord_shopping/DOCS.md) |
| **Intelligent Home - Discord - Task channels** (`ih_discord_taskchannels`) | Task channels for Discord as a bus module of ih_discord (one private channel per task, Work in Progress and Done categories, goal as topic, daily cleanup of old done channels). | `ih_discord`, `mqtt` | [DOCS](ih_discord_taskchannels/DOCS.md) |
| **Intelligent Home - Orchestration - Inbox** (`ih_orchestration_inbox`) | Orchestration inbox: durable queue of messages on chosen Discord channel keys (bus module of ih_discord), eye-emoji acknowledgement, token-protected read API. Never answers in the channel. | `ih_discord`, `mqtt` | [DOCS](ih_orchestration_inbox/DOCS.md) |

### Jobs

| Add-on | What it does | Needs | Docs |
|---|---|---|---|
| **Intelligent Home - Jobs** (`ih_jobs`) | Parent of the Jobs family; lists and triggers the child job add-ons. | `mqtt` | [DOCS](ih_jobs/DOCS.md) |
| **Intelligent Home - Jobs - Backup** (`ih_jobs_backup`) | Child of Intelligent Home - Jobs: backup storage sensors. | `ih_jobs` | [DOCS](ih_jobs_backup/DOCS.md) |
| **Intelligent Home - Jobs - Met Office** (`ih_jobs_metoffice`) | Child of Intelligent Home - Jobs: Met Office forecast sensors. | `ih_jobs` | [DOCS](ih_jobs_metoffice/DOCS.md) |
| **Intelligent Home - Jobs - AI Trust Test** (`ih_jobs_trust`) | Child of Intelligent Home - Jobs: monthly trust test of the outside AIs via the AI layer, adds a review to-do. | `ih_jobs`, `ih_ai_layer` | [DOCS](ih_jobs_trust/DOCS.md) |

### AI layer

| Add-on | What it does | Needs | Docs |
|---|---|---|---|
| **Intelligent Home - AI Layer** (`ih_ai_layer`) | Intelligent Home - AI Layer: routes AI requests to AI providers (built in) and agent add-ons. | nothing else | [DOCS](ih_ai_layer/DOCS.md) |
| **Intelligent Home - AI Layer - Claude** (`ih_ai_layer_claude`) | Agent of Intelligent Home - AI Layer: general questions and summaries via Anthropic Claude (text scrubbed first). | `ih_ai_layer` | [DOCS](ih_ai_layer_claude/DOCS.md) |
| **Intelligent Home - AI Layer - Claude CLI Agent** (`ih_ai_layer_claude_cli`) | Agent of Intelligent Home - AI Layer: general questions and summaries via the Claude Code CLI (text scrubbed first). | `ih_ai_layer` | [DOCS](ih_ai_layer_claude_cli/DOCS.md) |
| **Intelligent Home - AI Layer - Gemini** (`ih_ai_layer_gemini`) | Agent of Intelligent Home - AI Layer: general questions and summaries via Google Gemini (text scrubbed first). | `ih_ai_layer` | [DOCS](ih_ai_layer_gemini/DOCS.md) |
| **Intelligent Home - AI Layer - Grok** (`ih_ai_layer_grok`) | Agent of Intelligent Home - AI Layer: general questions and summaries via xAI Grok (text scrubbed first). | `ih_ai_layer` | [DOCS](ih_ai_layer_grok/DOCS.md) |
| **Intelligent Home - AI Layer - Groq** (`ih_ai_layer_groq`) | Agent of Intelligent Home - AI Layer: general questions and summaries via Groq (text scrubbed first). | `ih_ai_layer` | [DOCS](ih_ai_layer_groq/DOCS.md) |
| **Intelligent Home - AI Layer - Mistral** (`ih_ai_layer_mistral`) | Agent of Intelligent Home - AI Layer: general questions and summaries via Mistral (text scrubbed first). | `ih_ai_layer` | [DOCS](ih_ai_layer_mistral/DOCS.md) |
| **Intelligent Home - AI Layer - OpenAI** (`ih_ai_layer_openai`) | Agent of Intelligent Home - AI Layer: general questions and summaries via OpenAI (text scrubbed first). | `ih_ai_layer` | [DOCS](ih_ai_layer_openai/DOCS.md) |
| **Intelligent Home - AI Layer - OpenRouter** (`ih_ai_layer_openrouter`) | Agent of Intelligent Home - AI Layer: general questions and summaries via OpenRouter (text scrubbed first). | `ih_ai_layer` | [DOCS](ih_ai_layer_openrouter/DOCS.md) |
| **Intelligent Home - AI Layer - Pollinations** (`ih_ai_layer_pollinations`) | Agent of Intelligent Home - AI Layer: general questions and summaries via Pollinations (text scrubbed first). | `ih_ai_layer` | [DOCS](ih_ai_layer_pollinations/DOCS.md) |
| **Intelligent Home - AI Layer - SerpApi Agent** (`ih_ai_layer_serpapi`) | Agent of Intelligent Home - AI Layer: showtimes and web search via SerpApi. | `ih_ai_layer` | [DOCS](ih_ai_layer_serpapi/DOCS.md) |

### Games

| Add-on | What it does | Needs | Docs |
|---|---|---|---|
| **Intelligent Home Games** (`ih_games`) | Turns game activity (PlayStation trophies, Xbox gamerscore, Steam, Destiny 2) into friendly chat: decides what is worth saying and queues messages for the chat bots. | `ih_discord`, `mqtt` | [DOCS](ih_games/DOCS.md) |

### Cooking

| Add-on | What it does | Needs | Docs |
|---|---|---|---|
| **Intelligent Home - Cooking** (`ih_cooking`) | Data owner for recipes and the meal plan, plus the meal-vs-calendar check (read-only in v0.1). | nothing else | [DOCS](ih_cooking/DOCS.md) |

### Brain

| Add-on | What it does | Needs | Docs |
|---|---|---|---|
| **Intelligent Home - Brain** (`ih_brain`) | Serves the House Brain markdown notes to Claude as an MCP server over HTTP, outside Home Assistant core (read-only by default). | nothing else | [DOCS](ih_brain/DOCS.md) |

"Needs" lists the add-ons and services a module depends on. `mqtt` is an MQTT broker you run yourself (for example the Mosquitto add-on).

## Build on first install

No `image:` is set in any `config.yaml`. Home Assistant builds each add-on on your own device from its `Dockerfile`. The first install and each update can take several minutes on a small board.

## Troubleshooting

- **The repository will not add.** Check that the URL is exactly `https://example.invalid/OWNER/REPO` and that Home Assistant can reach GitHub.
- **An add-on does not appear in the store.** Reload the store (three-dot menu, Check for updates) and try again.
- **An install fails or stalls.** Open the add-on's Log tab. A build on a small board is slow; wait for it to finish before you retry.
- **The Intelligent Home panel is empty or an add-on shows as missing a setting.** Open the Settings tab of the panel. It lists the option names that are still empty (never the values).
- **The settings hub changes nothing.** Check that `provision` is on and `ingest_token` is set. Restart the Intelligent Home add-on. The hub fills only empty options unless `provision_overwrite` is on.
- **A module shows as down.** Check that the broker add-on is running and that the module's `mqtt_*` options and `signing_key` are filled.
- **A module posts nothing.** This is the safe default. Turn off its `shadow` or `dry_run` option when you are ready.

## Privacy design

- No secret and no house detail is stored in this repository.
- Tokens, entity names, channel names, people and other house values are **options** of each add-on. You set them in the add-on's Configuration tab. Home Assistant stores them on your device only.
- Each add-on ships an `options.example.json` with empty or generic values.
- Every export is scanned for private addresses, identifiers, e-mail addresses, phone numbers and secret-shaped strings before it is committed.

## Updates

Keep **auto-update OFF**. Read the change list, then update by hand. Back up first. New versions start in dry-run mode where an add-on can act on the outside world.

## Licence

MIT. See `LICENSE`.
