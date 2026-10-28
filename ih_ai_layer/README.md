# Intelligent Home - AI Layer (ih_ai_layer)

Takes an **AI request** and routes it to an **AI agent add-on**, then returns the answer. It remembers which agent answers best for each kind of request.

**Discord is NOT part of this add-on.** It knows nothing about chats or channels. A separate "Discord AI bot" add-on (planned, not built) will turn chat messages into AI requests, call this API, and post the answers back.

## API (port 8801)
All except `/healthz` and `/register` need the header `Authorization: Bearer <api_token>`. With an empty `api_token` everything is refused.

| Call | Meaning |
|---|---|
| `POST /request` | Body `{text, capability (optional), context (optional object), model (optional string), request_id}`. With `model`: no classifier or scoring, the request goes only to the live agent that lists that model (agents register a `models` list), with no fallback to other agents; none = HTTP 404 `model_not_available`. Shadow mode, STOP, rate limit and auth apply as usual. If the agent refuses because of its `spend_cap`, the reply has `error: spend_cap`. Returns `{ok, answer, agent, capability, classified, took_ms, request_id}`. |
| `POST /feedback` | Body `{request_id, score}`; a score above 0 is a thumbs up, otherwise a thumbs down. Counts for the agent that answered that request (the last 500 requests are remembered). |
| `GET /agents` | Registered agents and their capabilities. |
| `GET /status` | Mode, counts, agents, score table. Never contains request text. |
| `GET /healthz` | Plain `ok`. |
| `POST /register` | Used by agent add-ons (signed, see below). |

If no `capability` is given, a simple keyword rule picks one and `classified` is `true`. If nothing matches, or no live agent has that capability, the answer says so ("No agent ..."). The best agent is tried first (score = thumbs, success rate and speed); on failure the next one is tried, up to `max_tries`.

## Safety
- **Shadow mode** (option `shadow`, on by default): agents are NOT called; the answer is a canned "[dry]" line naming the agent that would be asked.
- **Kill switch:** create the file `/data/STOP` and `/request` and `/feedback` return 503.
- **Rate limit:** `rate_per_min` requests a minute, then 429.
- **Privacy:** request text is never logged, stored or shown in `/status`. Logs hold only request id, capability, agent name, result and timing. The text IS sent to the chosen agent add-on (that is the job), so choose agents you trust.
- Agent links are signed (HMAC, 5 minute freshness) with the shared `register_key`. Empty `register_key` = nobody can register and no agent is called.

## How agents join
Set the same `register_key` here and in each agent add-on. Each agent posts a signed registration every 30 s with its name, URL and capabilities; an agent not seen for 2 minutes is not used. This parent calls the agent's signed `POST /ask`.

## Options (examples fake; see `options.example.json`)
`api_token`, `register_key`, `shadow`, `agent_timeout_s` (1-60), `rate_per_min`, `max_tries`.

## After install
The owner sets boot to auto, watchdog on, auto update on (the config file explains the watchdog line). Starts with boot manual.

## Status
Version 0.2.0: code and tests only; not installed, not build-tested. Scores live in `/data/scores.json`.
