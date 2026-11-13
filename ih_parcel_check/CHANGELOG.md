# Changelog
## 0.1.0
- First version. Dry only: polls saved doorstep pictures, asks a local Ollama vision model "is there a parcel?", logs verdicts to /data/verdicts.jsonl. Posts nothing.
- Uses the Intelligent Home - Ollama add-on (ih_ollama) for the model; nothing is bundled. Waits until the engine answers and has the model.
