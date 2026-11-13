# Changelog
## 0.2.0
- Asks the Intelligent Home AI layer (capability `vision`) instead of the Ollama add-on; Ollama is no longer needed (it does not run on the Pi). Options `ollama_url` and `model` are replaced by `ai_layer_url` and `api_token`. Needs AI layer 0.6.0+ and a vision provider (Gemini). With an outside provider the picture leaves the house.

## 0.1.1
- Port 8816 instead of 8798: 8798 is already used by ih_jobs_backup, so the add-on could not start ("port is already allocated").

## 0.1.0
- First version. Dry only: polls saved doorstep pictures, asks a local Ollama vision model "is there a parcel?", logs verdicts to /data/verdicts.jsonl. Posts nothing.
- Uses the Intelligent Home - Ollama add-on (ih_ollama) for the model; nothing is bundled. Waits until the engine answers and has the model.
