# Intelligent Home - AI Layer - Claude CLI Agent (ih_ai_layer_claude_cli)

An agent for **Intelligent Home - AI Layer** (`ih_ai_layer`). It registers with the capabilities `general_qa` and `summarize` and answers the parent's signed `POST /ask` by running the Claude Code CLI in print mode (`claude -p`) as a subprocess. There is no API key: the CLI uses a login stored in this add-on's own `/data` volume (`/data/claude-home`).

## Login (Owner only)
**The owner must log in himself, inside this add-on.** This is new access and the owner's decision. The assistant must never do the login, never read the stored login files and never copy them elsewhere. Until the owner has logged in, live calls fail with `upstream_error`. How the login is made (a terminal in the add-on, running `claude` with `HOME=/data/claude-home`) is UNVERIFIED and is not set up by this add-on.

## Safety design
- All tools OFF, no permission prompts, no MCP servers, no slash commands, no settings files (hooks), no session files.
- Working dir is a new empty temp dir per call. No folders are mapped.
- The subprocess gets only PATH, HOME and LANG. None of this add-on's options or keys reach it.
- Prompt on stdin (never on the command line). Hard timeout (`timeout_s`). Output cut to `max_chars`. One call at a time (a second gets `busy`).
- Text is scrubbed (email, phone, long digits, IPv4, MAC, URL queries, token-like strings) before it is sent. The request `context` is never sent.
- Kill switch: file `/data/STOP`.

## CLI flags used (UNVERIFIED: never run against a real CLI)
`claude -p --tools "" --permission-mode dontAsk --strict-mcp-config --mcp-config '{"mcpServers":{}}' --disable-slash-commands --setting-sources "" --no-session-persistence --output-format text --system-prompt <text> --model <model>`
Check each flag against `claude --help` of the installed version before going live. If a flag is unknown the CLI exits non-zero and the call fails safely (`upstream_error`).

## Usage gate
Before each live call the agent reads `usage_url` (HTTP GET, JSON with a number `week_percent`). Empty or unreadable url or no number = refused with why `usage_unknown`. `week_percent` above `max_week_percent` = refused with why `usage_gate`. Refused calls are not counted.

## Options (see `options.example.json`)
| Option | Meaning |
|---|---|
| `register_key` | Same value as the parent's `register_key`. Empty = link off. |
| `parent_url` / `callback_url` | Parent (default gateway, port 8801) / how the parent reaches this agent (empty = gateway, port 8814). |
| `model` | CLI model name or alias (default `sonnet`). Registered as this agent's `models`. |
| `spend_cap` | Max PAID calls a day (default 0 = free only). |
| `free_tier` | true = a CLI call on your plan costs nothing extra, so calls are free. Default false: every call counts as paid until you set this. |
| `daily_cap` | Max CLI calls a day (default 20). 0 = never call. |
| `timeout_s` | Hard timeout of the CLI call (default 90). |
| `shadow` | true (default) = answers "[dry] ..." and runs nothing. |
| `max_chars` | Replies are cut to this length (default 1900). |
| `max_week_percent` | Weekly usage limit in percent (default 50). |
| `usage_url` | HTTP endpoint returning JSON `{"week_percent": <number>}`. |
| `log_payloads` | false (default) = ONE metadata log line per /ask. true = also the first 200 chars of request and answer. |

## After install
The owner sets boot to auto, watchdog on, auto update on. Starts with boot manual and shadow on. Version 0.1.0: Dockerfile (npm install of `@anthropic-ai/claude-code` on a node base) and CLI flags UNVERIFIED; not build-tested.
