# Intelligent Home - AI Layer - Gemini (ih_ai_layer_gemini)

An agent for **Intelligent Home - AI Layer** (`ih_ai_layer`). It registers with the parent with the capabilities `general_qa` and `summarize` (text in, text out) and answers the parent's signed `POST /ask` using Google Gemini (REST API, `generativelanguage.googleapis.com`).

## Privacy
This agent sends text to an OUTSIDE service. Before sending, a scrubber removes email addresses, phone numbers, long digit strings, IPv4 and MAC addresses, URL query strings/fragments and long token-like strings. The request `context` is never sent. The model gets a system instruction that it is a general assistant. Do not rely on the scrubber for secrets: keep private questions away from this agent.

## Options (examples fake; see `options.example.json`)
| Option | Meaning |
|---|---|
| `gemini_key` | Your own Gemini API key. Sent in the `x-goog-api-key` header only. Never in a URL, log or `/status`. |
| `register_key` | Same value as the parent's `register_key`. Empty = link off, nothing is accepted. |
| `parent_url` / `callback_url` | Parent (default gateway, port 8801) / how the parent reaches this agent (empty = gateway, port 8804). |
| `model` | Gemini model (default `gemini-2.5-flash`). |
| `spend_cap` | Max PAID calls a day. Default 0 = free only: paid calls are refused with error `spend_cap`. Counted before the call. |
| `free_tier` | true = your plan costs nothing per call, so calls are free (spend_cap not needed). Default false. The agent cannot see real billing, so it treats every call as paid unless this is true (or the model name ends in `:free`). Stops by itself when the upstream credits run out (upstream error). |
| `daily_cap` | Max Gemini calls a day (default 50). Counted before the call. 0 = never call. |
| `timeout_s` | Timeout of the Gemini call (default 30). |
| `shadow` | true (default) = answers "[dry] ..." and calls nothing. Set false to go live. |
| `max_chars` | Replies are cut to this length (default 1900). |

## Safety
Kill switch: file `/data/STOP`. `/status` shows use today versus cap and whether the key is set (not the key). Request text and reply text are never logged. No cache.

## After install
The owner sets boot to auto, watchdog on, auto update on. Starts with boot manual and shadow on. Version 0.1.0: not tested against the real Gemini API.
