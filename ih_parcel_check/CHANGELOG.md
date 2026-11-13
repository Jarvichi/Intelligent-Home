# Changelog
## 0.1.1
- Port 8816 instead of 8798: 8798 is already used by ih_jobs_backup, so the add-on could not start ("port is already allocated").

## 0.1.0
- First version. Dry only: polls saved doorstep pictures, asks a local Ollama vision model "is there a parcel?", logs verdicts to /data/verdicts.jsonl. Posts nothing.
- Uses the Intelligent Home - Ollama add-on (ih_ollama) for the model; nothing is bundled. Waits until the engine answers and has the model.
