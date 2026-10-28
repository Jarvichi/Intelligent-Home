# Intelligent Home - AI Layer - Claude CLI Agent

## What it does

Agent of Intelligent Home - AI Layer: general questions and summaries via the Claude Code CLI (text scrubbed first). Agent (child) of `ih_ai_layer`.

## Requires

- `ih_ai_layer` add-on
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - AI Layer - Claude CLI Agent**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `register_key` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in shadow mode, so it changes nothing yet.
5. When the log looks right, set `shadow` to `false` to go live.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `register_key` | Yes | Same value as the parent's `register_key`. Empty = link off. |
| `parent_url` | No | Address of the parent add-on. Default: `http://172.30.32.1:8801`. |
| `callback_url` | No | How the parent reaches this add-on. Empty = the Supervisor gateway and this add-on's port. |
| `model` | No | CLI model name or alias (default `sonnet`). Registered as this agent's `models`. |
| `spend_cap` | No | Max PAID calls a day (default 0 = free only). |
| `free_tier` | No | true = a CLI call on your plan costs nothing extra, so calls are free. Default false: every call counts as paid until you set this. |
| `daily_cap` | No | Max CLI calls a day (default 20). 0 = never call. |
| `timeout_s` | No | Hard timeout of the CLI call (default 90). |
| `shadow` | No | On (default): answers or logs only, posts nothing. Turn off to go live. |
| `max_chars` | No | Replies are cut to this length (default 1900). |
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `max_week_percent` | No | Weekly usage limit in percent (default 50). |
| `usage_url` | No | HTTP endpoint returning JSON `{"week_percent": <number>}`. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `shadow` is on. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Parent does not list this add-on / "cannot reach parent".** `register_key` must be identical in both add-ons, and `parent_url` must point at the parent (default gateway address and port). An empty key turns the link off.
- **Error `spend_cap` or `daily_cap`.** Calls are counted before they are made. Raise the cap, or set `free_tier` to true if your plan is free. A missing or wrong API key also gives an upstream error: check `/status`, which shows whether a key is set (never the key).

## Privacy

The question text is scrubbed (email addresses, phone numbers, long digit strings, IP and MAC addresses, URL queries and token-like strings are removed), then sent to Anthropic by the Claude Code CLI using a login you create inside the add-on. Keep private questions away from this agent.

## More details

### Login (Owner only)
**The owner must log in himself, inside this add-on.** This is new access and the owner's decision. The assistant must never do the login, never read the stored login files and never copy them elsewhere. Until the owner has logged in, live calls fail with `upstream_error`. How the login is made (a terminal in the add-on, running `claude` with `HOME=/data/claude-home`) is UNVERIFIED and is not set up by this add-on.

### Safety design
- All tools OFF, no permission prompts, no MCP servers, no slash commands, no settings files (hooks), no session files.
- Working dir is a new empty temp dir per call. No folders are mapped.
- The subprocess gets only PATH, HOME and LANG. None of this add-on's options or keys reach it.
- Prompt on stdin (never on the command line). Hard timeout (`timeout_s`). Output cut to `max_chars`. One call at a time (a second gets `busy`).
- Text is scrubbed (email, phone, long digits, IPv4, MAC, URL queries, token-like strings) before it is sent. The request `context` is never sent.
- Kill switch: file `/data/STOP`.

### CLI flags used (UNVERIFIED: never run against a real CLI)
`claude -p --tools "" --permission-mode dontAsk --strict-mcp-config --mcp-config '{"mcpServers":{}}' --disable-slash-commands --setting-sources "" --no-session-persistence --output-format text --system-prompt <text> --model <model>`
Check each flag against `claude --help` of the installed version before going live. If a flag is unknown the CLI exits non-zero and the call fails safely (`upstream_error`).

### Usage gate
Before each live call the agent reads `usage_url` (HTTP GET, JSON with a number `week_percent`). Empty or unreadable url or no number = refused with why `usage_unknown`. `week_percent` above `max_week_percent` = refused with why `usage_gate`. Refused calls are not counted.
