# Intelligent Home - AI Layer

## What it does

Intelligent Home - AI Layer: routes AI requests to AI providers (built in) and agent add-ons. No Discord. Parent of its family.

## Requires

- No other Intelligent Home add-on is required.
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - AI Layer**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `api_token`, `register_key` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in shadow mode, so it changes nothing yet.
5. When the log looks right, set `shadow` to `false` to go live.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `api_token` | Yes | Bearer token callers must send to this add-on's API. |
| `register_key` | Yes | Shared secret. Must be the same value in this add-on and its parent. Empty = link off. |
| `shadow` | No | On (default): answers or logs only, posts nothing. Turn off to go live. |
| `agent_timeout_s` | No | How long to wait for an agent to answer, in seconds. Default: `20`. |
| `rate_per_min` | No | Maximum requests a minute. Default: `30`. |
| `max_tries` | No | How many agents to try for one request. Default: `2`. |
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `providers` | No | List of built-in providers: `name`, `enabled`, `key`, `model`, `settings` (JSON text for caps). See Built-in providers. |
| `gemini_key` | No | Gemini API key. The settings hub can fill it. A key in the `providers` entry wins. |
| `groq_key` | No | Groq API key. The settings hub can fill it. |
| `openrouter_key` | No | OpenRouter API key. The settings hub can fill it. |
| `mistral_key` | No | Mistral API key. The settings hub can fill it. |
| `openai_key` | No | OpenAI API key. The settings hub can fill it. |
| `pollinations_key` | No | Pollinations key (optional: the service works without one). |
| `serpapi_key` | No | SerpApi key. The settings hub can fill it. |
| `xai_key` | No | xAI (Grok) API key. The settings hub can fill it. |
| `anthropic_key` | No | Anthropic (Claude) API key. The settings hub can fill it. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `shadow` is on. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Parent does not list this add-on / "cannot reach parent".** `register_key` must be identical in both add-ons, and `parent_url` must point at the parent (default gateway address and port). An empty key turns the link off.

## Privacy

Each request goes to a built-in provider you enabled, or to an agent add-on you registered. Built-in providers send the question text to that provider with your own key, after a scrubber removes email addresses, phone numbers, long digit strings, IP and MAC addresses, URL queries and token-like strings; the request context is never sent. SerpApi sends the search text as it is. Pollinations prompts are public. Do not rely on the scrubber for secrets.

## More details

### API (port 8801)
All except `/healthz` and `/register` need the header `Authorization: Bearer <api_token>`. With an empty `api_token` everything is refused.

| Call | Meaning |
|---|---|
| `POST /request` | Body `{text, capability (optional), context (optional object), model (optional string), request_id}`. With `model`: no classifier or scoring, the request goes only to the live agent that lists that model (agents register a `models` list), with no fallback to other agents; none = HTTP 404 `model_not_available`. Shadow mode, STOP, rate limit and auth apply as usual. If the agent refuses because of its `spend_cap`, the reply has `error: spend_cap`. Returns `{ok, answer, agent, capability, classified, took_ms, request_id}`. |
| `POST /feedback` | Body `{request_id, score}`; a score above 0 is a thumbs up, otherwise a thumbs down. Counts for the agent that answered that request (the last 500 requests are remembered). |
| `GET /agents` | Registered agents and their capabilities. |
| `GET /status` | Mode, counts, agents, score table. Never contains request text. |
| `GET /healthz` | Plain `ok`. |
| `POST /register` | Used by agent add-ons (signed, see below). |

If no `capability` is given, a simple keyword rule picks one and `classified` is `true`. If nothing matches, or no live agent has that capability, the answer says so ("No agent ..."). The best agent is tried first (score = thumbs, success rate and speed); on failure the next one is tried, up to `max_tries`.

### Safety
- **Shadow mode** (option `shadow`, on by default): agents are NOT called; the answer is a canned "[dry]" line naming the agent that would be asked.
- **Kill switch:** create the file `/data/STOP` and `/request` and `/feedback` return 503.
- **Rate limit:** `rate_per_min` requests a minute, then 429.
- **Privacy:** request text is never stored or shown in `/status`. Each `/request` and each agent try logs ONE line with metadata only (request id, caller, capability, agent, model, chars in/out, ms, ok, why, free, shadow). With option `log_payloads` true the line also holds the first 200 chars of the request and answer (one line, newlines escaped). Keys, tokens, signatures and headers are never logged. The text IS sent to the chosen agent add-on (that is the job), so choose agents you trust.
- Agent links are signed (HMAC, 5 minute freshness) with the shared `register_key`. Empty `register_key` = nobody can register and no agent is called.

