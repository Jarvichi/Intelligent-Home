# Intelligent Home - Ollama

## What it does

Runs [Ollama](https://ollama.com) (an official, pinned release) so that other Intelligent Home add-ons can use a local AI model for pictures, private text and search without sending anything off the home network. Other add-ons call it at `http://32c8114c-ih-ollama:11434`. The port is **not published** on your network, because Ollama has no login: only add-ons on this machine can reach it.

## Requires

- Home Assistant OS or Supervised (add-ons need the Supervisor).
- Disk: about 1.6 GB for Ollama plus the models (moondream is about 1.8 GB).
- Memory: a small model needs about 2 GB while it answers. On a Raspberry Pi with 8 GB, keep to one small model at a time.
- **Hardware: not recommended on a Raspberry Pi or other small board.** Running a model there is slow and can starve Home Assistant of memory. This add-on is for a Home Assistant machine with plenty of RAM (16 GB or more) and, ideally, a GPU. On small hardware, run Ollama on another machine and point the AI layer at it instead.

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Ollama**.
2. Leave `models` as it is (`moondream`, a small model that reads pictures) or list other model names from ollama.com/library. Start the add-on.
3. Read the log. It says `pulling <model>` while a model downloads and `models ready: ...` when done. The API answers while models download.
4. Install an add-on that uses it (for example **Intelligent Home - Parcel check**). Its `ollama_url` option already points here.

## Options

| Option | Required? | What it is |
|---|---|---|
| `models` | No | Models to download on start. Names are checked (letters, digits, `. : / - _`). Default `moondream`. |
| `keep_alive` | No | How long a model stays in memory after its last use, for example `30s` or `5m`. Short saves memory. Default `30s`. |
| `max_loaded_models` | No | Models in memory at once (1 to 3). Default 1. |
| `num_parallel` | No | Requests answered at once (1 to 4). Default 1. |

## Troubleshooting

- The first answer after a quiet period is slow while the model loads into memory.
- Watch memory on a small machine: if the system swaps heavily, use a smaller model or a shorter `keep_alive`.
- A model name that does not exist logs `pull of <name> failed` and is retried on the next start.
