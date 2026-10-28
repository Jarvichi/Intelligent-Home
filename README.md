# Intelligent Home add-ons

Small, generic Home Assistant add-ons. Each one does one job and keeps working if the others are off.

| Add-on | What it does |
|---|---|
| `ih_orchestrator` | Health and status page for the other modules, plus an optional signed job scheduler that talks to an MQTT broker you run yourself. |
| `ih_discord` | Discord commands as a module: shopping list and house status. Dry-run by default. |
| `ih_jobs` | Parent of the Jobs family: scheduled housekeeping jobs (backup store, weather fetch), plus a list of the child job add-ons that register with it. Dry-run by default. |
| `ih_jobs_backup` | Child of `ih_jobs`: backup storage sensors as its own add-on. Registers with the parent. Dry-run by default. |
| `ih_jobs_metoffice` | Child of `ih_jobs`: Met Office forecast sensors as its own add-on. Registers with the parent. Dry-run by default. |
| `ih_jobs_trust` | Child of `ih_jobs`: monthly test of the outside AIs through the AI layer (fixed questions, scored), then a review to-do. Registers with the parent. Dry-run by default. |
| `ih_games` | Turns game activity (PlayStation trophies first) into friendly chat events. Plug-in providers, one orchestrator that rations messages. Dry-run by default. |
| `ih_discord_house` | House status commands (who is home, alarm, bins, heating) as a bus module of `ih_discord`. Shadow mode by default: it posts nothing. |
| `ih_discord_shopping` | Shopping list commands (add items, `shop` checklist by aisle with tick reactions, `got ...`) as a bus module of `ih_discord`. Shadow mode by default: it posts nothing. |
| `ih_discord_taskchannels` | One private channel per task (`new`, `done`, `reopen`, `list`), Work in Progress and Done categories, daily cleanup of old done channels, as a bus module of `ih_discord` that asks the transport to administer channels. Shadow mode by default: it creates nothing. |
| `ih_discord_ai_bot` | Answers messages from allow-listed users on chosen channel keys by asking the AI layer API and posting the reply, as a bus module of `ih_discord`. Shadow mode by default: it posts nothing. |
| `ih_orchestration_inbox` | Durable inbox for messages on chosen channel keys, as a bus module of `ih_discord`: stores each message in a local queue, reacts with an eye emoji so the sender knows it arrived, never replies, and serves the queue over a token-protected local API. Shadow mode by default: queue written, no reactions. |
| `ih_ai_layer` | Routes an AI request (text plus optional capability) to a registered AI agent add-on and returns the answer; remembers which agent answers best. Not connected to Discord or any chat. Shadow mode by default. |
| `ih_ai_layer_serpapi` | Agent of `ih_ai_layer`: cinema showtimes and web search through SerpApi, with a small daily cap and a cache. Needs your own SerpApi key. |
| `ih_ai_layer_gemini` | Agent of `ih_ai_layer`: general questions and summaries through Google Gemini, with a daily cap. Text is scrubbed of personal data before it is sent. Needs your own Gemini key. |
| `ih_cooking` | Data owner for recipes and the meal plan, plus the weekly meal-vs-calendar check (who is not home for each planned meal). Read-only in v0.1. Needs a Home Assistant token for calendars. |
| `ih_ai_layer_groq` | Agent of `ih_ai_layer`: general questions and summaries through Groq, with a daily cap. Text is scrubbed of personal data before it is sent. Needs your own Groq key. |
| `ih_ai_layer_openrouter` | Agent of `ih_ai_layer`: general questions and summaries through OpenRouter, with a daily cap. Text is scrubbed of personal data before it is sent. Needs your own OpenRouter key. |
| `ih_ai_layer_claude` | Agent of `ih_ai_layer`: general questions and summaries through Anthropic Claude, with a daily cap. Text is scrubbed of personal data before it is sent. Needs your own Anthropic Claude key. |
| `ih_ai_layer_grok` | Agent of `ih_ai_layer`: general questions and summaries through xAI Grok, with a daily cap. Text is scrubbed of personal data before it is sent. Needs your own xAI Grok key. |
| `ih_ai_layer_openai` | Agent of `ih_ai_layer`: general questions and summaries through OpenAI, with a daily cap and a spend cap. Text is scrubbed of personal data before it is sent. Needs your own OpenAI key. |
| `ih_ai_layer_mistral` | Agent of `ih_ai_layer`: general questions and summaries through a Mistral agent, with a daily cap. Text is scrubbed of personal data before it is sent. Needs your own Mistral key and agent id. Easy generic jobs only. |
| `ih_ai_layer_pollinations` | Agent of `ih_ai_layer`: general questions and summaries through Pollinations, with a daily cap. Text is scrubbed of personal data before it is sent. No key needed. Prompts are public: generic jobs only. |

## Add the repository in Home Assistant

1. Open Settings, then Add-ons (Apps), then the store.
2. Open the three-dot menu, then Repositories.
3. Paste the URL of this repository and select Add.
4. Install the add-on you want, set its options, then start it.

## Build on first install

No `image:` is set in any `config.yaml`. Home Assistant builds each add-on locally from its `Dockerfile`.
The first install and each update build on your own device. On a small board this can take several minutes.

## Privacy design

- No secret and no house detail is stored in this repository.
- Tokens, entity names, channel names, people and other house values are **options** of each add-on.
  You set them in the add-on's Configuration tab. They are stored by Home Assistant on your device only.
- Each add-on ships an `options.example.json` with empty or generic values.
- Optional house-specific plugins are not part of this repository. The code runs without them.
- Every export is scanned for private addresses, identifiers, e-mail addresses, phone numbers and secret-shaped strings before it is committed.

## Updates

Keep **auto-update OFF**. Read the change list, then update by hand. Back up first.
New versions start in dry-run mode where an add-on can act on the outside world; check the add-on README before you switch anything live.

## Licence

MIT. See `LICENSE`.
