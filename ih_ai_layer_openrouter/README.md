# Intelligent Home - AI Layer - OpenRouter (ih_ai_layer_openrouter)

An agent for **Intelligent Home - AI Layer** (`ih_ai_layer`). It registers with the parent with the capabilities `general_qa` and `summarize` (text in, text out) and answers the parent's signed `POST /ask` using OpenRouter (REST API, `openrouter.ai`).

## Privacy
This agent sends text to an OUTSIDE service. Before sending, a scrubber removes email addresses, phone numbers, long digit strings, IPv4 and MAC addresses, URL query strings/fragments and long token-like strings. The request `context` is never sent. The model gets a system instruction that it is a general assistant. Do not rely on the scrubber for secrets: keep private questions away from this agent.

## Options (examples fake; see `options.example.json`)
| Option | Meaning |
|---|---|
| `openrouter_key` | Your own OpenRouter API key. Sent in the `Authorization: Bearer` header only. Never in a URL, log or `/status`. |
| `register_key` | Same value as the parent's `register_key`. Empty = link off, nothing is accepted. |
| `parent_url` / `callback_url` | Parent (default gateway, port 8801) / how the parent reaches this agent (empty = gateway, port 8806). |
| `model` | OpenRouter model (default `openrouter/auto`). |
| `spend_cap` | Max PAID calls a day. Default 0 = free only: paid calls are refused with error `spend_cap`. Counted before the call. |
| `free_tier` | true = your plan costs nothing per call, so calls are free (spend_cap not needed). Default false. The agent cannot see real billing, so it treats every call as paid unless this is true (or the model name ends in `:free`). Stops by itself when the upstream credits run out (upstream error). |
| `daily_cap` | Max OpenRouter calls a day (default 50). Counted before the call. 0 = never call. |
| `timeout_s` | Timeout of the OpenRouter call (default 30). |
| `shadow` | true (default) = answers "[dry] ..." and calls nothing. Set false to go live. |
| `max_chars` | Replies are cut to this length (default 1900). |
| `log_payloads` | false (default) = each /ask logs ONE line with metadata only (id, caller, capability, model, chars in/out, ms, ok, why, free, shadow). true = the line also holds the first 200 chars of the request text and of the answer (one line, newlines escaped). Keys, tokens and headers are never logged. |

## Safety
Kill switch: file `/data/STOP`. `/status` shows use today versus cap and whether the key is set (not the key). Request text and reply text are logged only when `log_payloads` is true (200 chars each); by default the log has metadata only. No cache.

## After install
The owner sets boot to auto, watchdog on, auto update on. Starts with boot manual and shadow on. Version 0.1.0: not tested against the real OpenRouter API.
