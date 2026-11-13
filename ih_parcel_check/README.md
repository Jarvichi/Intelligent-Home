# ih_parcel_check
Local parcel-on-doorstep check. Dry by default (logs only). The AI engine is the separate Intelligent Home - Ollama add-on (install it first). If the engine is down the picture is retried later and nothing is lost. Live posting is NOT built yet: add it only after the dry results are counted by hand.
Verdicts: `/data/verdicts.jsonl` (one JSON per picture: file, time, verdict yes/no/unsure, raw answer, seconds). Status at `:8798/status`.
