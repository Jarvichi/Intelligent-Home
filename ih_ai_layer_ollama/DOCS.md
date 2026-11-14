# Intelligent Home - AI Layer - Ollama Agent

## What it does

Agent of Intelligent Home - AI Layer. Sends general questions, summaries and picture questions (`vision`) to an Ollama server **on another machine in the house**. Nothing leaves the home network. It never runs Ollama itself: do not run Ollama on the Raspberry Pi (too little memory, it swaps and overheats).

With `ollama_url` empty (the default) the agent is off: it registers as not healthy and answers `unavailable` at once, so the AI layer uses another agent.

## Requires

- `ih_ai_layer` add-on
- An Ollama server on another always-on machine, bound to the home network only (it has no login). Not chosen yet.

## Set-up

1. Install Ollama on the chosen machine, pull a text model (`llama3.2:3b`) and a picture model (`llava:7b`), and firewall it to the home network.
2. Install this add-on, set `register_key` (the settings hub fills it) and `ollama_url` (for example `http://nas.local:11434`, no path).
3. Start it and read the log. It starts in shadow mode (nothing is sent). Set `shadow` to `false` to go live.

## Options

| Option | Required? | What it is |
|---|---|---|
| `register_key` | Yes | Same value as the parent's `register_key`. |
| `parent_url` | No | Address of the parent add-on. |
| `callback_url` | No | How the parent reaches this add-on. Empty = the Supervisor gateway and port 8817. |
| `ollama_url` | No | `http://host:port` of the Ollama server. Empty = off. |
| `model` | No | Text model (default `llama3.2:3b`). |
| `vision_model` | No | Picture model (default `llava:7b`). |
| `timeout_s` | No | Wait for the server (default 120). |
| `daily_cap` | No | Max calls a day (default 500). |
| `shadow` | No | true = answer a dry note, send nothing. |
| `max_chars` | No | Longest answer kept. |
| `log_payloads` | No | Log the first 200 characters of questions and answers. A picture is never logged. |
