# Intelligent Home - AI Layer - Claude (ih_ai_layer_claude)

An agent for **Intelligent Home - AI Layer** (`ih_ai_layer`). It registers with the parent with the capabilities `general_qa` and `summarize` (text in, text out) and answers the parent's signed `POST /ask` using Anthropic Claude (REST API, `api.anthropic.com`).

## Privacy
This agent sends text to an OUTSIDE service. Before sending, a scrubber removes email addresses, phone numbers, long digit strings, IPv4 and MAC addresses, URL query strings/fragments and long token-like strings. The request `context` is never sent. The model gets a system instruction that it is a general assistant. Do not rely on the scrubber for secrets: keep private questions away from this agent.

## Options (examples fake; see `options.example.json`)
| Option | Meaning |
|---|---|
| `anthropic_key` | Your own Anthropic Claude API key. Sent in the `x-api-key` header (with `anthropic-version: 2023-06-01`) only. Never in a URL, log or `/status`. |
| `register_key` | Same value as the parent's `register_key`. Empty = link off, nothing is accepted. |
| `parent_url` / `callback_url` | Parent (default gateway, port 8801) / how the parent reaches this agent (empty = gateway, port 8807). |
| `model` | Claude model (default `claude-haiku-5-5`). |
| `daily_cap` | Max Claude calls a day (this costs money, so the default is lower) (default 20). Counted before the call. 0 = never call. |
| `timeout_s` | Timeout of the Claude call (default 30). |
| `shadow` | true (default) = answers "[dry] ..." and calls nothing. Set false to go live. |
| `max_chars` | Replies are cut to this length (default 1900). |

## Safety
Kill switch: file `/data/STOP`. `/status` shows use today versus cap and whether the key is set (not the key). Request text and reply text are never logged. No cache.

## After install
The owner sets boot to auto, watchdog on, auto update on. Starts with boot manual and shadow on. Version 0.1.0: not tested against the real Claude API.
