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
| `games_art_token` | No | Optional Games-only token. It can call `/request` only with `game_art_search`; it cannot access status, usage, feedback or other AI capabilities. |
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
| `igdb_key` | No | Twitch client secret for the `igdb` provider (its client id goes in that provider's `settings` as `client_id`). |
| `serper_key` | No | Serper.dev key (used by the `serper` provider and the search-first flow). The settings hub can fill it. |
| `xai_key` | No | xAI (Grok) API key. The settings hub can fill it. |
| `anthropic_key` | No | Anthropic (Claude) API key. The settings hub can fill it. |

## Search first, then answer (`current_facts`)

A general model has no web access and guesses about current things (a price, a release date). For those questions the router searches first:

1. The classifier picks `current_facts` when the text has a time-sensitive word (price, cost, how much, release date, launch, latest, news, today, now, still, upcoming, score, weather, 2025-2027 ...; whole words only). Showtimes and API questions keep their own routes. A caller can also ask for `current_facts` by name.
2. A short search query (at most 120 characters, filler and mentions removed) goes to the best live `web_search` agent: `serper` first, then others such as `serpapi`. Only the query leaves the house, never the whole text.
3. A general_qa agent (normal scoring, fallback and `max_tries`) gets a prompt that says: answer ONLY from the numbered results, quote the link of each result used, say so if the results do not answer, never invent links, prices or dates. The prompt stays under the 4000-character limit (results are dropped from the end, never the question).
4. Link check: any http(s) link in the answer that is not a result link (same host and path; a shorter path on the same host is allowed) is removed and "(link removed: not in search results)" is added once.
5. The answer is cut to about 1100 characters, then ends with "Sources:" and the result links it cites (or the top 3 results if it cites none).
6. No search agent, a failed search or no results: plain general_qa, with the note "(No web search available - this may be out of date.)" first.

`/feedback` scores the general_qa agent that wrote the answer. The response also has `search` (ok, none or empty), `results`, `links_removed`. Usage log: one `current_facts` line plus one `web_search` line for the search. Trace: request span attrs include `results`; a `search` span names the search agent. Each Serper search uses one free credit, so the daily cap (default 60) matters: when it is reached, questions fall back to the plain answer with the note.

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
| `POST /request` | Body `{text, capability (optional), context (optional object), model (optional string), request_id, trace_id (optional 32-hex opaque ID), parent_span_id (optional 16-hex ID)}`. With `model`: no classifier or scoring, the request goes only to the live agent that lists that model (agents register a `models` list), with no fallback to other agents; none = HTTP 404 `model_not_available`. Shadow mode, STOP, rate limit and auth apply as usual. If the agent refuses because of its `spend_cap`, the reply has `error: spend_cap`. Trace-enabled requests also return `trace_id` and `trace_spans` containing component, operation, outcome, duration and reason code only; they never include request/response text. |
| `POST /feedback` | Body `{request_id, score}`; a score above 0 is a thumbs up, otherwise a thumbs down. Counts for the agent that answered that request (the last 500 requests are remembered). |
| `GET /agents` | Registered agents and their capabilities. |
| `GET /status` | Mode, counts, agents, score table. Never contains request text. |
| `GET /usage?since=<ts>` | Recent request log lines (token needed): time, ai, model, script, what (capability only), ok, ms, by, via. Last 2000 kept in `/data/usage.jsonl`. Never contains request text, answers or keys. |
| `GET /healthz` | Plain `ok`. |
| `POST /register` | Used by agent add-ons (signed, see below). |

**Pictures (0.6.0):** add `image` (base64 JPEG/PNG text, up to 1.5 million characters) and the request is routed with capability `vision` (the only one allowed with an image; no `model`). The picture goes only to an agent that registered `vision`. The built-in Gemini provider declares `vision`; without a live agent for it the answer is "No agent is available for capability 'vision'". Gemini sends the picture to Google as it is (only JPEG/PNG are accepted; picture metadata is not stripped). The picture is never classified, scrubbed, logged or sent to web search. A picture sent to Gemini leaves the house. Turn Gemini off (or leave `vision` unrequested) if a picture must stay inside. `/request` bodies may be up to 1.6 MB; every other call stays at 16 KB.

If no `capability` is given, a simple keyword rule picks one and `classified` is `true`. If nothing matches, the request is treated as `general_qa`. If no live agent has the capability, the answer says so ("No agent ..."). The best agent is tried first (score = thumbs, success rate and speed); on failure the next one is tried, up to `max_tries`.

### Safety
- **Shadow mode** (option `shadow`, on by default): agents are NOT called; the answer is a canned "[dry]" line naming the agent that would be asked.
- **Kill switch:** create the file `/data/STOP` and `/request` and `/feedback` return 503.
- **Rate limit:** `rate_per_min` requests a minute, then 429.
- **Privacy:** request text is never stored or shown in `/status`. Each `/request` and each agent try logs ONE line with metadata only (request id, caller, capability, agent, model, chars in/out, ms, ok, why, free, shadow). With option `log_payloads` true the line also holds the first 200 chars of the request and answer (one line, newlines escaped). Keys, tokens, signatures and headers are never logged. The text IS sent to the chosen agent add-on (that is the job), so choose agents you trust.
- Agent links are signed (HMAC, 5 minute freshness) with the shared `register_key`. Empty `register_key` = nobody can register and no agent is called.

### Built-in providers
Ten providers run inside this add-on (Phase 4). Each does what the old agent add-on did: same request code, default model, capabilities, daily and spend caps, and text scrubbing. `claude_cli` is not here (it needs the Claude CLI) and stays a separate add-on.

| Provider (`name`) | Capabilities | Default model | Key option | Plan |
|---|---|---|---|---|
| `gemini` | general_qa, summarize | gemini-3.5-flash | `gemini_key` | paid unless `free_tier` |
| `groq` | general_qa, summarize | openai/gpt-oss-120b | `groq_key` | paid unless `free_tier` |
| `openrouter` | general_qa, summarize | openrouter/auto | `openrouter_key` | paid unless `free_tier` or a `:free` model |
| `mistral` | general_qa, summarize | mistral-medium-latest | `mistral_key` (+ setting `agent_id`) | free by default |
| `openai` | general_qa, summarize | gpt-6-luna | `openai_key` | paid unless `free_tier` |
| `pollinations` | general_qa, summarize | openai | `pollinations_key` (optional) | free by default; prompts are PUBLIC |
| `serpapi` | showtimes, web_search | none | `serpapi_key` | paid unless `free_tier` |
| `serper` | web_search, game_art_search | none | `serper_key` | free (2,500 credits); daily cap 60 |
| `igdb` | game_art_search | none | `igdb_key` (Twitch client secret) + setting `client_id` | free (Twitch developer app); daily cap 500; portrait covers incl. console games |
| `steamstore` | game_art_search | none | none (no key) | free; daily cap 200; official Steam art for titles on Steam |
| `grok` | general_qa, summarize | grok-4 | `xai_key` | paid unless `free_tier` |
| `claude` | general_qa, summarize | claude-haiku-5-5 | `anthropic_key` | paid unless `free_tier` |
| `publicapis` | api_search, api_info, api_quickstart, api_compare, api_offerings | none | none needed | free |
| `mcp` | the capabilities you list for each server | none | entry `key` (optional) | free unless the server charges; see "Connect any MCP server" |

A provider is used only when it has an entry in `providers` with `enabled` true and a key (`publicapis` needs no key) (the entry `key`, or the flat key option above). Paid calls need `spend_cap` above 0 in the `settings`.

#### The `providers` option
One entry per provider: `name`, `enabled`, `key` (optional: empty = use the flat key option), `model` (optional: empty = default) and `settings` (optional JSON text). Example settings: `{"daily_cap": 50, "spend_cap": 0, "free_tier": true, "timeout_s": 30, "max_chars": 4000, "max_tokens": 4096, "shadow": false, "log_payloads": false}`. Extra settings: `agent_id` (mistral, required), `cache_hours` and `default_cinema` (serpapi), `cache_hours` (serper, default 6), `cache_hours` (publicapis, default 24). `max_tokens` (default 4096) is the output budget per answer; thinking models use part of it for reasoning, so do not set it low. `max_chars` (default 4000, at most 8000) cuts the returned answer. The daily cap counts calls per day, before the call. A provider-level `shadow` is off by default; the router `shadow` option already stops every call.

#### How the router uses them
Built-in providers join the same scoring and learning as agents, under the same names as the old add-ons (`ih_ai_layer_groq` and so on), so earlier scores still count. If an external agent add-on with the same name is live and healthy, it wins; when it stops registering (for example it is stopped or uninstalled), the built-in provider answers. Both could exist during the move. The separate provider add-ons were removed from the repository on 28 Oct 2026; uninstall any you still have. A file `/data/providers/<name>/STOP` stops one provider.

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
| `serper_key` | Serper.dev key. The settings hub can fill it. |
| `xai_key` | xAI (Grok) API key. The settings hub can fill it. |
| `anthropic_key` | Anthropic (Claude) API key. The settings hub can fill it. |

### Move an agent add-on into a built-in provider
Do one provider at a time. Never paste a key into a chat.
1. Make sure the settings hub has the agent's key. If the orchestrator `provision` is on, `ih_ai_layer` gets `<provider>_key` filled by itself (the hub imports it from the agent add-on). Otherwise copy the value of the agent's key option into the same-named option here (`gemini_key`, `groq_key`, `openrouter_key`, `mistral_key`, `openai_key`, `pollinations_key`, `serpapi_key`; Grok uses `xai_key`; Claude uses `anthropic_key`).
2. Add a `providers` entry: `name`, `enabled` true. Copy `model`, `spend_cap`, `free_tier`, `daily_cap`, `timeout_s`, `max_chars` (and Mistral `agent_id`, SerpApi `cache_hours` and `default_cinema`) from the agent add-on into `settings` as JSON, so the caps stay the same. Leave `key` empty.
3. Save and restart this add-on. Read the log: one `provider <name>` line says `configured`.
4. While the agent add-on is still running, it keeps answering. Stop the agent add-on, then send a test request: `/agents` shows the provider as `in_process` and live.
5. When it works, uninstall the agent add-on. Its daily counters start again from zero in the new place.

#### PublicAPIs.io (`publicapis`)
A directory of 1,500+ public APIs, read through its free MCP server (https://publicapis.io/mcp, no key). Use it to find an API for a task, read its details or a starter code snippet, or compare APIs. The request text is a search query or an API slug and is sent as it is (not scrubbed); it is never logged.

| Capability | Request text | Optional `context` keys |
|---|---|---|
| `api_search` | what the API should do | `category`, `auth` (`none`, `api_key`, `oauth`), `free` (bool), `https` (bool), `limit` (1-20) |
| `api_info` | API slug | `slug` |
| `api_quickstart` | API slug | `slug`, `language` (`javascript`, `python`, `curl`; default `python`) |
| `api_compare` | 2-5 slugs split by commas or spaces | `slugs` (list) |
| `api_offerings` | not used | none |

Invalid context values are dropped. Answers are cached for `cache_hours` (default 24, 0 = off); errors are never cached. Daily cap default 200. One MCP session is kept per process and re-created once if the server drops it. Only the read-only tools are used. The server's other tools (`start_deal`, `send_message`, `get_deal`) are for API owners who buy a paid listing or sponsorship; they are deliberately NOT implemented. Results come from a third-party directory and are unverified: check an API's terms and safety before using it. A plain request such as "find a public API for weather" is routed to `api_search` by the keyword rule.

### Connect any MCP server (`mcp`)
A new MCP server (streamable HTTP, https only) is connected by a settings entry, not by code. Add an entry to `providers` with `name` `mcp`. List it once per server; each `id` must be different. Example `settings` (placeholder address):

```
{"id": "demo", "url": "https://example.com/mcp", "label": "Demo server",
 "auth_header": "Authorization",
 "tools": {"search_items": {"capability": "demo_search", "arg": "query", "context_args": ["limit"]}},
 "keywords": {"demo_search": ["demo item", "demo catalogue"]},
 "daily_cap": 100, "cache_hours": 1, "timeout_s": 30, "max_chars": 6000, "shadow": false}
```

- `id` (required): a-z, 0-9 and `_`, up to 24 characters. The agent is called `ih_ai_layer_mcp_<id>`.
- `url` (required): must start with `https://`. Redirects are not followed.
- `auth_header` (optional) and the entry `key`: the key is sent as the value of that header. For `Authorization`, a key with no scheme gets `Bearer ` in front. With no `auth_header` the key is not used. The key is never logged or shown.
- `tools` (required): the ALLOWLIST. Only tools named here can ever be called; any other tool is refused before any network call, even if a caller asks for it. Each tool gives a `capability` (the name callers use), `arg` (the tool argument that receives the request text) and optional `context_args` (extra arguments that may be taken from the request `context`; other context values are ignored). One capability per tool.
- `keywords` (optional): `{"capability": ["word", ...]}` so a plain question is routed to the capability. Whole words or phrases, merged at startup; they never override a built-in capability.
- `daily_cap`, `cache_hours` (default 1; only answers of tools that the server marks read-only are cached), `timeout_s`, `max_chars`, `shadow` work as for the other providers. `scrub` (default true) removes private-looking data from the request text before it is sent.

On first use, and every 24 hours, the layer reads the server's tool list and keeps only listed tools that the server really offers. Listed tools the server does not offer are logged by name. A listed tool that the server does not mark read-only shows under `write_tools` in the provider status; the allowlist is the owner's decision. Do not allowlist tools that spend money or send messages unless the owner has approved that specific tool. A bad entry (no `id`, an `http` address, a bad `id`, an empty allowlist) is skipped with a log line. Request text, argument values and keys are never logged.

### Status
Version 0.3.1: request usage log (`/usage`); 0.3.0: built-in providers added; tests use fake network calls only. Scores live in `/data/scores.json`.
