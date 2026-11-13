# Intelligent Home - Parcel check

## What it does

Looks at saved doorstep pictures with a small local vision model (Ollama, model `moondream`) and answers one question: is there a parcel on the doorstep? Version 0.1.0 is **dry only**: it writes each verdict (yes, no or unsure) to its own log and posts nothing. Pictures never leave the home network. Ollama is bundled in this add-on (official release, pinned version and checksum); the model (about 1.8 GB) is downloaded once on first start.

## Requires

- A folder of saved camera pictures that Home Assistant can read at `/media` (default `/media/eufy_snapshots`, files named `<camera>_<date>_<time>.jpg`).
- About 3.5 GB of disk and, while a picture is being checked, about 2 GB of memory. Watch memory on a small machine (a Raspberry Pi 5 with 8 GB works but swaps for a minute on the first picture).
- Home Assistant OS or Supervised (add-ons need the Supervisor).

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Parcel check**.
2. Leave `dry` on. Start the add-on and read the log. The first start downloads the model, which takes a few minutes.
3. Open `http://<home-assistant>:8798/status` to see how many pictures were checked and the last verdict. Every verdict is also saved in `/data/verdicts.jsonl`.
4. Check a few verdicts against the pictures. Turning on alerts is a later version.

## Options

| Option | Required? | What it is |
|---|---|---|
| `dry` | No | Default `true`. 0.1.0 is dry whatever this says. |
| `snapshot_dir` | No | Folder of saved pictures. Default `/media/eufy_snapshots`. |
| `cameras` | No | Comma list of camera names; only files starting with `<name>_` are checked. |
| `ollama_url` | No | Default `http://127.0.0.1:11434` (the bundled Ollama). |
| `model` | No | Default `moondream`. |
| `poll_seconds` | No | How often to look for new pictures (10 to 600). |
| `recheck_after_seconds` | No | A picture is checked only after it is this old, so the courier has left (0 to 900). |
| `timeout_seconds` | No | Longest wait for one answer (30 to 900). |

## Troubleshooting

- `/status` shows `ollama_ok: false`: Ollama is still starting or the model is still downloading; it retries on the next round.
- Memory pressure on the first picture is normal on a Pi; later checks are faster.
