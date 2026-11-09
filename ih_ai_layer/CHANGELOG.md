# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

## [0.4.16] - 2026-11-09

### Fixed
- Keep the phone-scrubbing test data from matching the publication scanner's real-phone-number pattern.

## [0.4.15] - 2026-11-08

### Fixed
- Compiled `.pyc` files are no longer tracked in git (they were already ignored by `.gitignore`). No code change.

## [0.4.14] - 2026-11-08

### Fixed
- `scrub` no longer turns a plain date such as 2026-11-05 into `[number]` (the phone-number rule matched it). The nightly code reviews were reporting "placeholder [number] in the changelog" for about 20 commits. Phone numbers and long digit runs are still hidden.

## [0.4.13] - 2026-11-05

### Changed
- The add-on page has a sticky top bar: "‹ Intelligent Home" goes back to the Operations console in the whole Home Assistant window (it used to open inside the page frame, and only from a footer link at the very bottom). The footer link also opens in the whole window.

## [0.4.12] - 2026-11-05

### Changed
- Ingress status page: the shared page can now show rows without a score and bars without used/cap (for other add-ons' Today cards). No change to this add-on's own page.

## [0.4.11] - 2026-11-05

### Changed
- Ingress status page v2: one status line with a Check button, a plain "What it does" list, plain-language description, and a "Today" card (daily allowance used and answer quality per AI) from a new read-only `/api/today` on the ingress sidecar. No keys, tokens, URLs or request text are shown.

## [0.4.10] - 2026-11-05

### Changed
- Ingress status page now uses the shared Games-style layout (page heading with service health, status and capabilities cards, collapsible About); the description no longer says providers are never called.

## [0.4.9] - 2026-11-05

### Added
- Generic MCP provider `mcp`: connect any remote MCP server (https only) with a `providers` entry instead of new code. The entry can be listed many times; each `settings` JSON has an `id`, `url`, optional `label`, optional `auth_header` (secret in the entry `key`), an ALLOWLIST of `tools` (each mapped to a capability, with the tool argument that receives the request text and optional `context_args`), optional `keywords` and the usual caps/timeouts/`shadow`. Only listed tools can ever be called; anything else is refused before any network call. See DOCS.md "Connect any MCP server".
- The server's `tools/list` is read on first use and every 24 hours; only listed tools that the server really offers are callable, and missing ones are logged. Listed tools that are not marked read-only by the server show as `write_tools` in the provider status.
- Keywords from `mcp` settings are merged into the classifier at startup (whole words, never overriding a built-in capability).
- Agent name `ih_ai_layer_mcp_<id>` (scores, trace spans). Request text, argument values and keys are never logged; the key is sent only in the chosen header and redirects are not followed.

### Changed
- The MCP client code (session, JSON-RPC, SSE parsing, re-initialise on HTTP 400/404) moved from `publicapis` into `providers/mcp_client.py`, shared by both. PublicAPIs.io behaves as before.

## [0.4.8] - 2026-11-05

### Added
- Search first, then answer: new capability `current_facts`, handled by the router itself. Questions about prices, release dates, news, scores, weather and other time-sensitive facts are searched on the web first; a general_qa agent then answers ONLY from the numbered results. Links in the answer that are not result links are removed (note "(link removed: not in search results)"), and the answer ends with a short "Sources:" list. If no search is possible the answer is plain general_qa with the note "(No web search available - this may be out of date.)".
- New built-in provider `serper` (Serper.dev, Google results, 2,500 free credits): capability `web_search`, option `serper_key`, daily cap 60, results cached 6 hours (never errors or empty results). The search text is not scrubbed and never logged.
- Classifier: time-sensitive words (whole words only) now choose `current_facts` before `web_search`. Showtimes and API search are unchanged.
- Trace: the request span has `results` (number of search results; `chars` is left out so there are at most 8 attrs); a `search` span per search.

### Changed
- The general_qa system prompt now tells every provider to say so when it cannot verify current information, and never to invent links.

## [0.4.7] - 2026-11-05

### Added
- Trace spans now carry routing metadata (content-free): the request span has capability, whether the classifier chose it, the chosen agent and model, its routing score (x1000), the number of live candidates and the agents tried; each ask span has agent, model, score and answer length. A forced-model request has no score and is not classified.
- The `/request` response now includes the model that answered on the scored route too, not only for forced-model requests.

## [0.4.6] - 2026-11-05

### Fixed
- A request with no capability and no keyword match now goes to `general_qa` instead of failing with "No agent". Plain questions from callers that leave the capability empty (such as the Discord AI bot) were refused.

## [0.4.5] - 2026-11-05

### Fixed
- Answers from thinking models were cut off mid-sentence (Gemini 3.5 Flash) or empty (OpenRouter free router): the output budget was a fixed 1024 tokens. New provider setting `max_tokens`, default 4096.
- Default `max_chars` raised from 1900 to 4000 (limit 8000), so long answers and answer comparisons are not cut.

## [0.4.4] - 2026-11-05

### Added
- Built-in provider `publicapis` (PublicAPIs.io MCP directory, free, no key): capabilities api_search, api_info, api_quickstart, api_compare, api_offerings. Result cache (`cache_hours`, default 24). The owner-side deal tools are not implemented.
- Keyword rule routes "public API" style requests to api_search.
- Tests for the provider (`tests/test_publicapis.py`).

### Changed
- Gemini default model is now `gemini-3.5-flash` (Google retired 2.5 flash for this account).

## [0.4.3] - 2026-11-04

### Fixed
- Chat providers now send a named User-Agent. Groq rejected the default Python one with HTTP 403 (Cloudflare error 1010).
- Groq default model is now `openai/gpt-oss-120b`. Groq no longer offers `llama-3.3-70b-versatile`.

## [0.4.2] - 2026-11-03

### Added
- Scoped Games artwork token, restricted to the `game_art_search` capability on `/request`.
- SerpApi Google Images search capability for official game box art.

## [0.4.1] - 2026-11-03

### Added
- Ingress panel (sidebar page) showing this add-on's status.

## [0.4.0] - 2026-11-02

### Added
- Adds correlated trace spans for router and provider attempts to trace-enabled requests.

## [0.3.3] - 2026-10-31

### Changed
- Prebuilt image, no code change.

## [0.3.2] - 2026-10-28

### Removed
- The separate provider add-ons (Gemini, Groq, OpenRouter, Mistral, OpenAI, Pollinations, SerpApi, Grok, Claude) are no longer in the repository. Their providers are built in. Uninstall any you still have.

## [0.3.1] - 2026-10-28

### Added
- Persistent request log: one JSON line per handled request in `/data/usage.jsonl` (time, ai, model, script, what, ok, ms, by, via). It holds metadata only: the capability name, never request text, answers or keys. The last 2000 lines are kept.
- `GET /usage?since=<ts>` (token needed) returns the recent lines, so the house dashboards can show requests that go through the AI layer.

## [0.3.0] - 2026-10-28

### Added
- Built-in AI providers (Phase 4 of the migration): Gemini, Groq, OpenRouter, Mistral, OpenAI, Pollinations, SerpApi, Grok and Claude now run inside this add-on. They keep the request code, default model, capabilities, daily and spend caps and text scrubbing of the old agent add-ons.
- Option `providers`: a list of `name`, `enabled`, `key`, `model` and `settings` (JSON text for caps and other settings).
- Optional key options the settings hub can fill: `gemini_key`, `groq_key`, `openrouter_key`, `mistral_key`, `openai_key`, `pollinations_key`, `serpapi_key`, `xai_key`, `anthropic_key`. A key in a `providers` entry wins.
- `/status` lists the built-in providers (never keys). `/agents` marks them `in_process`.

### Changed
- Separate agent add-ons still work. If an external agent with the same name is live and healthy, it wins; when it stops registering, the built-in provider takes over. Scores carry over because the names are the same (`ih_ai_layer_groq` and so on).
- The HTTP API is unchanged.
- `claude_cli` stays a separate add-on.

## [0.2.1] - 2026-10-28

### Added
- Routes AI requests to registered agent add-ons and remembers which agent answers best.

## 0.4.1
- Add a shared-style, Supervisor-ingress add-on console with live health status and capability summary.
