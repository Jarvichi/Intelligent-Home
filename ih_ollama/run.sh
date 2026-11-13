#!/bin/sh
# Options are in /data/options.json. Models are pulled in the background so the API is up straight away.
eval "$(python3 /app/options_env.py)"
ollama serve >/data/ollama_serve.log 2>&1 &
SERVE=$!
i=0
until curl -sf http://127.0.0.1:11434/api/tags >/dev/null; do
  i=$((i+1)); [ "$i" -gt 60 ] && echo "ollama did not start" && break; sleep 2
done
(
  for m in $(python3 /app/options_env.py --models); do
    ollama list | grep -q "^$m" || { echo "pulling $m"; ollama pull "$m" || echo "pull of $m failed; will retry on next start"; }
  done
  echo "models ready: $(ollama list | tail -n +2 | awk '{print $1}' | tr '\n' ' ')"
) &
wait $SERVE
