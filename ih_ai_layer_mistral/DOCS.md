# Intelligent Home - AI Layer - Mistral

## What it does

Agent of Intelligent Home - AI Layer: general questions and summaries via Mistral (text scrubbed first). Agent (child) of `ih_ai_layer`.

## Requires

- `ih_ai_layer` add-on
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - AI Layer - Mistral**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `mistral_key`, `register_key` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in shadow mode, so it changes nothing yet.
5. When the log looks right, set `shadow` to `false` to go live.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `mistral_key` | Yes | Your own Mistral API key. Sent in the `Authorization: Bearer` header only. Never in a URL, log or `/status`. |
| `register_key` | Yes | Same value as the parent's `register_key`. Empty = link off, nothing is accepted. |
| `parent_url` | No | Address of the parent add-on. Default: `http://172.30.32.1:8801`. |
| `callback_url` | No | How the parent reaches this add-on. Empty = the Supervisor gateway and this add-on's port. |
| `model` | No | Name of the model your agent uses (default `mistral-medium-latest`). Used for registration and model matching only; the agent decides the real model. |
| `agent_id` | No | Id of your Mistral agent (made in the Mistral console). |
| `spend_cap` | No | Max PAID calls a day. Default 0 = free only: paid calls are refused with error `spend_cap`. Counted before the call. |
| `free_tier` | No | Default true here (Mistral free tier). |
| `daily_cap` | No | Max Mistral calls a day (default 50). Counted before the call. 0 = never call. |
| `timeout_s` | No | Timeout of the Mistral call (default 30). |
| `shadow` | No | On (default): answers or logs only, posts nothing. Turn off to go live. |
| `max_chars` | No | Replies are cut to this length (default 1900). |
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `shadow` is on. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Parent does not list this add-on / "cannot reach parent".** `register_key` must be identical in both add-ons, and `parent_url` must point at the parent (default gateway address and port). An empty key turns the link off.
- **Error `spend_cap` or `daily_cap`.** Calls are counted before they are made. Raise the cap, or set `free_tier` to true if your plan is free. A missing or wrong API key also gives an upstream error: check `/status`, which shows whether a key is set (never the key).

## Privacy

The question text is sent to **Mistral** with your own key. Before sending, a scrubber removes email addresses, phone numbers, long digit strings, IP and MAC addresses, URL queries and token-like strings, and the request context is never sent. Do not rely on it for secrets.

## More details

### Quality
IQ score 54: easy generic jobs only (simple questions, short summaries). Do not rely on it for hard reasoning.

### Safety
Kill switch: file `/data/STOP`. `/status` shows use today versus cap and whether the key is set (not the key). Request text and reply text are logged only when `log_payloads` is true (200 chars each); by default the log has metadata only. No cache.
