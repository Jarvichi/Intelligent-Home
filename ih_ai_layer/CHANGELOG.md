# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

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
