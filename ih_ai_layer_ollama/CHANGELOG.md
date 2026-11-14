# Changelog

## 0.1.0
- First version. Agent of `ih_ai_layer` that calls an Ollama server on ANOTHER machine in the house (general_qa, summarize, vision). It never runs Ollama itself; Ollama must not run on the Raspberry Pi.
- `ollama_url` is EMPTY by default = off: the agent answers `unavailable` at once and registers as not healthy, so the AI layer skips it. Set it when a host is chosen.
- Starts in shadow mode (nothing is sent). Daily cap, kill switch (`STOP` file), no text or picture logging unless `log_payloads`.
