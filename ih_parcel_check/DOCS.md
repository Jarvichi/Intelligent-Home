# Intelligent Home - Parcel check

## What it does

Looks at saved doorstep pictures through the **Intelligent Home - AI Layer** (capability `vision`) and answers one question: is there a parcel on the doorstep? Version 0.2.0 is **dry only**: it writes each verdict (yes, no or unsure) to its own log and posts nothing. The AI layer chooses the provider: a local vision agent keeps the picture in the house; the built-in Gemini provider sends it to Google.

## Requires

- The **Intelligent Home - AI Layer** add-on, started, with a provider that declares `vision` (for example Gemini with a key and a `spend_cap` above 0) and an `api_token` for it.
- A folder of saved camera pictures that Home Assistant can read at `/media` (default `/media/eufy_snapshots`, files named `<camera>_<date>_<time>.jpg`).
- Home Assistant OS or Supervised (add-ons need the Supervisor).

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Parcel check**.
2. Start **Intelligent Home - AI Layer** with a vision provider, then set `api_token` here (the settings hub can fill it) and leave `dry` on.
3. Open `http://<home-assistant>:8816/status` to see how many pictures were checked and the last verdict. Every verdict is also saved in `/data/verdicts.jsonl`.
4. Check a few verdicts against the pictures. Turning on alerts is a later version.

## Options

| Option | Required? | What it is |
|---|---|---|
| `dry` | No | Default `true`. 0.1.0 is dry whatever this says. |
| `snapshot_dir` | No | Folder of saved pictures. Default `/media/eufy_snapshots`. |
| `cameras` | No | Comma list of camera names; only files starting with `<name>_` are checked. |
| `ai_layer_url` | No | Default `http://172.30.32.1:8801`. |
| `api_token` | Yes | The AI layer's API token. |
| `poll_seconds` | No | How often to look for new pictures (10 to 600). |
| `recheck_after_seconds` | No | A picture is checked only after it is this old, so the courier has left (0 to 900). |
| `timeout_seconds` | No | Longest wait for one answer (30 to 900). |

## Troubleshooting

- `/status` shows `ai_layer_ok: false`: the AI layer is stopped, the token is wrong or empty, or no vision agent is live; `last_error` says which. It retries every round.
