# Changelog

## [0.4.3] - 2026-11-27
- Add five themes with shared colours and visual details.

## [0.4.2] - 2026-11-27
- Apply the active Intelligent Home appearance across add-ons.

## 0.4.1
- Update the shared Intelligent Home visual theme and components.
## 0.4.0
- New read-only page in the Home Assistant sidebar ("IH · Parcel check", ingress port 8099, answers only the Supervisor gateway): mode (DRY or live), last check time, whether the AI layer answers, counts (checked, parcel, no parcel, unsure, errors) for today and 7 days, the last 20 verdicts (time, camera, verdict, answer text, which AI answered) and recent errors. No pictures and no picture file names are shown or served. Confidence is not recorded (the model answers yes/no), so the answer text is shown instead.
- New own database /data/ih.db (ihlib.db copy) with the verdicts and the last 50 errors. On the first start the old /data/verdicts.jsonl is imported; that file is kept and still written.
- The HTTP servers are now threaded (an idle connection no longer blocks a request). Port 8816 with /healthz and /status is unchanged.
## 0.3.0
- New option `checks`: a list of yes/no questions, each with `name`, `cameras` (comma list of picture prefixes), `question` and optional `yes_means`. Every picture is asked every matching check; verdicts are logged per check (new `check` field) and /status shows the latest verdict per check under `checks`. Empty list = the old single parcel check on `cameras`, so nothing changes on update. Old log lines count as check `parcel`. Still DRY: nothing is posted. Invalid or duplicate checks are skipped.

## 0.2.0
- Asks the Intelligent Home AI layer (capability `vision`) instead of the Ollama add-on; Ollama is no longer needed (it does not run on the Pi). Options `ollama_url` and `model` are replaced by `ai_layer_url` and `api_token`. Needs AI layer 0.6.0+ and a vision provider (Gemini). With an outside provider the picture leaves the house.

## 0.1.1
- Port 8816 instead of 8798: 8798 is already used by ih_jobs_backup, so the add-on could not start ("port is already allocated").

## 0.1.0
- First version. Dry only: polls saved doorstep pictures, asks a local Ollama vision model "is there a parcel?", logs verdicts to /data/verdicts.jsonl. Posts nothing.
- Uses the Intelligent Home - Ollama add-on (ih_ollama) for the model; nothing is bundled. Waits until the engine answers and has the model.