### Built-in providers
Nine AI providers run inside this add-on (Phase 4). Each does what the old agent add-on did: same request code, default model, capabilities, daily and spend caps, and text scrubbing. `claude_cli` is not here (it needs the Claude CLI) and stays a separate add-on.

| Provider (`name`) | Capabilities | Default model | Key option | Plan |
|---|---|---|---|---|
| `gemini` | general_qa, summarize | gemini-2.5-flash | `gemini_key` | paid unless `free_tier` |
| `groq` | general_qa, summarize | llama-3.3-70b-versatile | `groq_key` | paid unless `free_tier` |
| `openrouter` | general_qa, summarize | openrouter/auto | `openrouter_key` | paid unless `free_tier` or a `:free` model |
| `mistral` | general_qa, summarize | mistral-medium-latest | `mistral_key` (+ setting `agent_id`) | free by default |
| `openai` | general_qa, summarize | gpt-6-luna | `openai_key` | paid unless `free_tier` |
| `pollinations` | general_qa, summarize | openai | `pollinations_key` (optional) | free by default; prompts are PUBLIC |
| `serpapi` | showtimes, web_search | none | `serpapi_key` | paid unless `free_tier` |
| `grok` | general_qa, summarize | grok-4 | `xai_key` | paid unless `free_tier` |
| `claude` | general_qa, summarize | claude-haiku-5-5 | `anthropic_key` | paid unless `free_tier` |

A provider is used only when it has an entry in `providers` with `enabled` true and a key (the entry `key`, or the flat key option above). Paid calls need `spend_cap` above 0 in the `settings`.

#### The `providers` option
One entry per provider: `name`, `enabled`, `key` (optional: empty = use the flat key option), `model` (optional: empty = default) and `settings` (optional JSON text). Example settings: `{"daily_cap": 50, "spend_cap": 0, "free_tier": true, "timeout_s": 30, "max_chars": 1900, "shadow": false, "log_payloads": false}`. Extra settings: `agent_id` (mistral, required), `cache_hours` and `default_cinema` (serpapi). The daily cap counts calls per day, before the call. A provider-level `shadow` is off by default; the router `shadow` option already stops every call.

#### How the router uses them
Built-in providers join the same scoring and learning as agents, under the same names as the old add-ons (`ih_ai_layer_groq` and so on), so earlier scores still count. If an external agent add-on with the same name is live and healthy, it wins; when it stops registering (for example it is stopped or uninstalled), the built-in provider answers. Both can exist during the move. A file `/data/providers/<name>/STOP` stops one provider.

### Provider options
| Option | What it is |
|---|---|
| `providers` | List of built-in providers: `name`, `enabled`, `key`, `model`, `settings` (JSON text for caps). See Built-in providers. |
| `gemini_key` | Gemini API key. The settings hub can fill it. A key in the `providers` entry wins. |
| `groq_key` | Groq API key. The settings hub can fill it. |
| `openrouter_key` | OpenRouter API key. The settings hub can fill it. |
| `mistral_key` | Mistral API key. The settings hub can fill it. |
| `openai_key` | OpenAI API key. The settings hub can fill it. |
| `pollinations_key` | Pollinations key (optional: the service works without one). |
| `serpapi_key` | SerpApi key. The settings hub can fill it. |
| `xai_key` | xAI (Grok) API key. The settings hub can fill it. |
| `anthropic_key` | Anthropic (Claude) API key. The settings hub can fill it. |

### Move an agent add-on into a built-in provider
Do one provider at a time. Never paste a key into a chat.
1. Make sure the settings hub has the agent's key. If the orchestrator `provision` is on, `ih_ai_layer` gets `<provider>_key` filled by itself (the hub imports it from the agent add-on). Otherwise copy the value of the agent's key option into the same-named option here (`gemini_key`, `groq_key`, `openrouter_key`, `mistral_key`, `openai_key`, `pollinations_key`, `serpapi_key`; Grok uses `xai_key`; Claude uses `anthropic_key`).
2. Add a `providers` entry: `name`, `enabled` true. Copy `model`, `spend_cap`, `free_tier`, `daily_cap`, `timeout_s`, `max_chars` (and Mistral `agent_id`, SerpApi `cache_hours` and `default_cinema`) from the agent add-on into `settings` as JSON, so the caps stay the same. Leave `key` empty.
3. Save and restart this add-on. Read the log: one `provider <name>` line says `configured`.
4. While the agent add-on is still running, it keeps answering. Stop the agent add-on, then send a test request: `/agents` shows the provider as `in_process` and live.
5. When it works, uninstall the agent add-on. Its daily counters start again from zero in the new place.

### Status
Version 0.3.0: built-in providers added; tests use fake network calls only. Scores live in `/data/scores.json`.
