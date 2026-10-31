# Changelog

All notable changes to this add-on are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the add-on uses semantic versioning.

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
