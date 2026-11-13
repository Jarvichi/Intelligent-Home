# ih_parcel_check
Parcel-on-doorstep check. Dry by default (logs only). Each picture is sent to the Intelligent Home AI layer (`ih_ai_layer`, capability `vision`), which picks the agent. If the AI layer is down, has no vision agent, or a spend cap refuses, the picture is retried later and nothing is lost. Live posting is NOT built yet: add it only after the dry results are counted by hand.
Verdicts: `/data/verdicts.jsonl` (one JSON per picture: file, time, verdict yes/no/unsure, raw answer, seconds). Status at `:8816/status`.
