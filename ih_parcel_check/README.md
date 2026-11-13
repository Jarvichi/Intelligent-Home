# ih_parcel_check
Local parcel-on-doorstep check. Dry by default (logs only). Ollama (pinned official release) is bundled; the moondream model (~1.8 GB) is pulled on first start into /data/ollama. If Ollama is down the picture is retried later and nothing is lost. Live posting is NOT built yet: add it only after the dry results are counted by hand.
Verdicts: `/data/verdicts.jsonl` (one JSON per picture: file, time, verdict yes/no/unsure, raw answer, seconds). Status at `:8798/status`.
