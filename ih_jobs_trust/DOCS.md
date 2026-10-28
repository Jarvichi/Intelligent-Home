# Intelligent Home - Jobs - AI Trust Test

## What it does

Child of Intelligent Home - Jobs: monthly trust test of the outside AIs via the AI layer, adds a review to-do. Dry-run (shadow) by default. Child of `ih_jobs`.

## Requires

- `ih_jobs` add-on
- `ih_ai_layer` add-on
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Jobs - AI Trust Test**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `register_key`, `ai_layer_token` (skip any the settings hub fills).
4. Start the add-on and read the log. It starts in dry-run mode, so it changes nothing yet.
5. When the log looks right, set `mode` to `live`.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `log_payloads` | No | Off: logs one line of metadata per request. On: the line also holds the first 200 characters of the request and reply. Keys are never logged. Default: `false`. |
| `mode` | No | `dry` logs what it would do and changes nothing; `live` acts. Default: `dry`. |
| `old_loops_disabled` | No | Same as the other Jobs children. Default: `false`. |
| `parent_url` | No | Address of the parent add-on. Default: `http://172.30.32.1:8792`. |
| `callback_url` | No | How the parent reaches this add-on. Empty = the Supervisor gateway and this add-on's port. |
| `register_key` | Yes | Same as the other Jobs children |
| `own_timer_min` | No | Same as the other Jobs children. Default: `60`. |
| `ai_layer_url` | No | The AI layer (default: Supervisor gateway, port 8801) |
| `ai_layer_token` | Yes | The layer's `api_token` (secret) |
| `run_hour` | No | Local hour (0-23) from which the month's run may start (default 3) |
| `todo_entity` | No | To-do list for the review item (default `todo.claude_to_do`) |
| `pause_s` | No | Seconds between questions (default 4; the layer allows 30 a minute) |
| `keep_answers` | No | Store answer text in the run file (default off) |
| `timezone` | No | For the day-of-month check. Default: `Europe/London`. |

## Troubleshooting

- **Nothing happens, or replies start with `[dry]`.** The add-on starts safe because `mode` is `dry`. Switch it as in the last Set-up step once the log looks right.
- **Still dry after going live.** A file `/data/STOP` or `/data/STOP_LIVE` is a kill switch that forces dry mode. Delete it to re-enable.
- **Parent does not list this add-on / "cannot reach parent".** `register_key` must be identical in both add-ons, and `parent_url` must point at the parent (default gateway address and port). An empty key turns the link off.

## Privacy

Only fixed, non-private test questions are sent, through the AI layer to the outside AI services you enabled. Answers are not kept unless `keep_answers` is true.

## More details

### Shadow (dry) and live
Default `mode: dry` (shadow): it asks the layer, scores and records, but adds **no** to-do (logs "[DRY] would add to-do"). Live needs `mode: live`, `old_loops_disabled: true` (stop the tmux `trustmo` loop) and no `/data/STOP_LIVE`. If you run dry first and go live later in the same month, the to-do is added from the stored result without asking the AIs again. A `/data/STOP` file stops the job at once.

### Per-agent testing: needs a layer change (not made here)
The layer chooses the agent itself (best score for the capability) and accepts no way to name one. So this version tests whichever agent the layer picks for `general_qa`, and the run file lists the agents seen. To test every agent each month, the layer needs an optional request field **`prefer_agent`**. Small change in `addon_ai_layer/app/ailayer.py`, `request()`: read `pa = obj.get('prefer_agent')` (validate with `NAME_RE`; a bad value gives 400) and, after `order = self.ranked(cap)`, set `order = [pa] if pa in order else []` (no fallback to another agent, so the test measures that agent); when the list is empty return "No agent ...". Then this job would loop over `/agents` names that have `general_qa`.
